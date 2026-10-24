"""日程是预期，事件是候选；外部观察只选择管理员登记的公开地址。"""
from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, ConfigDict, Field, model_validator
from ..information.evidence_models import EvidenceRef


class PlanningModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class ScheduleItem(PlanningModel):
    id: str = Field(min_length=1, max_length=80)
    kind: Literal['fixed', 'routine', 'flexible', 'exception'] = 'fixed'
    title: str = Field(min_length=1, max_length=200)
    starts_at: float | None = Field(default=None, ge=0, le=253402300799)
    ends_at: float | None = Field(default=None, ge=0, le=253402300799)
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    local_start: str = Field(default='09:00', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    local_end: str = Field(default='10:00', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    exception_date: str | None = Field(default=None, max_length=10)
    replaces_id: str | None = Field(default=None, max_length=80)
    cancelled: bool = False
    sources: list[EvidenceRef] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def bounds(self):
        if not self.title.strip():
            raise ValueError('日程名称不能为空。')
        if self.kind == 'routine':
            if not self.weekdays or any(day < 0 or day > 6 for day in self.weekdays) or len(set(self.weekdays)) != len(self.weekdays):
                raise ValueError('习惯需要不重复的星期 0—6（周一为 0）。')
            if self.local_start >= self.local_end or self.starts_at is not None or self.ends_at is not None:
                raise ValueError('习惯使用同日、本地开始／结束时间，跨午夜请拆为两项。')
        elif self.kind in {'fixed', 'flexible'} and (self.starts_at is None or self.ends_at is None or self.ends_at <= self.starts_at):
            raise ValueError('安排需要结束晚于开始的时间范围。')
        if self.kind == 'exception':
            try:
                if date.fromisoformat(self.exception_date or '').isoformat() != self.exception_date:
                    raise ValueError()
            except ValueError as exc:
                raise ValueError('日期例外需要 YYYY-MM-DD。') from exc
            if not self.replaces_id:
                raise ValueError('日期例外必须指向本计划中的原安排。')
            if not self.cancelled and (self.starts_at is None or self.ends_at is None or self.ends_at <= self.starts_at):
                raise ValueError('改期例外需要新的时间范围；取消可不填。')
        elif self.exception_date or self.replaces_id or self.cancelled:
            raise ValueError('只有日期例外可填写例外日期、替代目标或取消。')
        return self


class ScheduleUpdate(PlanningModel):
    revision: int = Field(ge=0)
    sources: list[EvidenceRef] = Field(default_factory=list, max_length=20)
    entries: list[ScheduleItem] = Field(default_factory=list, max_length=64)

    @model_validator(mode='after')
    def references(self):
        if len(self.model_dump_json().encode('utf-8')) > 256000:
            raise ValueError('近期日程合计超过 256000 字节。')
        ids = {item.id for item in self.entries}
        if len(ids) != len(self.entries):
            raise ValueError('日程编号不能重复。')
        by_id = {item.id: item for item in self.entries}
        exceptions = set()
        for item in self.entries:
            if item.kind == 'exception':
                target = by_id.get(item.replaces_id)
                key = (item.replaces_id, item.exception_date)
                if not target or target.kind == 'exception' or key in exceptions:
                    raise ValueError('例外必须引用本计划的普通安排，同日同目标只能有一个例外。')
                exceptions.add(key)
        return self


class ExtensionPolicy(PlanningModel):
    revision: int = Field(default=0, ge=0)
    schedules_enabled: bool = False
    events_enabled: bool = False
    observations_enabled: bool = False
    timezone: str = Field(default='Asia/Shanghai', max_length=80)
    max_events: int = Field(default=6, ge=1, le=16)
    event_daily_cap: int = Field(default=6, ge=1, le=24)
    observation_daily_cap: int = Field(default=6, ge=1, le=24)
    observation_sources: list['ObservationSource'] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def scope(self):
        try:
            ZoneInfo(self.timezone)
        except (ValueError, KeyError) as exc:
            raise ValueError('请填写有效 IANA 时区。') from exc
        if len({item.id for item in self.observation_sources}) != len(self.observation_sources):
            raise ValueError('公开来源编号不能重复。')
        return self


class ObservationSource(PlanningModel):
    id: str = Field(min_length=1, max_length=80, pattern=r'^[a-zA-Z0-9_-]+$')
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2000)


class WorldEventDraft(PlanningModel):
    summary: str = Field(min_length=1, max_length=1000)
    rationale: str = Field(min_length=1, max_length=500)
    due_at: float = Field(ge=0, le=253402300799)
    expires_at: float = Field(ge=0, le=253402300799)
    sources: list[EvidenceRef] = Field(default_factory=list, max_length=20)


class EventAction(PlanningModel):
    id: int = Field(ge=1)
    revision: int = Field(ge=1)
    action: Literal['adopt', 'discard', 'defer']
    reason: str = Field(min_length=1, max_length=500)
    due_at: float | None = Field(default=None, ge=0, le=253402300799)


class ObservationDraft(PlanningModel):
    source_id: str = Field(min_length=1, max_length=80)
    purpose: str = Field(min_length=1, max_length=500)
    due_at: float = Field(ge=0, le=253402300799)
    expires_at: float = Field(ge=0, le=253402300799)


ExtensionPolicy.model_rebuild()
