"""通过 Chat Completions 协议发起完整或流式消息与工具请求。"""
from functools import partial
from typing import Any

import httpx
from ....attachments.store import ImageStore

from ...contracts import TextSink, ReasoningSink
from ...diagnostics import RequestTrace
from ...attempts import ModelAttemptSink
from ...models import ModelConfig, ModelRequest, ModelResponse
from ...validation import resolve_model_config
from ...tool_policy import native_tool_control, strict_parameters
from ...structured_output import output_fields
from ..image_input import prepare_request_images
from ..http import bearer_headers, post_json, read_stream
from ..protocol_messages import chat_messages
from ..reasoning import reasoning_fields, reasoning_attempts
from .response import parse_chat_response as _parse_response
from .stream import read_chat_stream


class HttpChatProvider:
    supports_reasoning_events = True
    supports_request_trace = True
    def __init__(self, config: ModelConfig, api_key: str, *, client: httpx.AsyncClient | None = None,
                 attachments: ImageStore | None = None):
        self.config = resolve_model_config(config)
        self._api_key = api_key
        self._client = client
        self._attachments = attachments

    async def complete(self, request: ModelRequest, *, on_attempt: ModelAttemptSink | None = None,
                       on_reasoning: ReasoningSink | None = None, trace: RequestTrace | None = None) -> ModelResponse:
        return await post_json(
            self.config.endpoint_url, bearer_headers(self._api_key),
            await self._request_payload(request, stream=False), self.config.timeout_seconds, client=self._client,
            connect_timeout=self.config.connect_timeout_seconds, max_attempts=self.config.max_attempts,
            on_attempt=reasoning_attempts(self.config, request, on_attempt, on_reasoning),
            trace=trace,
            decoder=partial(_parse_response, config=self.config),
        )

    async def stream_complete(
        self, request: ModelRequest, on_text: TextSink, *, on_attempt: ModelAttemptSink | None = None,
        on_reasoning: ReasoningSink | None = None,
        trace: RequestTrace | None = None,
    ) -> ModelResponse:
        """读取 SSE 内容；调用方取消时退出客户端和响应上下文。"""
        return await read_stream(
            self.config.endpoint_url, bearer_headers(self._api_key),
            await self._request_payload(request, stream=True), self.config.timeout_seconds,
            on_text, partial(read_chat_stream, config=self.config, on_reasoning=on_reasoning), client=self._client,
            connect_timeout=self.config.connect_timeout_seconds, max_attempts=self.config.max_attempts,
            on_attempt=reasoning_attempts(self.config, request, on_attempt, on_reasoning),
            trace=trace,
        )

    async def _request_payload(self, request: ModelRequest, *, stream: bool) -> dict[str, Any]:
        """附件异步准备，厂商参数仅在已确认的服务规则下映射。"""
        reasoning = reasoning_fields(self.config, request)
        images = await prepare_request_images(request, self.config, self._attachments)
        payload = {"model": self.config.model, "messages": chat_messages(request.messages, self.config, images), "stream": stream}
        payload.update(output_fields(self.config, request))
        if stream and self.config.provider_rules.stream_usage:
            payload["stream_options"] = {"include_usage": True}
        if request.tools:
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": tool.name, "description": tool.description,
                    "parameters": strict_parameters(tool.parameters) if self.config.tool_policy.schema_mode == 'strict' else tool.parameters,
                    "strict": self.config.tool_policy.schema_mode == 'strict',
                }} for tool in request.tools
            ]
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        payload.update(native_tool_control(self.config, request))
        if request.max_output_tokens is not None:
            payload[self.config.provider_rules.output_limit_field] = request.max_output_tokens
        payload.update(reasoning)
        return payload
