"""模型后端共用的 HTTP 边界；协议字段转换由各 Provider 完成。"""
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any
from contextvars import ContextVar
import asyncio
import json
import re

import httpx

from ..contracts import TextSink
from ..messages import ReplayState, ToolCall
from ..models import ModelConfig, ModelResponse
from ..attempts import ModelAttemptSink
from ..diagnostics import RequestTrace
from ..usage import normalize_usage
from .retry import ProviderError, EmptyResponseError, request_with_retries


StreamReader = Callable[[httpx.Response, TextSink], Awaitable[ModelResponse]]
payload_observer: ContextVar[Callable | None] = ContextVar('rp_payload_observer', default=None)
payload_transformer: ContextVar[Callable | None] = ContextVar('rp_payload_transformer', default=None)


def bearer_headers(api_key: str) -> dict[str, str]:
    """无鉴权的本地服务不发送Bearer空值，避免HTTP客户端拒绝尾部空白。"""
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _prepare_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """先观察完整业务前缀，再应用显式缓存；流式与非流式共用同一顺序。"""
    observer = payload_observer.get()
    if observer is not None:
        observer(payload)
    transformer = payload_transformer.get()
    return transformer(payload) if transformer is not None else payload


@asynccontextmanager
async def _request_client(client: httpx.AsyncClient | None, timeout: httpx.Timeout) -> AsyncIterator[httpx.AsyncClient]:
    """借用运行内客户端；未传入时保留原有单次请求的创建和关闭方式。"""
    if client is not None:
        yield client
    else:
        async with httpx.AsyncClient(timeout=timeout) as owned_client:
            yield owned_client


async def _record_body(client, url, headers, payload, limits, trace, *, stream: bool):
    """仅开启诊断时准备一次JSON正文，重试发送同一字节串，不再次编码。"""
    if trace is None:
        return None, headers
    request = client.build_request("POST", url, headers=headers, json=payload, timeout=limits)
    body = request.content
    await trace.prepared(body, method=request.method, url=str(request.url), stream=stream)
    return body, {**headers, "Content-Type": request.headers["Content-Type"]}


async def _raise_for_status(response: httpx.Response) -> None:
    """失败响应限量读取，只保留可展示的分类；不保存厂商正文或鉴权信息。"""
    if response.is_error:
        try:
            content = bytearray()
            async with asyncio.timeout(2.0):
                async for piece in response.aiter_bytes(chunk_size=4096):
                    if len(content) + len(piece) > 16384:
                        break
                    content.extend(piece)
                else:
                    data = json.loads(content)
                    error = data.get('error') if isinstance(data, dict) else None
                    if isinstance(error, dict):
                        safe = {}
                        message = error.get('message')
                        if isinstance(message, str):
                            lower = message.lower()
                            if 'thinking mode does not support this tool_choice' in lower:
                                safe['kind'] = 'thinking_tool_conflict'
                            elif any(phrase in lower for phrase in ('too many states', 'too much branching',
                                    'schema is too complex', 'schema too complex', 'constraint compilation')):
                                safe['kind'] = 'schema_complexity'
                            elif 'thought' in lower and 'signature' in lower:
                                safe['kind'] = 'thought_signature'
                            elif any(phrase in lower for phrase in ('parametersjsonschema', 'function_declarations',
                                    'functiondeclarations')):
                                safe['kind'] = 'tool_schema'
                        # Google的通用INVALID_ARGUMENT可能没有细节；只保留标准状态及受限字段路径。
                        status = error.get('status')
                        if isinstance(status, str) and status in {'INVALID_ARGUMENT', 'FAILED_PRECONDITION', 'OUT_OF_RANGE'}:
                            safe['status'] = status
                        fields = []
                        details = error.get('details')
                        for detail in details if isinstance(details, list) else []:
                            if not isinstance(detail, dict):
                                continue
                            violations = detail.get('fieldViolations')
                            for violation in violations if isinstance(violations, list) else []:
                                field = violation.get('field') if isinstance(violation, dict) else None
                                if (isinstance(field, str) and re.fullmatch(
                                        r'(?:tools|contents|system_instruction|systemInstruction|generation_config|generationConfig|tool_config|toolConfig)(?:[.\[\]A-Za-z0-9_/$-]){0,180}', field)
                                        and field not in fields and len(fields) < 4):
                                    fields.append(field)
                        if fields:
                            safe['fields'] = fields
                        # 只保留已知参数名，远端任意文本不能借错误字段回显私人资料。
                        parameter = error.get('param')
                        if isinstance(parameter, str) and parameter in {
                            'tool_choice', 'tools', 'thinking', 'reasoning', 'reasoning_effort',
                            'output_config', 'max_tokens', 'max_output_tokens', 'temperature',
                            'model', 'input', 'messages', 'response_format', 'parallel_tool_calls',
                            'text', 'text.format', 'text.format.type', 'output_config.format',
                            'responseMimeType', 'responseJsonSchema', 'generationConfig.responseJsonSchema',
                        }:
                            safe['parameter'] = parameter
                        response.extensions['rp_error_detail'] = safe
        except (httpx.RequestError, httpx.StreamError, TimeoutError, ValueError, RecursionError):
            # 错误正文不可读仍以原HTTP状态结束，不改成网络重试，也不吞掉取消。
            pass
    response.raise_for_status()


async def post_json(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float,
    *, client: httpx.AsyncClient | None = None, connect_timeout: float = 30.0,
    max_attempts: int = 5, on_attempt: ModelAttemptSink | None = None,
    trace: RequestTrace | None = None,
    decoder: Callable[[dict[str, Any]], ModelResponse] | None = None,
) -> dict[str, Any] | ModelResponse:
    """复用显式传入的连接范围；协议解析仍交给 Provider。"""
    payload = _prepare_payload(payload)
    limits = httpx.Timeout(timeout, connect=connect_timeout)
    async with _request_client(client, limits) as active_client:
        body, send_headers = await _record_body(active_client, url, headers, payload, limits, trace, stream=False)
        async def send(on_text: TextSink) -> dict[str, Any] | ModelResponse:
            request = (active_client.build_request('POST', url, headers=headers, json=payload, timeout=limits) if trace is None
                       else active_client.build_request('POST', url, headers=send_headers, content=body, timeout=limits))
            response = await active_client.send(request, stream=True)
            try:
                if trace is not None:
                    trace.http_received(response.status_code)
                await _raise_for_status(response)
                content = bytearray()
                async for piece in response.aiter_bytes(chunk_size=65536):
                    if len(content) + len(piece) > 32 * 1024 * 1024:
                        raise ProviderError('模型响应超过 32 MiB，已停止读取。')
                    content.extend(piece)
                data = json.loads(content)
            except (ValueError, RecursionError) as exc:
                raise ProviderError("模型服务返回的内容不是有效 JSON") from exc
            finally:
                await response.aclose()
            if not isinstance(data, dict) or data.get("error"):
                raise ProviderError("模型服务返回了无效或失败的响应对象")
            # 解析属于本次尝试；空响应在这里报告用量并按原payload重试，不重跑工具。
            return decoder(data) if decoder is not None else data
        return await request_with_retries(send, max_attempts=max_attempts, on_attempt=on_attempt, trace=trace)


async def read_stream(
    url: str, headers: dict[str, str], payload: dict[str, Any], timeout: float,
    on_text: TextSink, reader: StreamReader,
    *, client: httpx.AsyncClient | None = None, connect_timeout: float = 30.0,
    max_attempts: int = 5, on_attempt: ModelAttemptSink | None = None,
    trace: RequestTrace | None = None,
) -> ModelResponse:
    """共用网络与 SSE 类型检查，完成条件交给实际协议的 reader。"""
    payload = _prepare_payload(payload)
    limits = httpx.Timeout(timeout, connect=connect_timeout)
    async with _request_client(client, limits) as active_client:
        send_headers = {**headers, "Accept": "text/event-stream"}
        body, send_headers = await _record_body(active_client, url, send_headers, payload, limits, trace, stream=True)
        async def consume(response, accept_text):
            await _raise_for_status(response)
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type != "text/event-stream":
                raise ProviderError("接口未返回 SSE 流；请关闭逐段显示或确认接口支持流式响应。")
            return await reader(response, accept_text)
        async def send(accept_text: TextSink) -> ModelResponse:
            if trace is not None:
                request = active_client.build_request("POST", url, headers=send_headers, content=body, timeout=limits)
                response = await active_client.send(request, stream=True)
                try:
                    trace.http_received(response.status_code)
                    return await consume(response, accept_text)
                finally:
                    await response.aclose()
            async with active_client.stream(
                "POST", url, headers={**headers, "Accept": "text/event-stream"}, json=payload, timeout=limits,
            ) as response:
                return await consume(response, accept_text)
        return await request_with_retries(
            send, max_attempts=max_attempts, on_attempt=on_attempt, on_text=on_text, trace=trace,
        )


def model_response(
    text: str, usage: Any, reason: Any, status: str, native_data: dict[str, Any],
    *, tool_calls: list[ToolCall], config: ModelConfig, replay: dict[str, Any],
) -> ModelResponse:
    """合法空截断仍保留用量；完整成功须有效内容，截断调用不能交给执行层。"""
    if usage is not None and not isinstance(usage, dict):
        raise ProviderError("响应中的用量信息不是对象")
    if reason is not None and not isinstance(reason, str):
        raise ProviderError("响应中的停止原因不是字符串")
    if not isinstance(text, str):
        raise ProviderError('响应文本不是字符串。')
    if not text.strip() and not tool_calls and status != 'incomplete':
        if status != 'completed':
            raise ProviderError('响应未包含有效文本或工具调用，结束状态也未确认。')
        tokens = normalize_usage(usage, config.api_style, config.provider_kind)
        items = native_data.get('output', native_data.get('content', []))
        types = [item.get('type') for item in items[:32] if isinstance(item, dict)] if isinstance(items, list) else []
        known = {'reasoning', 'message', 'text', 'thinking', 'redacted_thinking', 'tool_use', 'function_call'}
        summary = {'kind': 'empty_response', 'protocol': config.api_style, 'status': status,
                   'text_characters': len(text), 'tool_calls': 0,
                   'output_item_types': [value if isinstance(value, str) and value in known else 'other' for value in types]}
        identifier = native_data.get('id')
        if isinstance(identifier, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', identifier):
            summary['response_id'] = identifier
        raise EmptyResponseError('模型返回HTTP200且标记完成，但未包含有效正文或工具调用。',
            token_usage=asdict(tokens) if tokens is not None else None, response_summary=summary)
    if tool_calls and status != "completed":
        raise ProviderError("工具调用未完整结束，不能执行或作为完整调用续接；请调整限制后重试。")
    if len({call.call_id for call in tool_calls}) != len(tool_calls):
        raise ProviderError("模型返回了重复的工具调用 ID。")
    return ModelResponse(
        text=text, usage=usage, finish_reason=reason,
        completion_status="requires_tool" if tool_calls else status, native_data=native_data,
        tool_calls=tool_calls,
        replay_state=ReplayState(config.api_style, config.endpoint_url, config.model, replay,
                                 provider_kind=config.provider_kind, credential_ref=config.credential_ref, key_env=config.key_env,
                                 connection_id=config.connection_id),
        token_usage=normalize_usage(usage, config.api_style, config.provider_kind),
    )


def parsed_tool_call(call_id: Any, name: Any, arguments: Any) -> ToolCall:
    """在远端响应边界检查字段形状，实际参数规则由之后的工具执行层负责。"""
    try:
        return ToolCall(call_id=call_id, name=name, arguments=arguments)
    except ValueError as exc:
        raise ProviderError("模型工具调用缺少有效 ID、名称或参数字符串。") from exc
