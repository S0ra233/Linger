"""非敏感配置带修订号保存，防止多个页面静默覆盖。"""
import json
from time import time

from pydantic import BaseModel, ConfigDict, Field, field_validator
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .database import Database


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    site_name: str = Field(default="RP 管理台", min_length=1, max_length=40)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=80)

    @field_validator("site_name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("管理台名称不能为空。")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("请填写有效的 IANA 时区，例如 Asia/Shanghai。") from exc
        return value


class PreferencesUpdate(Preferences):
    revision: int = Field(ge=0)


class RevisionConflict(ValueError):
    pass


class Configuration:
    def __init__(self, database: Database):
        self.database = database

    def preferences(self) -> dict:
        row = self.database.one("SELECT value_json,revision,updated_at FROM configurations WHERE key='preferences'")
        if row is None:
            return {**Preferences().model_dump(), "revision": 0, "updated_at": None}
        return {**Preferences.model_validate_json(row["value_json"]).model_dump(),
                "revision": row["revision"], "updated_at": row["updated_at"]}

    def save_preferences(self, update: PreferencesUpdate) -> dict:
        value = Preferences(site_name=update.site_name, timezone=update.timezone).model_dump_json()
        now = time()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT revision FROM configurations WHERE key='preferences'").fetchone()
            current = row["revision"] if row else 0
            if current != update.revision:
                raise RevisionConflict("配置已被另一页面更新，请重新载入后再保存。")
            connection.execute("""INSERT INTO configurations(key,value_json,revision,updated_at)
                VALUES ('preferences',?,?,?) ON CONFLICT(key) DO UPDATE SET
                value_json=excluded.value_json,revision=excluded.revision,updated_at=excluded.updated_at""",
                               (value, current + 1, now))
            connection.execute("INSERT INTO audit_events(created_at,action,details_json) VALUES (?,?,?)",
                               (now, "preferences.updated", json.dumps({"revision": current + 1})))
        return {**Preferences.model_validate_json(value).model_dump(), "revision": current + 1, "updated_at": now}
