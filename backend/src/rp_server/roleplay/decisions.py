"""模型工具的结构化计划；模型不能指定作用域、文件或执行代码。"""
from typing import Literal
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator
from ..information.evidence_models import EvidenceRef, SceneCandidate
from ..information.adjustment_models import AdjustmentProposal
from ..information.models import MemoryVersionRef
from .planning_models import ScheduleUpdate, WorldEventDraft, EventAction, ObservationDraft


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class RuntimeSettings(StrictModel):
    enabled: bool = False
    concurrency: int = Field(default=2, ge=1, le=8)
    debounce_seconds: float = Field(default=2, ge=0, le=30)
    # 保留旧配置的读写兼容；3A 起由 ContextPolicy 管理完整轮次和摘要，不按此值裁剪历史。
    history_messages: int = Field(default=20, ge=0, le=100)
    # 3A 预设重构后仅保留旧配置兼容，实际字节上限取模型预设。
    context_bytes: int = Field(default=64000, ge=8000, le=512000)
    max_steps: int = Field(default=6, ge=2, le=12)
    max_tool_calls: int = Field(default=16, ge=1, le=32)
    run_timeout: int = Field(default=240, ge=10, le=900)
    receipt_timeout: int = Field(default=30, ge=5, le=120)
    delivery_grace: int = Field(default=300, ge=30, le=3600)
    schedule_hours: int = Field(default=168, ge=1, le=720)
    hide_html_comments: bool = True


class SettingsUpdate(RuntimeSettings):
    revision: int = Field(ge=0)


class MessagePart(StrictModel):
    type: Literal['text', 'asset']
    text: str = Field(default='', max_length=2000)
    asset_id: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}$')
    revision: int = Field(default=1, ge=1)

    @model_validator(mode='after')
    def combination(self):
        if self.type == 'text' and (not self.text.strip() or self.asset_id is not None):
            raise ValueError('文字片段需要非空 text，不能带素材编号。')
        if self.type == 'asset' and (not self.asset_id or self.text):
            raise ValueError('素材片段需要 asset_id 和 revision，不带 text。')
        return self


class Bubble(StrictModel):
    text: str = Field(default='', max_length=2000)
    parts: list[MessagePart] = Field(default_factory=list, max_length=8)
    delay_seconds: int = Field(default=0, ge=0, le=3600)
    send_at: float | str | None = None

    def absolute_time(self) -> float | None:
        if not isinstance(self.send_at, str):
            return self.send_at
        try:
            if len(self.send_at) > 64:
                raise ValueError()
            parsed = datetime.fromisoformat(self.send_at)
            if parsed.tzinfo is None:
                raise ValueError()
            return parsed.timestamp()
        except (ValueError, OverflowError) as exc:
            raise ValueError('send_at 字符串须为带时区的 ISO 8601 时间。') from exc

    @model_validator(mode='after')
    def combination(self):
        if (bool(self.text.strip()) == bool(self.parts)) or (self.parts and self.text) or self.send_at is not None and self.delay_seconds:
            raise ValueError('内容不能全空；send_at 与非零 delay_seconds 不得同时使用。')
        self.absolute_time()
        if sum(len(part.text) for part in self.parts) > 2000:
            raise ValueError('单个气泡文字合计不能超过 2000 字符。')
        return self


class Followup(Bubble):
    after_seconds: int = Field(ge=1, le=2592000)

    @model_validator(mode='after')
    def relative_only(self):
        if self.delay_seconds or self.send_at is not None:
            raise ValueError('后续分支只使用 after_seconds，不能混用普通气泡时间。')
        return self


class DeliveryChange(StrictModel):
    operation: Literal['add', 'update', 'keep', 'cancel']
    id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    revision: int = Field(default=0, ge=0)
    at: float | str | None = None
    text: str = Field(default='', max_length=2000)
    parts: list[MessagePart] = Field(default_factory=list, max_length=8)

    @model_validator(mode='after')
    def combination(self):
        if self.operation == 'add':
            if self.id is not None or self.revision:
                raise ValueError('新增投递不填写编号和修订。')
        elif self.id is None or self.revision < 1:
            raise ValueError('处理已有长期投递需要编号和当前修订。')
        if self.operation in {'add', 'update'}:
            if self.at is None:
                raise ValueError('新增或修改长期投递需要 at。')
            Bubble(text=self.text, parts=self.parts, send_at=self.at)
        elif self.at is not None or self.text or self.parts:
            raise ValueError('保留或取消只填写操作、编号和修订。')
        return self

    def timestamp(self):
        return Bubble(text=self.text, parts=self.parts, send_at=self.at).absolute_time()


class MemoryCandidate(StrictModel):
    kind: Literal['profile', 'episode']
    text: str = Field(min_length=1, max_length=1000)
    source_message_ids: list[int] = Field(default_factory=list, max_length=20)
    sources: list[EvidenceRef] = Field(default_factory=list, max_length=20)
    memory_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    revision: int = Field(default=0, ge=0)
    operation: Literal['upsert', 'forget'] = 'upsert'
    expires_at: float | None = Field(default=None, ge=0, le=253402300799)
    valid_from: float | None = Field(default=None, ge=0, le=253402300799)
    supersedes: list[MemoryVersionRef] = Field(default_factory=list, max_length=4)

    @model_validator(mode='after')
    def memory_bounds(self):
        if not self.source_message_ids and not self.sources:
            raise ValueError('记忆候选需要来源引用或兼容的来源输入编号。')
        if not self.text.strip() or len(set(self.source_message_ids)) != len(self.source_message_ids):
            raise ValueError('记忆内容不能为空，来源消息不能重复。')
        if self.memory_id is None and (self.revision or self.operation == 'forget'):
            raise ValueError('修改或遗忘需要已有记忆编号和修订。')
        if self.memory_id is not None and self.revision < 1:
            raise ValueError('已有记忆需要当前修订。')
        if self.expires_at is not None:
            raise ValueError('长期记忆不接收过期时间；临时记忆已退役。')
        if len({item.memory_id for item in self.supersedes}) != len(self.supersedes):
            raise ValueError('替代的旧记忆不能重复。')
        if self.operation == 'forget' and (self.supersedes or self.valid_from is not None):
            raise ValueError('遗忘不同时声明新事实时间或替代其他记忆。')
        return self


class RpDecision(StrictModel):
    reply_mode: Literal['reply', 'silent']
    read_state: bool = True
    messages: list[Bubble] = Field(default_factory=list, max_length=20,
        description='本轮即时回复气泡列表（常态1—2条，最多不超3条；能1条说完就只发1条，说完即止，不把动作、台词、停顿拆成多条凑数；较晚联系用followups或deliveries）。')
    followups: list[Followup] = Field(default_factory=list, max_length=20,
        description='用户无新输入时按序执行的短期后续。after_seconds相对本轮末泡确认送达时刻（silent时为输入时间），严格递增、非累加且遵守最小间隔。按停话原因与角色卡规划，已知返回时间优先于热聊参考；已解决的事不再追问，不宜打扰可留空[]。允许自然猜测问句但不当用户事实。新输入取消剩余项，跨聊天约定用deliveries。')
    deliveries: list[DeliveryChange] = Field(default_factory=list, max_length=16)
    life_fragment: str = Field(default='', max_length=4000)
    state_changes: dict[str, str] = Field(default_factory=dict, max_length=20)
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list, max_length=8)
    scene: SceneCandidate | None = None
    character_adjustment_proposal: AdjustmentProposal | None = None
    schedule_update: ScheduleUpdate | None = None
    event_candidates: list[WorldEventDraft] = Field(default_factory=list, max_length=3)
    event_actions: list[EventAction] = Field(default_factory=list, max_length=6)
    observation_requests: list[ObservationDraft] = Field(default_factory=list, max_length=2)

    @model_validator(mode='after')
    def combinations(self):
        if bool(self.messages) != (self.reply_mode == 'reply'):
            raise ValueError('reply 必须包含消息，silent 不得包含消息。')
        if sum(len(item.text) + sum(len(part.text) for part in item.parts) for item in [*self.messages, *self.followups, *self.deliveries]) > 12000:
            raise ValueError('单次计划的消息合计不能超过 12000 字符。')
        if any(not key.strip() or len(key) > 80 or len(value) > 1000 for key, value in self.state_changes.items()):
            raise ValueError('状态名称最多 80 字符，值最多 1000 字符。')
        return self

    def schedule(self, now: float, hours: int) -> list[float]:
        """相对延迟从上一气泡算起；绝对时间统一使用 Unix 秒。"""
        due, result = now, []
        for message in self.messages:
            candidate = message.absolute_time() if message.send_at is not None else due + message.delay_seconds
            if candidate < now - 5 or candidate > now + hours * 3600 or candidate < due - 5:
                raise ValueError('消息时间已过期、超过允许范围或顺序倒置。')
            due = max(due, candidate)
            result.append(due)
        return result
