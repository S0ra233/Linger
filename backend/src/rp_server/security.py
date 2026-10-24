"""单管理员鉴权；密码单向校验，会话令牌仅保存摘要。"""
import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
import hashlib
import hmac
import json
import secrets
from time import time, monotonic
from uuid import uuid4

from .settings import ServerSettings
from .storage.database import Database


def validate_password(password: str):
    if not 12 <= len(password) <= 128:
        raise ValueError("密码需要 12—128 个字符。")


def hash_password(password: str) -> str:
    validate_password(password)
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=64)
    return f"scrypt$16384$8$1${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not isinstance(password, str) or not 1 <= len(password) <= 128:
        return False
    try:
        algorithm, n, r, p, salt, expected = stored.split("$")
        if (algorithm, n, r, p) != ("scrypt", "16384", "8", "1"):
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=64)
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csrf_token(token: str) -> str:
    """由不可读 Cookie 的随机令牌派生，刷新页面后可取回且不暴露会话令牌。"""
    return hashlib.sha256(("rp-csrf-v1:" + token).encode("utf-8")).hexdigest()


class AuthenticationError(ValueError):
    pass


class TooManyAttempts(ValueError):
    pass


@dataclass(frozen=True)
class Session:
    id: str
    username: str
    csrf_hash: str
    expires_at: float


class AuthService:
    def __init__(self, database: Database, settings: ServerSettings):
        self.database = database
        self.settings = settings
        self._attempts: dict[str, deque] = defaultdict(deque)
        self._global_attempts = deque()
        # 限制 scrypt 的并行内存开销，不在数据库事务中计算密码。
        self._password_slots = asyncio.Semaphore(2)

    def _throttle(self, peer: str):
        now = monotonic()
        if peer not in self._attempts and len(self._attempts) >= 1024:
            oldest = min(self._attempts, key=lambda key: self._attempts[key][-1] if self._attempts[key] else 0)
            del self._attempts[oldest]
        recent = self._attempts[peer]
        for attempts in (recent, self._global_attempts):
            while attempts and attempts[0] < now - 60:
                attempts.popleft()
        if len(recent) >= 5 or len(self._global_attempts) >= 30:
            raise TooManyAttempts("尝试次数过多，请稍后再试。")
        recent.append(now)
        self._global_attempts.append(now)

    async def login(self, username: str, password: str, peer: str, user_agent: str) -> tuple[dict, str, str]:
        self._throttle(peer)
        administrator = self.database.one("SELECT * FROM administrator WHERE id=1")
        if administrator is None:
            raise AuthenticationError("管理员尚未初始化。")
        async with self._password_slots:
            valid = await asyncio.to_thread(verify_password, password, administrator["password_hash"])
        if not valid or not hmac.compare_digest(username.encode("utf-8"), administrator["username"].encode("utf-8")):
            self.audit("auth.login_failed", {"peer": peer})
            raise AuthenticationError("用户名或密码错误。")
        token = secrets.token_urlsafe(32)
        csrf = csrf_token(token)
        now = time()
        session_id = uuid4().hex
        with self.database.transaction() as connection:
            # 密码校验期间可能已经换密码；不能使用过期身份创建新会话。
            current = connection.execute("SELECT password_hash FROM administrator WHERE id=1").fetchone()
            if current["password_hash"] != administrator["password_hash"]:
                raise AuthenticationError("密码已更新，请重新登录。")
            connection.execute("DELETE FROM login_sessions WHERE expires_at<=? OR last_seen_at<=?",
                               (now, now - self.settings.session_idle_seconds))
            connection.execute("""DELETE FROM login_sessions WHERE id IN
                (SELECT id FROM login_sessions ORDER BY created_at DESC LIMIT -1 OFFSET 15)""")
            connection.execute("INSERT INTO login_sessions VALUES (?,?,?,?,?,?,?,?)",
                               (session_id, token_hash(token), token_hash(csrf), now, now,
                                now + self.settings.session_seconds, peer[:100], user_agent[:300]))
            connection.execute("INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)",
                               (now, "auth.login", json.dumps({"session_id": session_id, "peer": peer})))
        self._attempts.pop(peer, None)
        return {"id": session_id, "username": administrator["username"],
                "expires_at": now + self.settings.session_seconds}, token, csrf

    def authenticate(self, token: str | None, *, touch: bool = True) -> Session:
        if not token or len(token) > 256:
            raise AuthenticationError("请先登录。")
        row = self.database.one("""SELECT s.*,a.username FROM login_sessions s
            CROSS JOIN administrator a WHERE s.token_hash=? AND a.id=1""", (token_hash(token),))
        now = time()
        if (row is None or row["expires_at"] <= now
                or row["last_seen_at"] + self.settings.session_idle_seconds <= now):
            raise AuthenticationError("登录已过期，请重新登录。")
        if touch and now - row["last_seen_at"] >= 60:
            with self.database.transaction() as connection:
                connection.execute("UPDATE login_sessions SET last_seen_at=? WHERE id=?", (now, row["id"]))
        return Session(row["id"], row["username"], row["csrf_hash"], row["expires_at"])

    def csrf(self, session: Session, value: str | None):
        if not value or len(value) > 256 or not hmac.compare_digest(token_hash(value), session.csrf_hash):
            raise AuthenticationError("页面安全令牌已失效，请刷新页面。")

    def audit(self, action: str, details: dict):
        with self.database.transaction() as connection:
            connection.execute("INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)",
                               (time(), action, json.dumps(details, ensure_ascii=False)))

    def logout(self, session: Session):
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM login_sessions WHERE id=?", (session.id,))
            connection.execute("INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)",
                               (time(), "auth.logout", json.dumps({"session_id": session.id})))

    def revoke_others(self, session: Session) -> int:
        with self.database.transaction() as connection:
            count = connection.execute("DELETE FROM login_sessions WHERE id<>?", (session.id,)).rowcount
            connection.execute("INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)",
                               (time(), "auth.sessions_revoked", json.dumps({"count": count})))
        return count

    async def change_password(self, session: Session, current_password: str, new_password: str):
        self._throttle("password:" + session.id)
        validate_password(new_password)
        if new_password == current_password:
            raise ValueError("新密码需要与当前密码不同。")
        administrator = self.database.one("SELECT * FROM administrator WHERE id=1")
        async with self._password_slots:
            if not await asyncio.to_thread(verify_password, current_password, administrator["password_hash"]):
                raise AuthenticationError("当前密码错误。")
            candidate = await asyncio.to_thread(hash_password, new_password)
        with self.database.transaction() as connection:
            # 并发修改只允许基于同一份旧密码快照完成一次。
            changed = connection.execute("""UPDATE administrator SET password_hash=?,password_changed_at=?
                WHERE id=1 AND password_hash=?""", (candidate, time(), administrator["password_hash"])).rowcount
            if not changed:
                raise AuthenticationError("密码已在另一页面更新，请重新登录。")
            connection.execute("DELETE FROM login_sessions")
            connection.execute("INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)",
                               (time(), "auth.password_changed", "{}"))
