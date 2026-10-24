"""资料管理输入和模型运行候选使用不同入口，作用域不由模型填写。"""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .evidence_models import EvidenceRef


class StrictInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class MemoryVersionRef(StrictInput):
    memory_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    revision: int = Field(ge=1)


class MemoryInput(StrictInput):
    replace_sources: bool = False
    kind: Literal['profile', 'temporary', 'episode']
    text: str = Field(min_length=1, max_length=1000)
    expires_at: float | None = Field(default=None, ge=0, le=253402300799)
    status: Literal['active', 'forgotten'] = 'active'
    source_message_ids: list[int] = Field(default_factory=list, max_length=20)
    sources: list[EvidenceRef] = Field(default_factory=list, max_length=20)
    revision: int = Field(default=0, ge=0)
    valid_from: float | None = Field(default=None, ge=0, le=253402300799)
    supersedes: list[MemoryVersionRef] = Field(default_factory=list, max_length=4)

    @model_validator(mode='after')
    def bounds(self):
        if any(ref.kind == 'current_life' for ref in self.sources):
            raise ValueError('管理员来源必须是已经保存的记录。')
        if not self.text.strip() or len(set(self.source_message_ids)) != len(self.source_message_ids):
            raise ValueError('内容不能为空，来源不能重复。')
        if self.kind == 'temporary' and self.status == 'active' and self.expires_at is None:
            raise ValueError('临时记忆需要过期时间。')
        if self.kind != 'temporary' and self.expires_at is not None:
            raise ValueError('只有临时记忆可设置过期时间。')
        self.text = self.text.strip()
        if len({item.memory_id for item in self.supersedes}) != len(self.supersedes):
            raise ValueError('替代的旧记忆不能重复。')
        if self.supersedes and self.status != 'active':
            raise ValueError('只有有效的新事实可以替代旧记忆。')
        return self


class MemoryProtectionInput(StrictInput):
    revision: int = Field(ge=1)
    protection_revision: int = Field(ge=0)
    locked: bool
    promote_to_profile: bool = False


class MemoryKindsInput(StrictInput):
    kinds: list[Literal['profile', 'temporary', 'episode']] = Field(min_length=1, max_length=3)

    @model_validator(mode='after')
    def unique_kinds(self):
        if len(set(self.kinds)) != len(self.kinds):
            raise ValueError('记忆类型不能重复。')
        return self


class MemoryBatchForget(MemoryKindsInput):
    token: str = Field(pattern=r'^[a-f0-9]{64}$')


class MemoryProposalReview(StrictInput):
    revision: int = Field(ge=1)
    action: Literal['approve', 'reject']


class LibraryInput(StrictInput):
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    revision: int = Field(default=0, ge=0)


class DocumentInput(StrictInput):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=250000)
    enabled: bool = True
    revision: int = Field(default=0, ge=0)


class SearchInput(StrictInput):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=6, ge=1, le=12)
    category: Literal['all', 'memory', 'knowledge'] = 'all'


class ReadInput(StrictInput):
    chunk_id: str = Field(pattern=r'^[0-9a-f]{64}$')


class RevisionInput(StrictInput):
    revision: int = Field(ge=1)


class AdminSearchInput(SearchInput):
    mode: Literal['keyword', 'semantic', 'hybrid'] = 'keyword'


class IndexInput(StrictInput):
    space_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    connection_revision: int = Field(ge=1)
    scope_kind: Literal['library', 'memory']
    scope_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    max_chunks: int = Field(default=200, ge=1, le=5000)
    batch_size: int = Field(default=4, ge=1, le=8)
