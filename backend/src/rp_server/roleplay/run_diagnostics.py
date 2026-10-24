"""有界、可追溯的运行问题；诊断不进入模型上下文，不按旧错误文字猜测原因。"""
import json
from dataclasses import dataclass, field
from time import monotonic
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


IssueCode = Literal['configuration', 'context_invalid', 'context_budget', 'provider_request', 'empty_response', 'output_limit',
    'incomplete_response', 'tool_identity', 'tool_limit', 'tool_json', 'tool_arguments', 'plan_json', 'plan_validation', 'tool_execution',
    'missing_plan', 'tool_selection', 'repair_exhausted', 'step_limit', 'commit_validation', 'stale_run', 'cancelled', 'timeout', 'internal']
IssueStage = Literal['setup', 'context', 'provider', 'response', 'tools', 'cleanup', 'commit', 'runtime']


class FieldIssue(BaseModel):
    model_config = ConfigDict(extra='forbid')
    path: list[str | int] = Field(default_factory=list, max_length=12)
    code: str = Field(max_length=96)
    message: str = Field(max_length=500)


class RunIssue(BaseModel):
    model_config = ConfigDict(extra='forbid')
    code: IssueCode
    stage: IssueStage
    message: str = Field(max_length=1000)
    step: int | None = None
    step_id: int | None = None
    tool: str | None = Field(default=None, max_length=128)
    call_id: str | None = Field(default=None, max_length=256)
    repair_attempt: int | None = None
    stage_elapsed_ms: int | None = Field(default=None, ge=0)
    fields: list[FieldIssue] = Field(default_factory=list, max_length=8)


@dataclass
class RunProgress:
    """每轮独立的内存位置；只在失败终态保存，不增加阶段写库或跨轮缓存。"""
    stage: IssueStage = 'setup'
    step: int | None = None
    step_id: int | None = None
    tool: str | None = None
    call_id: str | None = None
    repair_attempt: int | None = None
    entered_at: float = field(default_factory=monotonic)
    stopped_at: float | None = None

    def enter(self, stage: IssueStage, *, step=None, step_id=None, tool=None, call_id=None, repair_attempt=None):
        self.stage, self.step, self.step_id = stage, step, step_id
        self.tool = tool[:128] if tool else None
        self.call_id = call_id[:256] if call_id else None
        self.repair_attempt, self.entered_at = repair_attempt, monotonic()
        self.stopped_at = None

    def freeze(self):
        if self.stopped_at is None:
            self.stopped_at = monotonic()

    def elapsed_ms(self):
        stopped = monotonic() if self.stopped_at is None else self.stopped_at
        return max(0, int((stopped - self.entered_at) * 1000))

    def issue(self, code: IssueCode, message, *, fields=()):
        return RunIssue(code=code, stage=self.stage, message=message[:1000], step=self.step, step_id=self.step_id,
            tool=self.tool, call_id=self.call_id, repair_attempt=self.repair_attempt,
            stage_elapsed_ms=self.elapsed_ms(), fields=list(fields))


class RunError(ValueError):
    def __init__(self, message, *, code: IssueCode = 'configuration', stage: IssueStage = 'setup', **location):
        super().__init__(message)
        self.issue = RunIssue(code=code, stage=stage, message=message[:1000], **location)


@dataclass
class ToolRequestTrace:
    """从真正发送的JSON字节中提取控制字段，不保存请求正文、头部或思考。"""
    request_id: str
    controls: dict = field(default_factory=dict)

    async def prepared(self, body, *, method, url, stream):
        payload = json.loads(body)
        tools = payload.get('tools', [])
        tool_count = sum(len(tool['functionDeclarations']) if isinstance(tool, dict)
                         and isinstance(tool.get('functionDeclarations'), list) else 1 for tool in tools)
        controls = {'source': 'serialized_http_body', 'body_bytes': len(body), 'stream': stream,
                    'tool_count': tool_count}
        choice = payload.get('tool_choice')
        if isinstance(choice, str):
            controls['tool_choice'] = choice[:32]
        elif isinstance(choice, dict):
            controls['tool_choice'] = {key: value[:128] for key in ('type', 'name', 'mode')
                                       if isinstance((value := choice.get(key)), str)}
            function = choice.get('function')
            if isinstance(function, dict) and isinstance(function.get('name'), str):
                controls['tool_choice']['function'] = {'name': function['name'][:128]}
        else:
            controls['tool_choice'] = None
        for name, keys in {'thinking': ('type', 'budget_tokens'), 'reasoning': ('effort',),
                           'output_config': ('effort',), 'response_format': ('type',)}.items():
            value = payload.get(name)
            if isinstance(value, dict):
                controls[name] = {key: item[:64] if isinstance(item, str) else item
                                  for key in keys if type(item := value.get(key)) in (str, int, bool)}
        text = payload.get('text')
        output_format = text.get('format') if isinstance(text, dict) else None
        if isinstance(output_format, dict) and isinstance(output_format.get('type'), str):
            controls['text'] = {'format': {'type': output_format['type'][:64]}}
        output_config = payload.get('output_config')
        output_format = output_config.get('format') if isinstance(output_config, dict) else None
        if isinstance(output_format, dict) and isinstance(output_format.get('type'), str):
            controls.setdefault('output_config', {})['format'] = {'type': output_format['type'][:64]}
        generation = payload.get('generationConfig')
        if isinstance(generation, dict):
            controls['generationConfig'] = {key: value[:64] if isinstance(value, str) else value
                for key in ('responseMimeType', 'maxOutputTokens') if type(value := generation.get(key)) in (str, int)}
            if isinstance(generation.get('responseJsonSchema'), dict):
                controls['generationConfig']['responseJsonSchema'] = {'type': generation['responseJsonSchema'].get('type')}
            thinking = generation.get('thinkingConfig')
            if isinstance(thinking, dict):
                controls['generationConfig']['thinkingConfig'] = {key: value[:64] if isinstance(value, str) else value
                    for key in ('thinkingLevel', 'thinkingBudget') if type(value := thinking.get(key)) in (str, int)}
        tool_config = payload.get('toolConfig')
        function = tool_config.get('functionCallingConfig') if isinstance(tool_config, dict) else None
        if isinstance(function, dict) and isinstance(function.get('mode'), str):
            controls['toolConfig'] = {'functionCallingConfig': {'mode': function['mode'][:32]}}
        for name in ('reasoning_effort', 'parallel_tool_calls', 'max_tokens', 'max_output_tokens', 'max_completion_tokens'):
            value = payload.get(name)
            if type(value) in (str, int, bool):
                controls[name] = value[:64] if isinstance(value, str) else value
        self.controls.update(controls)

    def http_received(self, status):
        self.controls['http_status'] = status

    async def attempt(self, event):
        # 尝试与等待已有统一记录，避免重复落库。
        pass

    async def finish(self, status, **kwargs):
        pass

    async def unavailable(self):
        self.controls['source'] = 'unavailable'


def field_issues(error):
    if isinstance(error, ValidationError):
        return [FieldIssue(path=[part[:96] if isinstance(part, str) else part for part in item['loc'][:12]],
            code=item['type'][:96], message=item['msg'][:500]) for item in error.errors(include_input=False, include_context=False)[:8]]
    if isinstance(error, json.JSONDecodeError):
        return [FieldIssue(code='json_invalid', message=f'参数不是完整JSON对象（第{error.lineno}行，第{error.colno}列）。')]
    return [FieldIssue(code='invalid_value', message='参数嵌套过深。' if isinstance(error, RecursionError) else str(error)[:500])]


class StepIssues(BaseModel):
    step: int
    step_id: int
    status: str
    repair_attempt: int | None = None
    issues: list[RunIssue]


class DeliveryCounts(BaseModel):
    pending: int = 0
    sending: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    unknown: int = 0
    expired: int = 0


class LongPlanRecovery(BaseModel):
    restored: int = Field(ge=1, le=16)
    plan_ids: list[str] = Field(max_length=16)


class RunDiagnostics(BaseModel):
    failure: RunIssue | None
    steps: list[StepIssues]
    deliveries: DeliveryCounts
    recorded: bool
    long_plan_recovery: LongPlanRecovery | None = None


def run_diagnostics(database, run, steps):
    from .run_explanation import object_value
    report = object_value(run['context_json'])
    failure = report.get('failure')
    try:
        failure = RunIssue.model_validate(failure) if failure else None
    except (ValidationError, TypeError):
        failure = None
    try:
        recovery = LongPlanRecovery.model_validate(report['long_plan_recovery']) if report.get('long_plan_recovery') else None
    except (ValidationError, TypeError):
        recovery = None
    records = []
    for step in steps:
        response = object_value(step['response_json'])
        issues = []
        raw = response.get('validation_issues')
        for item in raw[:32] if isinstance(raw, list) else []:
            try:
                issues.append(RunIssue.model_validate(item))
            except (ValidationError, TypeError):
                continue
        if issues:
            records.append(StepIssues(step=step['step'], step_id=step['id'], status=step['status'],
                repair_attempt=response.get('repair_attempt') if type(response.get('repair_attempt')) is int else None, issues=issues))
    delivery = {item['status']: item['n'] for item in database.all(
        'SELECT status,count(*) AS n FROM rp_outbox WHERE run_id=? GROUP BY status', (run['id'],))}
    return RunDiagnostics(failure=failure, steps=records, recorded=bool(failure or records or recovery),
        deliveries=DeliveryCounts.model_validate(delivery), long_plan_recovery=recovery)
