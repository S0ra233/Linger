"""逐步明确管理端读取契约；原始记录保留扩展字段，诊断字段使用明确类型。"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from ..roleplay.run_diagnostics import RunDiagnostics


class Record(BaseModel):
    model_config = ConfigDict(extra='allow')


class RunRecord(Record):
    id: int
    instance_id: str
    input_version: int
    input_through: int
    status: str
    error: str | None
    created_at: float
    finished_at: float | None
    model_json: str | None
    settings_json: str
    decision_json: str | None
    context_json: str | None = None
    trigger_kind: Literal['input', 'life'] = 'input'


class StepRecord(Record):
    id: int
    step: int
    status: str
    request_json: str
    response_json: str | None
    tools_json: str | None
    created_at: float
    finished_at: float | None


class RunDetailResponse(BaseModel):
    run: RunRecord
    steps: list[StepRecord]
    # 原解释内含历史兼容投影，本批先固定外层与新增诊断，不重写旧记录。
    explanation: dict[str, Any]
    diagnostics: RunDiagnostics


class MemoryRecord(Record):
    id: str
    instance_id: str
    kind: Literal['profile', 'temporary', 'episode']
    text: str
    status: Literal['active', 'forgotten']
    revision: int
    expires_at: float | None
    updated_at: float
    locked: int | bool
    protection_revision: int
    confirmed_at: float | None
    valid_from: float | None
    superseded_by: str | None
    superseded_at: float | None


class MemoryProposalRecord(Record):
    id: str
    instance_id: str
    memory_id: str | None
    revision: int
    run_id: int
    proposal_json: str
    status: Literal['pending', 'applied', 'rejected']
    created_at: float
    reviewed_at: float | None
    targets: list[MemoryRecord]
    effective_status: Literal['pending', 'applied', 'rejected', 'stale']
    stale_reason: str | None = None


class MemoryProposalPageResponse(BaseModel):
    items: list[MemoryProposalRecord]
    next_before: str | None
    pending_count: int
    pending_limit: int


class ContextPreviewReport(Record):
    """明确公共预算字段；不同上下文策略的诊断扩展保留原样。"""
    input_bytes: int
    byte_limit: int
    estimated_input_tokens: int
    window: int | None
    output_reserve: int
    safety_margin: int
    available_input_tokens: int | None
    over_budget: bool
    fixed_over_budget: bool
    would_summarize: bool
    can_summarize: bool
    pending_inputs: int
    historical_messages: int
    summary_id: int | None
    prefix_hash: str
    worldbooks: dict[str, Any]


class PreviewText(Record):
    type: Literal['text']
    text: str


class PreviewImage(Record):
    type: Literal['image']
    attachment_id: str


class PreviewMessage(Record):
    role: Literal['system', 'user', 'assistant']
    content: list[PreviewText | PreviewImage]
    context_layer: str | None = None


class ContextPreviewResponse(BaseModel):
    report: ContextPreviewReport
    messages: list[PreviewMessage]
