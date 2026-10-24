"""QQ 接入配置；连接令牌独立于管理员和模型凭据。"""
import hmac
import json
from time import time
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..events import EventBus
from ..roleplay.inputs import qq_id
from ..roleplay.store import invalidate
from ..storage.configuration import RevisionConflict
from ..storage.credentials import Credentials
from ..storage.database import Database


class AccountInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str = Field(min_length=1, max_length=80)
    bot_id: str
    enabled: bool = True
    token: str | None = Field(default=None, min_length=24, max_length=256, repr=False)
    revision: int = Field(default=0, ge=0)

    @field_validator('bot_id')
    @classmethod
    def validate_id(cls, value: str):
        return qq_id(value)

    @field_validator('token')
    @classmethod
    def validate_token(cls, value: str | None):
        if value is not None and (not value.isascii() or any(char.isspace() or ord(char) < 33 or ord(char) > 126 for char in value)):
            raise ValueError('连接令牌须为不含空格的 ASCII 字符。')
        return value


class QQAccounts:
    def __init__(self, database: Database, credentials: Credentials, events: EventBus, public_origin: str):
        self.database, self.credentials, self.events = database, credentials, events
        self.ws_origin = public_origin.replace('https://', 'wss://', 1).replace('http://', 'ws://', 1)

    def list(self):
        return [{**row, 'enabled': bool(row['enabled']), 'token_configured': True,
                 'websocket_url': f"{self.ws_origin}/onebot/v11/{row['id']}"}
                for row in self.database.all('''SELECT id,bot_id,name,enabled,revision,created_at,updated_at
                    FROM qq_accounts ORDER BY created_at''')]

    def save(self, body: AccountInput, account_id: str | None = None):
        if not body.name.strip():
            raise ValueError('账号名称不能为空。')
        if account_id is None and body.token is None:
            raise ValueError('新账号必须设置独立连接令牌，至少 24 个字符。')
        encrypted = self.credentials.encrypt(body.token) if body.token is not None else None
        now, target = time(), account_id or uuid4().hex
        with self.database.transaction() as tx:
            row = tx.execute('SELECT * FROM qq_accounts WHERE id=?', (target,)).fetchone()
            if account_id and row is None:
                raise LookupError('QQ 账号不存在。')
            if (row['revision'] if row else 0) != body.revision:
                raise RevisionConflict('QQ 账号已变化，请刷新后重试。')
            if row and row['bot_id'] != body.bot_id:
                raise ValueError('已有账号的 QQ 号不能修改，请另建接入账号。')
            if not row:
                if tx.execute('SELECT 1 FROM qq_accounts WHERE bot_id=?', (body.bot_id,)).fetchone():
                    raise ValueError('这个机器人 QQ 号已有接入配置。')
                if tx.execute('SELECT count(*) FROM qq_accounts').fetchone()[0] >= 20:
                    raise ValueError('QQ 接入账号最多 20 个。')
            reference = row['credential_ref'] if row else uuid4().hex
            if encrypted is not None:
                # 原引用内更新，账号与令牌在同一事务发布。
                tx.execute('''INSERT INTO encrypted_secrets VALUES (?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET ciphertext=excluded.ciphertext,updated_at=excluded.updated_at''',
                           (reference, encrypted, now, now))
            if row:
                for instance in tx.execute('SELECT id FROM role_instances WHERE account_id=?', (target,)).fetchall():
                    invalidate(tx, instance['id'], 'QQ 账号配置已变化。')
                tx.execute('UPDATE qq_accounts SET name=?,enabled=?,revision=revision+1,updated_at=? WHERE id=?',
                           (body.name.strip(), int(body.enabled), now, target))
            else:
                tx.execute('INSERT INTO qq_accounts VALUES (?,?,?,?,?,1,?,?)',
                           (target, body.bot_id, body.name.strip(), int(body.enabled), reference, now, now))
            tx.execute('INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)',
                       (now, 'qq.account_saved', json.dumps({'id': target, 'revision': body.revision + 1,
                                                          'enabled': body.enabled, 'token_changed': encrypted is not None})))
        self.events.publish('qq')
        return next(item for item in self.list() if item['id'] == target)

    def authorization_error(self, account_id: str, bot_id: str, authorization: str) -> str | None:
        """只返回固定诊断代码；不把令牌、密文或请求头带入日志和管理接口。"""
        row = self.database.one('SELECT * FROM qq_accounts WHERE id=?', (account_id,))
        if row is None:
            return 'account_not_found'
        if not row['enabled']:
            return 'account_disabled'
        if row['bot_id'] != bot_id:
            return 'self_id_mismatch'
        if not authorization:
            return 'authorization_missing'
        parts = authorization.split()
        if len(parts) != 2 or parts[0].lower() != 'bearer':
            return 'authorization_format'
        supplied = parts[1]
        if not 24 <= len(supplied) <= 256 or not supplied.isascii():
            return 'authorization_format'
        try:
            expected = self.credentials.read(row['credential_ref'])
        except ValueError:
            return 'credential_unavailable'
        return None if hmac.compare_digest(supplied, expected) else 'token_mismatch'

    def authorize(self, account_id: str, bot_id: str, authorization: str) -> bool:
        return self.authorization_error(account_id, bot_id, authorization) is None
