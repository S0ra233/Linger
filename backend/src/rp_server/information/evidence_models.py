"""不可由模型指定生活线的来源引用；编号只指向不可变的原始记录。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['input', 'sent', 'life', 'world_event', 'observation', 'current_life']
    id: int | None = Field(default=None, ge=1)
    revision: int = Field(default=1, ge=1, le=1)

    @model_validator(mode='after')
    def reference(self):
        if (self.kind == 'current_life') != (self.id is None):
            raise ValueError('current_life 不填编号；其他来源必须提供真实编号。')
        return self


class SceneCandidate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    location: str = Field(default='', max_length=200)
    activity: str = Field(default='', max_length=500)
    participants: list[str] = Field(default_factory=list, max_length=12)
    unfinished: list[str] = Field(default_factory=list, max_length=12)
    expires_at: float = Field(ge=0, le=253402300799)
    sources: list[EvidenceRef] = Field(min_length=1, max_length=20)

    @model_validator(mode='after')
    def content(self):
        if not any((self.location.strip(), self.activity.strip(), self.participants, self.unfinished)):
            raise ValueError('场景内容不能全空。')
        if any(not value.strip() or len(value) > 300 for value in [*self.participants, *self.unfinished]):
            raise ValueError('场景列表每项须为 1—300 字符。')
        return self


class EvidenceReadInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['input', 'sent', 'life', 'world_event', 'observation']
    id: int = Field(ge=1)


class EvidenceListInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['input', 'sent', 'life', 'world_event', 'observation']
    before: int | None = Field(default=None, ge=1)


class ConversationWindowInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['input', 'sent']
    id: int = Field(ge=1, le=9223372036854775807)
    before_turns: int = Field(default=2, ge=0, le=4)
    after_turns: int = Field(default=2, ge=0, le=4)
    token_budget: int = Field(default=4000, ge=1000, le=8000)


class EvidenceStatusInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    invalid: bool
    reason: str = Field(min_length=1, max_length=1000)
    revision: int = Field(ge=0)
