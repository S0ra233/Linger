"""可恢复网络失败及主叙事429限次重试；已输出、协议错误与取消不重放。"""
import asyncio
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from datetime import datetime, timezone
from dataclasses import asdict
from email.utils import parsedate_to_datetime
import math
from typing import TypeVar
from time import monotonic
from uuid import uuid4

import httpx

from ..attempts import ModelAttemptEvent, ModelAttemptSink
from ..contracts import TextSink
from ..diagnostics import RequestTrace
from ..usage import TokenUsage
from .stream_events import StreamFormatError, StreamInterruptedError, StreamProgress, stream_progress


class ProviderError(RuntimeError):
    """可以展示的连接、服务或响应错误；不包含远端正文及密钥。"""


class EmptyResponseError(ProviderError):
    """已完整结束但没有最终内容；仅携带可持久化的统计和结构摘要。"""
    def __init__(self, message, *, token_usage=None, response_summary=None):
        super().__init__(message)
        self.token_usage, self.response_summary = token_usage, response_summary


# 仅由RP主叙事请求启用，不传播到模型检查、保活、MCP或缓存资源操作。
allow_rate_limit_retry: ContextVar[bool] = ContextVar('rp_allow_rate_limit_retry', default=False)
RATE_LIMIT_RETRIES = 2
RATE_LIMIT_DELAY = 60.0
UNAVAILABLE_DELAYS = (15.0, 30.0)
EMPTY_RESPONSE_DELAY = 3.0


def rate_limit_delay(exc: httpx.HTTPStatusError) -> float | None:
    """固定等待一分钟；有效服务端等待更长时安全退出，不提前重发。"""
    raw = exc.response.headers.get('Retry-After', '').strip()
    try:
        if len(raw) > 128:
            return RATE_LIMIT_DELAY
        if raw and raw.isascii() and raw.isdigit():
            seconds = float(raw)
        else:
            when = parsedate_to_datetime(raw)
            when = when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when
            seconds = (when - datetime.now(timezone.utc)).total_seconds()
    except (ValueError, TypeError, OverflowError):
        seconds = RATE_LIMIT_DELAY
    if not math.isfinite(seconds) or seconds > RATE_LIMIT_DELAY:
        return None
    return RATE_LIMIT_DELAY


def http_error(exc: Exception) -> ProviderError:
    """区分等待超时和真实中断；原异常通过 cause 保留供定位。"""
    if isinstance(exc, httpx.LocalProtocolError):
        return ProviderError("本地HTTP请求格式无效，未取得模型响应；请检查鉴权头和接入配置。")
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError("连接或响应等待超时，请检查网络或调整超时设置。")
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 429:
            return ProviderError('模型服务返回HTTP 429（请求受限或暂时繁忙）。')
        if exc.response.status_code == 503:
            return ProviderError('模型服务暂时不可用（HTTP 503），可能繁忙或正在临时维护；不是QQ投递失败，也不能据此判定密钥或提示词错误。')
        detail = exc.response.extensions.get('rp_error_detail', {})
        if detail.get('kind') == 'thinking_tool_conflict':
            return ProviderError(f'模型服务拒绝请求（HTTP {exc.response.status_code}）：服务端报告思考模式与tool_choice冲突；'
                                 '请核对提供商、协议、思考适配和实际工具策略。系统未自动关闭思考。')
        if exc.response.status_code == 400:
            parameter = f' 服务端指出参数：{detail["parameter"]}。' if detail.get('parameter') else ''
            if detail.get('fields'):
                parameter += ' 服务端指出字段：' + '、'.join(detail['fields']) + '。'
            reason = {
                'schema_complexity': '服务端报告工具或输出Schema过于复杂；请检查嵌套数组和数值边界。',
                'thought_signature': '服务端报告思考签名问题；请检查工具调用原始签名和历史续接。',
                'tool_schema': '服务端报告工具声明问题；请核对完整工具Schema，最小计划检查不能覆盖所有工具。',
            }.get(detail.get('kind'))
            if reason:
                return ProviderError('模型服务拒绝请求（HTTP 400）：' + reason + parameter)
            if detail.get('status') == 'INVALID_ARGUMENT':
                return ProviderError('模型服务拒绝请求（HTTP 400 / INVALID_ARGUMENT）；' +
                    ('请检查服务端指出的字段。' if parameter else '服务端没有提供具体参数或字段原因。') + parameter +
                    '基础连接或最小计划成功不代表完整工具声明与历史请求兼容；这不是QQ投递回执。')
            return ProviderError('模型服务拒绝请求（HTTP 400）；请核对协议、模型和参数，尤其是思考与工具选择组合、工具Schema及历史续接。'
                                 + parameter + '基础连接成功不代表这些组合兼容；这不是QQ投递回执。')
        return ProviderError(f"模型服务拒绝请求（HTTP {exc.response.status_code}）；请检查协议、密钥、模型和参数。")
    if isinstance(exc, (ProviderError, StreamFormatError)):
        return ProviderError(str(exc))
    return ProviderError("无法请求模型服务，请检查接口地址、网络和代理设置。")


RETRYABLE_ERRORS = (
    httpx.NetworkError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout,
    httpx.RemoteProtocolError, httpx.ProxyError, StreamInterruptedError,
)
T = TypeVar("T")


async def request_with_retries(
    operation: Callable[[TextSink], Awaitable[T]], *, max_attempts: int,
    on_attempt: ModelAttemptSink | None = None, on_text: TextSink | None = None,
    trace: RequestTrace | None = None,
) -> T:
    """operation 捕获同一份 payload；连接复用和关闭由 HTTP 层负责。"""
    request_id = trace.request_id if trace is not None else uuid4().hex
    rate_limit_retries, unavailable_retries, empty_retries, number = 0, 0, 0, 0
    while True:
        number += 1
        # 即使上一尝试立即失败，也先允许事件循环处理用户取消。
        await asyncio.sleep(0)
        fragments: list[str] = []
        progress = StreamProgress()

        def notify(phase: str, *, error: str | None = None, retrying: bool = False,
                   duration_seconds: float | None = None, http_status: int | None = None,
                   retry_delay_seconds: float | None = None, token_usage=None, response_summary=None) -> ModelAttemptEvent:
            event = ModelAttemptEvent(request_id, number, phase,
                partial_text="".join(fragments) if phase in ("failed", "cancelled") else "",
                error=error, retrying=retrying, duration_seconds=duration_seconds,
                http_status=http_status, retry_delay_seconds=retry_delay_seconds,
                token_usage=token_usage, response_summary=response_summary)
            if on_attempt is not None:
                on_attempt(event)
            return event

        def accept_text(text: str) -> None:
            if text:
                progress.has_output = True
            if on_attempt is not None:
                fragments.append(text)
            if on_text is not None:
                on_text(text)

        event = notify("started")
        if trace is not None:
            await trace.attempt(event)
        started = monotonic()
        progress_token = stream_progress.set(progress)
        try:
            value = await operation(accept_text)
        except asyncio.CancelledError:
            event = notify("cancelled")
            if trace is not None:
                await trace.attempt(event)
            raise
        except (httpx.RequestError, httpx.InvalidURL, httpx.HTTPStatusError, StreamFormatError, ProviderError) as exc:
            retrying = isinstance(exc, RETRYABLE_ERRORS) and not (rate_limit_retries or unavailable_retries or empty_retries) and not progress.has_output and number < max_attempts
            limited = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429
            unavailable = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 503
            empty = isinstance(exc, EmptyResponseError)
            http_status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            if empty: http_status = 200
            delay = None
            # 429有独立的两次额度，不被连接页的网络尝试次数提前耗尽。
            # 第一次429补偿之后，其他网络错误仍立即收束，避免重试相乘。
            compensation = rate_limit_retries + unavailable_retries + empty_retries
            if empty and on_text is None and allow_rate_limit_retry.get() and empty_retries == 0 and compensation < RATE_LIMIT_RETRIES and not progress.has_output:
                # 非流式RP只补偿一次空结果；与429／503共用总额度，不能指数叠加。
                delay, retrying = EMPTY_RESPONSE_DELAY, True
                empty_retries += 1
            elif limited and allow_rate_limit_retry.get() and compensation < RATE_LIMIT_RETRIES and not progress.has_output:
                delay = rate_limit_delay(exc)
                if delay is not None:
                    retrying = True
                    rate_limit_retries += 1
            elif unavailable and allow_rate_limit_retry.get() and compensation < RATE_LIMIT_RETRIES and not progress.has_output:
                # 两类HTTP状态共用补偿额度，避免429／503交替时重试相乘。
                suggested = exc.response.headers.get('Retry-After', '').strip()
                wait = rate_limit_delay(exc) if suggested else 0.0
                if wait is not None:
                    delay = max(UNAVAILABLE_DELAYS[unavailable_retries], wait)
                    unavailable_retries += 1
                    retrying = True
            error = http_error(exc)
            if progress.has_output and isinstance(exc, RETRYABLE_ERRORS):
                error = ProviderError(str(error) + "本次已收到模型输出，未自动重发；已显示片段不是完整回答，请手动决定是否继续。")
            event = notify("failed", error=str(error), retrying=retrying,
                           http_status=http_status, retry_delay_seconds=delay,
                           token_usage=exc.token_usage if empty else None,
                           response_summary=exc.response_summary if empty else None)
            if trace is not None:
                await trace.attempt(event)
            if not retrying:
                if empty:
                    detail = ('空响应已补偿重试一次，仍未获得有效内容；本轮停止。' if empty_retries else
                              '已达本步补偿上限，未额外重发空响应。' if compensation >= RATE_LIMIT_RETRIES else
                              '当前请求不启用空响应补偿，未自动重发。')
                    raise EmptyResponseError(str(error) + detail, token_usage=exc.token_usage,
                                             response_summary=exc.response_summary) from exc
                elif empty_retries:
                    detail = f'已因空响应重试{empty_retries}次、HTTP429重试{rate_limit_retries}次、HTTP503重试{unavailable_retries}次，共请求{number}次；本轮停止。'
                elif unavailable_retries:
                    detail = f'已因HTTP 503重试{unavailable_retries}次、因HTTP 429重试{rate_limit_retries}次，共请求{number}次，请求停止。'
                elif unavailable and allow_rate_limit_retry.get() and not progress.has_output:
                    detail = ('已达HTTP补偿重试上限，本轮停止请求。' if rate_limit_retries + unavailable_retries >= RATE_LIMIT_RETRIES else '服务端要求等待超过60秒，本轮未提前重发。')
                elif limited and allow_rate_limit_retry.get() and not progress.has_output:
                    detail = (f'已因HTTP 429重试{rate_limit_retries}次，共请求{number}次，已达重试上限。'
                              if rate_limit_retries >= RATE_LIMIT_RETRIES else
                              f'服务端要求等待超过60秒，本轮未提前重发；共请求{number}次。')
                elif rate_limit_retries:
                    detail = f'已因HTTP 429重试{rate_limit_retries}次，共请求{number}次，请求停止。'
                else:
                    detail = f"已尝试 {number} 次，请求停止。" if isinstance(exc, RETRYABLE_ERRORS) else ""
                raise ProviderError(str(error) + detail) from exc
            if delay is not None:
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    event = notify('cancelled')
                    if trace is not None:
                        await trace.attempt(event)
                    raise
        else:
            # 每次重发单独计时；工具执行发生在返回以后，不进入本次请求耗时。
            usage = getattr(value, 'token_usage', None)
            event = notify("received", duration_seconds=monotonic() - started,
                           token_usage=asdict(usage) if isinstance(usage, TokenUsage) else None)
            if trace is not None:
                await trace.attempt(event)
            return value
        finally:
            stream_progress.reset(progress_token)
