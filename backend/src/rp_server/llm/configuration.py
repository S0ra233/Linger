"""管理表单、已确认能力与一次运行的模型快照之间的边界。"""
from dataclasses import dataclass, field, replace
import hashlib
import json
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .messages import Message, ToolSchema
from .models import ModelConfig, ModelRequest
from .reasoning import ReasoningProfile, resolve_effort
from .rules import RequestRules
from .validation import validate_model_config
from .preset_models import ProfileBinding, PresetContent
from .tool_policy import ToolPolicy
from .endpoints import resolve_endpoint


Protocol = Literal['chat_completions', 'responses', 'anthropic_messages', 'gemini',
                   'embedding_openai', 'embedding_ollama', 'embedding_gemini']
Effort = Literal['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra']
Purpose = Literal['narrator', 'summary', 'vision', 'embedding']
PROTOCOL_LABELS = {
    'chat_completions': 'Chat Completions', 'responses': 'Responses',
    'anthropic_messages': 'Anthropic Messages', 'gemini': 'Gemini',
    'embedding_openai': 'OpenAI Embedding', 'embedding_ollama': 'Ollama Embedding',
    'embedding_gemini': 'Gemini Embedding',
}


class InputModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


def identifier(value: str) -> str:
    if UUID(value).hex != value:
        raise ValueError('编号须为规范的小写 UUID hex。')
    return value


class ReasoningSettings(InputModel):
    adapter: Literal['unsupported', 'openai', 'deepseek', 'anthropic', 'anthropic_combined', 'anthropic_adaptive',
                     'anthropic_budget', 'gemini_level', 'gemini_budget']
    effort_map: dict[Effort, str | int] = Field(default_factory=dict, max_length=8)
    tool_efforts: list[Effort] | None = Field(default=None, max_length=8)
    default_thinking: bool | None = None
    temperature_efforts: list[Effort] | None = Field(default=None, max_length=8)
    image_efforts: list[Effort] | None = Field(default=None, max_length=8)
    min_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    max_thinking_tokens: int | None = Field(default=None, ge=1, le=1_000_000)
    thinking_mode: Literal['enabled', 'disabled', 'adaptive', 'between_tools'] | None = None
    thinking_budget: int | None = Field(default=None, ge=1024, le=1_000_000)
    effort_delivery: Literal['top_level', 'message'] = 'top_level'
    baseline_effort: Literal['low', 'medium', 'high', 'xhigh', 'max'] | None = None


class ToolPolicySettings(InputModel):
    plan_output: Literal['system', 'tool', 'json'] = 'system'
    normal_mode: Literal['system', 'auto', 'required'] = 'system'
    repair_mode: Literal['system', 'prompt', 'named', 'allowed'] = 'system'
    force_with_thinking: bool = False
    schema_mode: Literal['compatible', 'strict'] = 'compatible'
    parallel_mode: Literal['default', 'serial'] = 'default'


class ModelDefinition(InputModel):
    id: str | None = None
    model: str = Field(min_length=1, max_length=200)
    label: str = Field(default='', max_length=80)
    image_input: bool | None = None
    tool_input: bool | None = None
    tool_policy: ToolPolicySettings | None = None
    context_window: int | None = Field(default=None, ge=1, le=20_000_000)
    max_output_tokens: int = Field(default=4096, ge=1, le=1_000_000)
    temperature: float | None = Field(default=None, ge=0, le=10, allow_inf_nan=False)
    reasoning: ReasoningSettings | None = None
    reasoning_effort: Effort | None = None
    embedding_dimensions: int | None = Field(default=None, ge=1, le=16384)
    profile: ProfileBinding | None = None

    @field_validator('id')
    @classmethod
    def valid_id(cls, value):
        return identifier(value) if value else value

    @field_validator('model')
    @classmethod
    def model_name(cls, value):
        if not value.strip():
            raise ValueError('模型 ID 不能为空。')
        return value.strip()


class ProtocolOptions(InputModel):
    output_limit_field: Literal['max_tokens', 'max_completion_tokens'] | None = None
    stream_usage: bool = False
    include_encrypted_reasoning: bool = False
    cache_control: bool = False
    gemini_explicit_cache_url: str | None = Field(default=None, max_length=2000)
    gemini_explicit_cache_confirmed: bool = False

    def rules(self, protocol: str) -> RequestRules:
        value = RequestRules(
            output_limit_field=(self.output_limit_field or 'max_tokens') if protocol == 'chat_completions' else None,
            stream_usage=self.stream_usage if protocol == 'chat_completions' else None,
            include_encrypted_reasoning=self.include_encrypted_reasoning if protocol == 'responses' else None,
            cache_control=self.cache_control,
        )
        value.validate(protocol)
        return value


class ConnectionInput(InputModel):
    name: str = Field(min_length=1, max_length=80)
    protocol: Protocol
    provider_kind: Literal['compatible', 'openai', 'deepseek', 'moonshot', 'anthropic', 'google'] = 'compatible'
    endpoint_url: str = Field(min_length=1, max_length=2000)
    endpoint_mode: Literal['base', 'full'] = 'full'
    enabled: bool = True
    timeout_seconds: int = Field(default=180, ge=5, le=600)
    connect_timeout_seconds: int = Field(default=30, ge=1, le=120)
    max_attempts: int = Field(default=3, ge=1, le=5)
    options: ProtocolOptions = Field(default_factory=ProtocolOptions)
    models: list[ModelDefinition] = Field(min_length=1, max_length=64)
    revision: int = Field(default=0, ge=0)
    key_action: Literal['keep', 'replace', 'clear'] = 'keep'
    api_key: str | None = Field(default=None, max_length=32768, repr=False)

    @field_validator('name')
    @classmethod
    def clean_name(cls, value):
        if not value.strip():
            raise ValueError('连接名称不能为空。')
        return value.strip()

    @field_validator('endpoint_url')
    @classmethod
    def endpoint(cls, value):
        value = value.strip()
        address = urlsplit(value)
        if (address.scheme not in {'http', 'https'} or not address.hostname or address.username
                or address.password or address.query or address.fragment):
            raise ValueError('请填写不含账号、查询参数或片段的 HTTP(S) 接口地址，密钥填写到独立字段。')
        address.port
        return value

    @model_validator(mode='after')
    def combinations(self):
        cache_url = self.options.gemini_explicit_cache_url
        if cache_url:
            address, cache = urlsplit(self.endpoint_url), urlsplit(cache_url)
            if (self.protocol != 'gemini' or cache.scheme not in {'http', 'https'} or cache.username or cache.password or cache.query or cache.fragment
                    or not cache.path.rstrip('/').endswith('/cachedContents')
                    or (cache.scheme, cache.hostname, cache.port or (443 if cache.scheme == 'https' else 80)) != (address.scheme, address.hostname, address.port or (443 if address.scheme == 'https' else 80))):
                raise ValueError('显式缓存需使用 Gemini，资源地址须与生成端点同源，并以 /cachedContents 结尾。')
        if self.options.gemini_explicit_cache_confirmed and not cache_url:
            raise ValueError('确认显式缓存能力前请填写资源地址。')
        if self.key_action == 'replace' and (not self.api_key or not self.api_key.strip()):
            raise ValueError('替换密钥时必须填写新密钥。')
        if self.key_action != 'replace' and self.api_key:
            raise ValueError('填写密钥后请选择替换，不能在保留或清除模式下提交明文。')
        ids = [model.id for model in self.models if model.id]
        if len(ids) != len(set(ids)):
            raise ValueError('模型条目编号不能重复。')
        if len({model.model for model in self.models}) != len(self.models):
            raise ValueError('同一连接的 API 模型 ID 不能重复。')
        if not self.is_embedding:
            for model in self.models:
                config = self.model_config_for(model)
                validate_model_config(config)
                if model.profile is not None:
                    # 预设关联需要查询固定版本，交给 ModelService 合成后再校验实际参数。
                    continue
                resolve_effort(config.reasoning, model.reasoning_effort,
                               temperature=model.temperature, max_output_tokens=model.max_output_tokens)
                if model.context_window is not None and model.max_output_tokens >= model.context_window:
                    raise ValueError('最大输出必须小于配置的上下文窗口。')
        elif any(model.reasoning is not None or model.reasoning_effort is not None for model in self.models):
            raise ValueError('Embedding 连接不使用生成模型的思考参数。')
        if self.is_embedding and any(model.tool_policy is not None for model in self.models):
            raise ValueError('Embedding 模型不使用工具策略。')
        if self.protocol == 'chat_completions' and self.provider_kind == 'deepseek' and urlsplit(self.resolved_endpoint_url).hostname == 'api.deepseek.com':
            if any(model.tool_policy and model.tool_policy.schema_mode == 'strict' for model in self.models) and not urlsplit(self.resolved_endpoint_url).path.startswith('/beta/'):
                raise ValueError('DeepSeek官方严格工具模式需使用 /beta 基础地址。')
        if self.is_embedding and any(model.profile is not None for model in self.models):
            raise ValueError('Embedding 模型不使用生成预设。')
        return self

    @property
    def is_embedding(self) -> bool:
        return self.protocol.startswith('embedding_')

    @property
    def resolved_endpoint_url(self) -> str:
        return resolve_endpoint(self.endpoint_url, self.protocol, self.endpoint_mode)

    def model_config_for(self, model: ModelDefinition, credential_ref: str | None = None) -> ModelConfig:
        return ModelConfig(
            endpoint_url=self.resolved_endpoint_url, model=model.model, api_style=self.protocol,
            provider_kind=self.provider_kind, image_input=model.image_input,
            context_window=model.context_window, timeout_seconds=self.timeout_seconds,
            connect_timeout_seconds=self.connect_timeout_seconds, max_attempts=self.max_attempts,
            provider_rules=self.options.rules(self.protocol),
            reasoning=ReasoningProfile(**model.reasoning.model_dump()) if model.reasoning else None,
            credential_ref=credential_ref, key_env='RP_MODEL_KEY',
            tool_policy=ToolPolicy(**model.tool_policy.model_dump()) if model.tool_policy else ToolPolicy(),
        )


class ModelBinding(InputModel):
    connection_id: str
    model_id: str
    _connection_id = field_validator('connection_id')(identifier)
    _model_id = field_validator('model_id')(identifier)


class AssignmentsInput(InputModel):
    revision: int = Field(ge=0)
    narrator: ModelBinding | None = None
    summary: ModelBinding | None = None
    vision: ModelBinding | None = None
    embedding: ModelBinding | None = None


class CheckInput(InputModel):
    model_id: str
    revision: int = Field(ge=1)
    stream: bool = True
    kind: Literal['connection', 'plan'] = 'connection'
    _model_id = field_validator('model_id')(identifier)


class RevisionInput(InputModel):
    revision: int = Field(ge=1)


@dataclass(frozen=True)
class ModelSnapshot:
    connection_id: str
    revision: int
    connection: ConnectionInput
    model: ModelDefinition
    credential_ref: str | None
    api_key: str = field(repr=False)
    preset_config: PresetContent = field(default_factory=PresetContent)

    def config(self) -> ModelConfig:
        return replace(self.connection.model_config_for(self.model, self.credential_ref), connection_id=self.connection_id)

    def request(self, messages: list[Message], tools: list[ToolSchema] | None = None) -> ModelRequest:
        if self.connection.is_embedding:
            raise ValueError('Embedding 连接不能创建生成请求。')
        if tools and self.model.tool_input is not True:
            raise ValueError('本模型的工具能力未明确启用。')
        return ModelRequest(messages=messages, tools=tools or [], temperature=self.model.temperature,
                            max_output_tokens=self.model.max_output_tokens,
                            reasoning_effort=self.model.reasoning_effort)

    def embedding_options(self) -> 'EmbeddingOptions':
        if not self.connection.is_embedding:
            raise ValueError('生成连接不能用于 Embedding。')
        return EmbeddingOptions(self.connection.protocol.removeprefix('embedding_'), self.connection.resolved_endpoint_url,
                                self.model.model, self.api_key, self.model.embedding_dimensions,
                                self.connection.timeout_seconds, self.connection.connect_timeout_seconds)


@dataclass(frozen=True)
class EmbeddingOptions:
    protocol: str
    url: str
    model: str
    api_key: str = field(repr=False)
    dimensions: int | None
    timeout_seconds: int
    connect_timeout_seconds: int = 30
    enabled: bool = True

    def fingerprint(self) -> str:
        fields = (self.protocol, self.url.rstrip('/'), self.model, self.dimensions, 'retrieval-1')
        return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()
