"""预设的数据契约；不依赖数据库、连接、凭据或 RP 运行对象。"""
from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Any, Literal

from .default_prompts import ROLE_PROMPT, DIALOGUE_PROMPT, CONTACT_PROMPT, IMMERSION_BLOCK_ID, IMMERSION_BLOCK_NAME, IMMERSION_PROMPT, DEFAULT_TOOL_SUBMISSION_HINT


class PromptBlock(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(default='', max_length=200)
    role: Literal['system', 'user', 'assistant'] = 'system'
    position: Literal['before_character', 'after_character', 'after_history'] = 'before_character'
    content: str = Field(default='', max_length=16000)
    enabled: bool = True
    # 空列表沿用旧版本：在所有业务运行中使用，不包括独立的缓存维护。
    run_types: list[Literal['input', 'life', 'summary']] = Field(default_factory=list, max_length=3)

    @model_validator(mode='after')
    def unique_run_types(self):
        if len(set(self.run_types)) != len(self.run_types):
            raise ValueError('提示词适用运行类型不能重复。')
        return self

    def selection_reason(self, run_type: str) -> str:
        if not self.enabled:
            return 'disabled'
        if not self.content.strip():
            return 'empty'
        if self.run_types and run_type not in self.run_types:
            return 'run_type'
        return 'selected'


class ContextPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    # 缺少字段的旧版本保持原行为；新建入口显式使用 new_preset_content。
    strategy: Literal['legacy', 'cost', 'continuity'] = 'legacy'
    history_trigger_turns: int = Field(default=100, ge=41, le=2000)
    history_trigger_tokens: int | None = Field(default=None, ge=2048, le=8000000)
    history_target_tokens: int | None = Field(default=None, ge=1024, le=8000000)
    # 旧版字段只兼容读取与导出，不再触发首次／周期自动检索。
    retrieval_enabled: bool = True
    allow_information_tools: bool = True
    retain_retrieval_snapshot: bool = True
    retrieval_refresh_turns: int = Field(default=20, ge=1, le=2000)
    retrieval_budget_tokens: int = Field(default=8192, ge=0, le=64000)
    retrieval_target_tokens: int = Field(default=4096, ge=0, le=64000)
    trim_on_material_change: bool = True
    # 逐轮生活描写不进入历史前缀；开启时仅低频保存结构化状态快照。
    state_injection: Literal['off', 'snapshot'] = 'off'
    state_refresh_turns: int = Field(default=20, ge=1, le=2000)
    context_window: int | None = Field(default=None, ge=2048, le=10000000)
    output_reserve: int = Field(default=4096, ge=1, le=1000000)
    safety_margin: int = Field(default=1024, ge=0, le=64000)
    trigger_ratio: float = Field(default=0.8, ge=0.5, le=0.95)
    retain_turns: int = Field(default=10, ge=1, le=1000)
    worldbook_budget_tokens: int = Field(default=4096, ge=0, le=32000)
    memory_budget_tokens: int = Field(default=2048, ge=0, le=16000)
    knowledge_budget_tokens: int = Field(default=2048, ge=0, le=16000)
    retrieval_mode: Literal['keyword', 'semantic', 'hybrid'] = 'keyword'
    retrieval_top_k: int = Field(default=6, ge=1, le=12)
    auto_summary: bool = False
    summary_target_tokens: int = Field(default=1500, ge=128, le=8000)
    summary_max_tokens: int = Field(default=4096, ge=256, le=16000)
    summary_timeout: int = Field(default=90, ge=10, le=300)
    summary_max_batches: int = Field(default=16, ge=1, le=64)
    pending_input_limit: int = Field(default=100, ge=1, le=500)
    context_bytes: int = Field(default=64000, ge=8000, le=32000000)
    prompt_blocks: list[PromptBlock] = Field(default_factory=list, max_length=64)
    role_prompt: str = Field(default='依据角色设定自然扮演角色，分清背景设定、示例台词与真实对话。', max_length=8000)
    post_history_prompt: str = Field(default='', max_length=8000)
    tool_submission_hint: str = Field(default=DEFAULT_TOOL_SUBMISSION_HINT, max_length=1000)
    summary_instructions: str = Field(default='', max_length=4000)

    @model_validator(mode='after')
    def budgets(self):
        if self.strategy != 'legacy':
            if self.retain_turns < 40 or self.history_trigger_turns <= self.retain_turns:
                raise ValueError('新策略至少保留40轮，触发轮数须大于保留轮数。')
            if (self.history_trigger_tokens is None) != (self.history_target_tokens is None):
                raise ValueError('历史Token触发量与目标须同时设置，或同时留空。')
            if self.history_trigger_tokens is not None and self.history_target_tokens >= self.history_trigger_tokens:
                raise ValueError('历史Token目标须小于触发量。')
            if self.retrieval_target_tokens > self.retrieval_budget_tokens:
                raise ValueError('检索缩减目标不能大于检索预算。')
        if len({block.id for block in self.prompt_blocks}) != len(self.prompt_blocks):
            raise ValueError('提示词段编号不能重复。')
        if self.summary_target_tokens * 1.2 >= self.summary_max_tokens:
            raise ValueError('摘要输出上限须大于正文目标及 20% 余量。')
        if self.context_window and self.context_window <= self.output_reserve + self.safety_margin:
            raise ValueError('上下文窗口不足以容纳输出预留与安全余量。')
        return self


class PresetContent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    context: ContextPolicy = Field(default_factory=ContextPolicy)
    max_output_tokens: int = Field(default=4096, ge=1, le=1000000)
    temperature: float | None = Field(default=None, ge=0, le=10)
    reasoning_effort: Literal['inherit', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'] | None = 'inherit'

    @model_validator(mode='after')
    def size(self):
        if len(self.model_dump_json().encode('utf-8')) > 256000:
            raise ValueError('单份预设合计超过 256000 字节。')
        if self.context.context_window and self.context.context_window <= max(self.context.output_reserve, self.max_output_tokens) + self.context.safety_margin:
            raise ValueError('预设窗口不足以容纳实际输出上限与安全余量。')
        return self


def new_preset_content():
    """只供新建入口使用，不回写旧预设或无绑定的旧模型。"""
    return PresetContent(context=ContextPolicy(strategy='cost', retain_turns=40,
        context_bytes=8000000, retrieval_enabled=False, role_prompt=ROLE_PROMPT,
        prompt_blocks=[
            PromptBlock(id='rp_dialogue', name='对话与人物表现', content=DIALOGUE_PROMPT, run_types=['input']),
            PromptBlock(id='rp_contact', name='时间与后续联系', content=CONTACT_PROMPT, run_types=['input']),
            PromptBlock(id=IMMERSION_BLOCK_ID, name=IMMERSION_BLOCK_NAME, content=IMMERSION_PROMPT,
                        enabled=False, run_types=['input']),
        ]))


class ProfileBinding(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    preset_id: str | None = Field(default=None, pattern=r'^[0-9a-f]{32}$')
    revision: int | None = Field(default=None, ge=1)
    overrides: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode='after')
    def reference(self):
        if (self.preset_id is None) != (self.revision is None):
            raise ValueError('预设编号和版本必须同时填写。')
        if set(self.overrides) - {'context', 'max_output_tokens', 'temperature', 'reasoning_effort'}:
            raise ValueError('模型本地修改包含未知字段。')
        if 'context' in self.overrides and (not isinstance(self.overrides['context'], dict) or set(self.overrides['context']) - set(ContextPolicy.model_fields)):
            raise ValueError('模型本地上下文修改包含未知字段。')
        return self
