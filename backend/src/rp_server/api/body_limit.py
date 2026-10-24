"""在解析 JSON 前限制实际接收字节，不只相信 Content-Length。"""
from starlette.responses import JSONResponse


class ManagementBodyLimit:
    def __init__(self, app, limit: int = 65536):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in {"GET", "HEAD", "OPTIONS"}:
            return await self.app(scope, receive, send)
        path = scope['path']
        # 原始文件由路由完成登录／CSRF 检查后，逐块限制接收量。
        if path in {'/api/v1/media/upload', '/api/v1/roleplay/imports/preview', '/api/v1/models/presets/import-preview'} or (
                path.startswith('/api/v1/information/libraries/') and path.endswith('/documents/import')):
            return await self.app(scope, receive, send)
        limit = self.limit
        if path == '/api/v1/usage/prices':
            # PricesInput 允许 300 个条目，包含最长标识与十进制字符串；仍逐块限制实际字节。
            limit = 512 * 1024
        elif path.startswith('/api/v1/roleplay/life/instances/') and path.endswith('/materials/schedule'):
            limit = 256 * 1024
        elif path.startswith('/api/v1/models/connections') or path.startswith('/api/v1/models/presets'):
            limit = 3 * 1024 * 1024
        elif path.startswith('/api/v1/information/libraries/'):
            limit = 2 * 1024 * 1024
        if path == '/api/v1/roleplay/characters' or path.startswith('/api/v1/roleplay/characters/'):
            limit = 512 * 1024
        elif path == '/api/v1/roleplay/worldbooks' or path.startswith('/api/v1/roleplay/worldbooks/'):
            limit = 2 * 1024 * 1024
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > limit:
                response = JSONResponse({"detail": f"当前管理请求不能超过 {limit // 1024} KiB。"}, status_code=413)
                return await response(scope, receive, send)
            if not message.get("more_body", False):
                break
        delivered = False

        async def replay_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return await self.app(scope, replay_receive, send)
