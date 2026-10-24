"""按协议解释已返回的用量；不估价，不将缺失字段当作零。"""
from collections.abc import Callable, Iterable
from dataclasses import dataclass, fields
from typing import Any
from time import time


class AttemptUsage:
    """仅记录已开始的联网尝试数；失败、取消及重试不推断为免费。"""
    def __init__(self, persist: Callable[[dict], None], *, record_events: bool = False):
        self.started, self.persist = 0, persist
        self.record_events, self.events = record_events, []
        self.usage_by_attempt = {}
        self.persist(self.snapshot())

    def snapshot(self):
        result = {'tracked': True, 'started': self.started}
        if self.record_events:
            result['events'] = list(self.events)
        if self.usage_by_attempt:
            result['usage_by_attempt'] = [{'attempt': number, 'token_usage': usage}
                for number, usage in sorted(self.usage_by_attempt.items())]
        return result

    def begin(self):
        self.started += 1
        self.persist(self.snapshot())

    def __call__(self, event):
        if event.token_usage is not None:
            usage = {key: _number(event.token_usage, key) for key in TokenUsage.__dataclass_fields__}
            if any(value is not None for value in usage.values()):
                self.usage_by_attempt[event.attempt] = usage
        if self.record_events:
            # 只保存本地元数据，不复制响应正文、片段或厂商异常。
            self.events.append({'request_id': event.request_id, 'attempt': event.attempt,
                'phase': event.phase, 'at': time(), 'http_status': event.http_status,
                'retrying': event.retrying, 'retry_delay_seconds': event.retry_delay_seconds,
                **({'token_usage': usage} if event.token_usage is not None else {}),
                **({'response_summary': event.response_summary} if event.response_summary is not None else {})})
            self.events = self.events[-32:]
        if event.phase == 'started':
            self.begin()
        elif self.record_events or event.token_usage is not None:
            self.persist(self.snapshot())


@dataclass(frozen=True)
class TokenUsage:
    """输入含缓存、输出含推理；缓存与推理是子集，不再叠加到 total。"""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    cache_miss_tokens: int | None = None


def _number(data: dict[str, Any], key: str) -> int | None:
    value = data.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _sum_known(values: Iterable[int | None]) -> int | None:
    values = list(values)
    return sum(values) if values and all(value is not None for value in values) else None


def aggregate_attempt_usage(final_usage, attempts):
    """计费按尝试求和；顶层最终用量仍供上下文预算使用，不能相加两遍。"""
    names = TokenUsage.__dataclass_fields__
    final = {name: _number(final_usage, name) for name in names}
    count = _number(attempts, 'started') if attempts.get('tracked') is True else None
    reports = {}
    raw = attempts.get('usage_by_attempt')
    for item in raw[:32] if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get('token_usage'), dict):
            continue
        number = _number(item, 'attempt')
        usage = {name: _number(item['token_usage'], name) for name in names}
        if number is not None and number > 0 and count is not None and number <= count and any(value is not None for value in usage.values()):
            reports[number] = usage
    if not reports:
        return final, int(any(value is not None for value in final.values()))
    if count and count not in reports and any(value is not None for value in final.values()):
        reports[count] = final
    # 某尝试未报告字段时保留未知，不用其他尝试的部分值伪装成完整合计。
    return {name: _sum_known(row[name] for row in reports.values()) for name in names}, len(reports)


def normalize_usage(
    raw: dict[str, Any] | None, api_style: str | None, provider_kind: str = "compatible",
) -> TokenUsage | None:
    """协议决定基本语义，提供商决定别名；缺失与零仍严格区分。"""
    if raw is None:
        return None
    cache_read = cache_write = cache_miss = reasoning = total = None
    if api_style in ("chat_completions", "responses"):
        chat = api_style == "chat_completions"
        input_tokens = _number(raw, "prompt_tokens" if chat else "input_tokens")
        output_tokens = _number(raw, "completion_tokens" if chat else "output_tokens")
        input_details = raw.get("prompt_tokens_details" if chat else "input_tokens_details")
        output_details = raw.get("completion_tokens_details" if chat else "output_tokens_details")
        if isinstance(input_details, dict):
            cache_read = _number(input_details, "cached_tokens")
            cache_write = _number(input_details, "cache_write_tokens")
        if isinstance(output_details, dict):
            reasoning = _number(output_details, "reasoning_tokens")
        total = _number(raw, "total_tokens")
        if chat and provider_kind == "deepseek":
            hit = _number(raw, "prompt_cache_hit_tokens")
            if hit is not None:
                cache_read = hit
            cache_miss = _number(raw, "prompt_cache_miss_tokens")
        elif chat and provider_kind == "moonshot" and cache_read is None:
            # 顶层与嵌套字段是同一统计的别名，不能相加。
            cache_read = _number(raw, "cached_tokens")
    elif api_style == "anthropic_messages":
        cache_read = _number(raw, "cache_read_input_tokens")
        cache_write = _number(raw, "cache_creation_input_tokens")
        # Anthropic 的 input_tokens 是未缓存部分，不能直接等同其他协议的输入总量。
        input_tokens = _sum_known((_number(raw, "input_tokens"), cache_read, cache_write))
        output_tokens = _number(raw, "output_tokens")
    elif api_style == "gemini":
        input_tokens = _number(raw, "promptTokenCount")
        cache_read = _number(raw, "cachedContentTokenCount")
        reasoning = _number(raw, "thoughtsTokenCount")
        total = _number(raw, "totalTokenCount")
        output_tokens = _sum_known((_number(raw, "candidatesTokenCount"), reasoning))
        if output_tokens is None and total is not None and input_tokens is not None and total >= input_tokens:
            output_tokens = total - input_tokens
    else:
        return None
    if total is None:
        total = _sum_known((input_tokens, output_tokens))
    usage = TokenUsage(input_tokens, output_tokens, total, cache_read, cache_write, reasoning, cache_miss)
    return usage if any(getattr(usage, item.name) is not None for item in fields(TokenUsage)) else None


def summarize_usage(usages: Iterable[TokenUsage | None], *, unreported_attempts: int = 0) -> dict[str, int | None]:
    """每字段只有全步骤已知才给总数；单步原值可查看，不伪造全程消耗。"""
    usages = list(usages)
    summary = {
        "model_steps": len(usages),
        "reported_steps": sum(usage is not None for usage in usages),
        "unreported_attempts": unreported_attempts,
    }
    for item in fields(TokenUsage):
        summary[item.name] = None if unreported_attempts else _sum_known(
            getattr(usage, item.name) if usage is not None else None for usage in usages
        )
    return summary
