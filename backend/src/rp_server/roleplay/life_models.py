"""后台生活的限制和结构化意图；只保存计划，不接受脚本或收件人。"""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import Field, model_validator
from .decisions import StrictModel


class LifeSettings(StrictModel):
    enabled: bool = False
    followup_min_gap: int = Field(default=1, ge=1, le=3600)
    global_runs: int = Field(default=100, ge=0, le=10000)
    instance_runs: int = Field(default=24, ge=0, le=1000)
    max_unanswered: int = Field(default=2, ge=0, le=20)
    grace_seconds: int = Field(default=900, ge=30, le=3600)
    quiet_enabled: bool = True
    quiet_start: str = Field(default='23:00', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    quiet_end: str = Field(default='08:00', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    timezone: str = Field(default='Asia/Shanghai', max_length=80)

    @model_validator(mode='after')
    def bounds(self):
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError('请选择有效的 IANA 时区。') from exc
        if self.quiet_enabled and self.quiet_start == self.quiet_end:
            raise ValueError('安静时段开始与结束不能相同；禁止主动联系可将连续未回复上限设为 0。')
        return self


class LifeSettingsInput(LifeSettings):
    revision: int = Field(ge=0)


class LifePolicyInput(StrictModel):
    enabled: bool = False
    revision: int = Field(ge=0)


class LifeReadInput(StrictModel):
    before: int | None = Field(default=None, ge=1)
    limit: int = Field(default=3, ge=1, le=4)

