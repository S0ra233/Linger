"""官方 MCP v1 SDK 适配；只使用 HTTP，连接进入和退出由同一异步任务持有。"""
from contextlib import asynccontextmanager, AsyncExitStack
from datetime import timedelta
import hashlib
import json
import httpx
from jsonschema import Draft202012Validator, SchemaError


class McpError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def tool_alias(server_id, remote):
    return f'mcp_{server_id}_{hashlib.sha256(remote.encode()).hexdigest()[:16]}'


def redact(value, secret):
    if isinstance(value, str):
        return value.replace(secret, '[redacted]') if secret else value
    if isinstance(value, dict):
        return {redact(key, secret): redact(item, secret) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, secret) for item in value]
    return value


def check_schema(schema):
    if not isinstance(schema, dict) or schema.get('type') != 'object':
        raise McpError('当前只支持 object 类型参数或结构化结果。')
    if schema.get('$schema', 'https://json-schema.org/draft/2020-12/schema') != 'https://json-schema.org/draft/2020-12/schema':
        raise McpError('本批支持未声明版本或 Draft 2020-12 的 Schema。')
    def inspect(value, depth=0):
        if depth > 32:
            raise McpError('Schema 嵌套过深。')
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {'$id', '$dynamicRef', '$recursiveRef'} or key == '$ref' and (not isinstance(item, str) or not (item == '#' or item.startswith('#/'))):
                    raise McpError('Schema 不接受外部引用或动态引用。')
                inspect(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                inspect(item, depth + 1)
    inspect(schema)
    if len(canonical(schema).encode('utf-8')) > 16384:
        raise McpError('单个 Schema 超过 16KiB。')
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise McpError('Schema 格式不支持。') from exc


class BoundedStream(httpx.AsyncByteStream):
    def __init__(self, source):
        self.source = source

    async def __aiter__(self):
        size = 0
        async for piece in self.source:
            size += len(piece)
            if size > 2 * 1024 * 1024:
                raise McpError('MCP 单个响应流超过 2MiB，已停止读取。')
            yield piece

    async def aclose(self):
        await self.source.aclose()


class BoundedTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.inner = httpx.AsyncHTTPTransport(retries=0,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2))

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        if 300 <= response.status_code < 400 or response.headers.get('content-encoding', 'identity').lower() != 'identity':
            await response.aclose()
            raise McpError('MCP 不接受跳转或压缩响应，请直接填写端点并关闭此端点压缩。')
        response.stream = BoundedStream(response.stream)
        return response

    async def aclose(self):
        await self.inner.aclose()


@asynccontextmanager
async def open_session(server, secret, on_tools_changed=None):
    # 不启动子进程，不传根目录，不提供模型采样、交互认证或服务端索要信息回调。
    from mcp import ClientSession
    from mcp.types import ServerNotification, ToolListChangedNotification
    from mcp.client.streamable_http import streamable_http_client
    async def incoming(message):
        if on_tools_changed and isinstance(message, ServerNotification) and isinstance(message.root, ToolListChangedNotification):
            on_tools_changed()
    headers = {'Accept-Encoding': 'identity'}
    if secret and server['auth_type'] != 'none':
        headers['Authorization' if server['auth_type'] == 'bearer' else 'X-API-Key'] = ('Bearer ' if server['auth_type'] == 'bearer' else '') + secret
    async with AsyncExitStack() as stack:
        client = await stack.enter_async_context(httpx.AsyncClient(headers=headers, follow_redirects=False,
            transport=BoundedTransport(), timeout=httpx.Timeout(server['call_timeout'], connect=server['connect_timeout'])))
        read, write, _ = await stack.enter_async_context(streamable_http_client(server['url'], http_client=client))
        session = await stack.enter_async_context(ClientSession(read, write,
            read_timeout_seconds=timedelta(seconds=server['call_timeout']), message_handler=incoming))
        await session.initialize()
        yield session


async def discover(session, secret=''):
    tools, seen, cursor = {}, set(), None
    for _ in range(20):
        page = await session.list_tools(cursor=cursor)
        for tool in page.tools:
            raw = redact(tool.model_dump(mode='json', by_alias=True, exclude_none=True), secret)
            name = raw.get('name', '')
            if not name or len(name) > 128 or name in tools:
                raise McpError('MCP 目录名称为空、过长或重复；未替换目录。')
            item = {key: raw.get(key) for key in ('name', 'description', 'inputSchema', 'outputSchema', 'annotations')}
            reason = None
            try:
                check_schema(item['inputSchema'])
                if item['outputSchema'] is not None:
                    check_schema(item['outputSchema'])
                annotations = item['annotations'] or {}
                if annotations.get('readOnlyHint') is not True:
                    raise McpError('本批只接入服务明确声明的只读查询。')
                if len(item['description'] or '') > 2000:
                    raise McpError('工具说明超过 2000 字符。')
            except McpError as exc:
                reason = str(exc)
            tools[name] = {**item, 'eligible': reason is None, 'reason': reason}
        if len(tools) > 200 or len(canonical(tools).encode('utf-8')) > 512000:
            raise McpError('工具目录超过 200 项或 512000 字节；未替换目录。')
        cursor = page.nextCursor
        if not cursor:
            return [tools[name] for name in sorted(tools)]
        if cursor in seen:
            raise McpError('工具目录游标重复；未替换目录。')
        seen.add(cursor)
    raise McpError('工具目录分页超过 20 页；未替换目录。')


def render_result(raw, secret):
    value = redact(raw.model_dump(mode='json', by_alias=True, exclude_none=True), secret)
    text, omitted = [], []
    for item in value.get('content', []):
        if item.get('type') == 'text':
            text.append(item.get('text', ''))
        else:
            omitted.append(item.get('type', 'unknown'))
    full = '\n'.join(text)
    result = {'text': full[:16000], 'truncated': len(full) > 16000, 'omitted_content_types': sorted(set(omitted))}
    structured = value.get('structuredContent')
    if structured is not None:
        if len(canonical(structured).encode('utf-8')) <= 8000:
            result['structured_content'] = structured
        else:
            result['structured_preview'] = canonical(structured).encode('utf-8')[:8000].decode('utf-8', errors='ignore')
            result['truncated'] = True
    # 多字节正文按最终字节预算压缩；原始图片、音频及资源 URI 不读取、不下载。
    while result['text'] and len(canonical(result).encode('utf-8')) > 24000:
        result['text'] = result['text'][:len(result['text']) // 2]
        result['truncated'] = True
    if len(canonical(result).encode('utf-8')) > 24000:
        return {'text': result['text'], 'truncated': True, 'omitted_content_types': ['oversized_metadata']}, bool(value.get('isError', False))
    return result, bool(value.get('isError', False))
