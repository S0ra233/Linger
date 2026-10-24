"""MCP 管理配置；身份来自路由，凭据只写入，不进入工具声明。"""
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, model_validator


class McpInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2000)
    enabled: bool = False
    auth_type: Literal['none', 'bearer', 'api_key'] = 'bearer'
    key_action: Literal['keep', 'replace', 'clear'] = 'keep'
    token: str | None = Field(default=None, min_length=1, max_length=4096)
    connect_timeout: int = Field(default=20, ge=3, le=60)
    call_timeout: int = Field(default=45, ge=5, le=180)
    revision: int = Field(default=0, ge=0)

    @model_validator(mode='after')
    def valid(self):
        self.name, self.url = self.name.strip(), self.url.strip()
        parsed = urlsplit(self.url)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError('MCP 地址端口不正确。') from exc
        if not self.name or parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or any(c.isspace() for c in self.url):
            raise ValueError('MCP 地址须为完整 HTTP(S) 端点；凭据使用独立字段，地址不接受查询串。')
        if port is not None and port == 0:
            raise ValueError('MCP 地址端口不正确。')
        if self.key_action == 'replace' and not self.token:
            raise ValueError('请填写新的认证密钥。')
        if self.key_action != 'replace' and self.token is not None:
            raise ValueError('只有更换密钥时接收明文。')
        if self.token and (not self.token.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in self.token)):
            raise ValueError('认证密钥须为不含空白的可打印 ASCII。')
        return self


class RevisionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: int = Field(ge=1)


class ToolGrant(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    server_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    name: str = Field(min_length=1, max_length=128)
    catalog_revision: int = Field(ge=1)


class PermissionInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    revision: int = Field(ge=0)
    tools: list[ToolGrant] = Field(default_factory=list, max_length=16)
