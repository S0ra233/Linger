"""模型只能提出有来源的局部设定修改，管理员决定是否采用。"""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .evidence_models import EvidenceRef


LIMITS = {'description': 24000, 'personality': 16000, 'scenario': 16000,
          'style': 4000, 'dialogue_examples': 24000}


class AdjustmentProposal(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    reason: str = Field(min_length=1, max_length=2000)
    changes: dict[str, str] = Field(min_length=1, max_length=5)
    sources: list[EvidenceRef] = Field(min_length=2, max_length=20)

    @model_validator(mode='after')
    def bounds(self):
        if not self.reason.strip():
            raise ValueError('调整理由不能为空。')
        if any(key not in LIMITS or len(value) > LIMITS[key] for key, value in self.changes.items()):
            raise ValueError('只能调整描述、性格、场景、表达风格和示例，且不能超过字段预算。')
        if any(ref.kind != 'input' for ref in self.sources) or len({ref.id for ref in self.sources}) < 2:
            raise ValueError('长期反馈调整至少需要两条不同的用户输入依据。')
        return self


class ProposalReview(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: int = Field(ge=1)
    action: str = Field(pattern=r'^(adopt|reject)$')


class AdjustmentRollback(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: int = Field(ge=0)
    target_revision: int = Field(ge=0)
