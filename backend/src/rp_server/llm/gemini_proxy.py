"""服务器维护的单凭据代理；不改变应用共用客户端或系统代理。"""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx

from .models import ModelConfig


HOST = 'generativelanguage.googleapis.com'


class _CredentialTransport(httpx.AsyncBaseTransport):
    def __init__(self, proxy_url: str, key_digest: str):
        self._key_digest = key_digest
        self._proxy = httpx.AsyncHTTPTransport(proxy=proxy_url, verify=True, trust_env=False,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        key = request.headers.get('x-goog-api-key', '')
        if (request.url.scheme != 'https' or request.url.host != HOST or request.url.port not in (None, 443)
                or not hmac.compare_digest(hashlib.sha256(key.encode()).hexdigest(), self._key_digest)):
            raise ValueError('专用Gemini客户端拒绝超出绑定域名或凭据范围的请求。')
        return await self._proxy.handle_async_request(request)

    async def aclose(self):
        await self._proxy.aclose()


class GeminiProxy:
    def __init__(self, policy: dict | None = None):
        self.policy = policy
        self._client: httpx.AsyncClient | None = None
        if policy is None:
            return
        if set(policy) != {'connection_id', 'credential_ref', 'key_sha256', 'proxy_url'}:
            raise ValueError('Gemini专用代理配置字段不完整或包含未知字段。')
        for field, length in (('connection_id', 32), ('credential_ref', 32), ('key_sha256', 64)):
            if not isinstance(policy[field], str) or not re.fullmatch(f'[a-f0-9]{{{length}}}', policy[field]):
                raise ValueError('Gemini专用代理绑定格式无效。')
        try:
            proxy = urlsplit(policy['proxy_url'])
            valid = (proxy.scheme == 'http' and proxy.hostname in {'127.0.0.1', '::1'}
                     and proxy.port and not proxy.username and not proxy.password
                     and proxy.path in {'', '/'} and not proxy.query and not proxy.fragment)
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError('Gemini专用代理必须是无认证信息的本地回环HTTP代理地址。')

    @classmethod
    def from_env(cls):
        path = os.environ.get('RP_GEMINI_PROXY_CONFIG')
        if not path:
            return cls()
        try:
            value = json.loads(Path(path).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise ValueError('无法读取Gemini专用代理绑定配置。') from None
        if not isinstance(value, dict):
            raise ValueError('Gemini专用代理配置必须是对象。')
        return cls(value)

    def client_for(self, config: ModelConfig, api_key: str, default: httpx.AsyncClient):
        policy = self.policy
        if (policy is None or config.connection_id != policy['connection_id']
                or config.credential_ref != policy['credential_ref']):
            return default
        address = urlsplit(config.endpoint_url)
        if address.hostname != HOST:
            return default
        if (address.scheme != 'https' or address.port not in (None, 443)
                or not hmac.compare_digest(hashlib.sha256(api_key.encode()).hexdigest(), policy['key_sha256'])):
            raise ValueError('Gemini专用代理绑定已失效，请由管理员更新凭据绑定。')
        if self._client is None:
            # 独立连接池不继承环境代理，也不跟随跳转把目标Key送往其他域名。
            self._client = httpx.AsyncClient(trust_env=False, follow_redirects=False,
                transport=_CredentialTransport(policy['proxy_url'], policy['key_sha256']))
        return self._client

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        if self._client is not None:
            await self._client.aclose()
