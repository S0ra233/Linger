"""主密钥与数据库分开管理；数据库、接口和日志只传凭据引用。"""
import os
from time import time
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from ..settings import ServerSettings
from .database import Database


KEY_CHECK = b"rp-master-key-check-v1"


def load_master_key(settings: ServerSettings) -> bytes:
    value = os.environ.get("RP_MASTER_KEY")
    if value:
        return value.encode("ascii")
    try:
        return settings.master_key_file.read_bytes().strip()
    except FileNotFoundError as exc:
        raise ValueError("服务器主密钥缺失；请恢复原密钥，不要生成新密钥覆盖已有数据。") from exc


class Credentials:
    def __init__(self, database: Database, settings: ServerSettings):
        self.database = database
        try:
            self._cipher = Fernet(load_master_key(settings))
        except (ValueError, UnicodeError) as exc:
            raise ValueError("服务器主密钥格式无效。") from exc

    def check_key(self, *, initialize: bool = False):
        row = self.database.one("SELECT value FROM metadata WHERE key='master_key_check'")
        if row is None:
            if not initialize:
                raise ValueError("主密钥校验资料缺失；请检查初始化或恢复是否完整。")
            with self.database.transaction() as connection:
                connection.execute("INSERT INTO metadata(key,value) VALUES ('master_key_check',?)",
                                   (self._cipher.encrypt(KEY_CHECK).decode("ascii"),))
            return
        try:
            if self._cipher.decrypt(row["value"].encode("ascii")) != KEY_CHECK:
                raise InvalidToken
        except InvalidToken as exc:
            raise ValueError("主密钥与数据库不匹配，请恢复原密钥。") from exc

    def encrypt(self, plaintext: str) -> bytes:
        """先准备密文，调用方可与自己的配置变更在同一事务提交。"""
        if not plaintext or len(plaintext.encode("utf-8")) > 32768:
            raise ValueError("凭据不能为空或超过 32 KiB。")
        return self._cipher.encrypt(plaintext.encode("utf-8"))

    def save(self, plaintext: str, reference: str | None = None) -> str:
        reference = reference or uuid4().hex
        ciphertext = self.encrypt(plaintext)
        now = time()
        with self.database.transaction() as connection:
            connection.execute("""INSERT INTO encrypted_secrets(id,ciphertext,created_at,updated_at)
                VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                ciphertext=excluded.ciphertext, updated_at=excluded.updated_at""",
                               (reference, ciphertext, now, now))
        return reference

    def read(self, reference: str) -> str:
        row = self.database.one("SELECT ciphertext FROM encrypted_secrets WHERE id=?", (reference,))
        if row is None:
            raise ValueError("凭据引用不存在，请重新保存对应连接的密钥。")
        try:
            return self._cipher.decrypt(row["ciphertext"]).decode("utf-8")
        except (InvalidToken, UnicodeError) as exc:
            raise ValueError("凭据无法解密，请检查主密钥与恢复数据。") from exc
