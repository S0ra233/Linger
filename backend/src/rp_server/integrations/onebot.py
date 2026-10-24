"""OneBot v11 反向Universal连接；接收私聊事件并投递已校验计划。"""
import asyncio
from dataclasses import dataclass, field
import json
import logging
import math
import re
from time import time

from fastapi import WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from ..events import EventBus
from ..roleplay.inputs import InputEvent, InputService, qq_id
from .qq_accounts import QQAccounts


logger = logging.getLogger('rp_server.qq')
MAX_FRAME_BYTES = 1024 * 1024
HANDSHAKE_ERRORS = {
    'server_closing': 'RP正在关闭，请等待服务重新启动。',
    'query_not_supported': '连接地址含查询参数；请使用页面原始地址，令牌填入NapCat的Token字段。',
    'self_id_missing': '握手缺少X-Self-ID；请使用NapCat的WebSocket客户端／反向连接。',
    'self_id_invalid': '握手X-Self-ID不是有效QQ号。',
    'client_role_missing': '握手缺少X-Client-Role；请使用Universal反向连接。',
    'client_role_unsupported': '当前只支持Universal双向通道，不支持Event或API分离通道。',
    'account_not_found': '连接地址中的接入账号不存在；请重新复制QQ接入页地址，不要填QQ号代替账号编号。',
    'account_disabled': '此QQ接入账号已停用，请在QQ接入页启用。',
    'self_id_mismatch': '握手的机器人QQ与此接入账号不一致，请核对登录QQ和连接地址。',
    'authorization_missing': '握手没有Authorization；请把连接令牌填入此反向客户端的Token字段。',
    'authorization_format': '握手鉴权头格式无效，应为Bearer加连接令牌；NapCat的Token字段只填令牌本身。',
    'credential_unavailable': 'RP无法读取此账号的连接凭据，请检查服务器主密钥或重新保存账号令牌。',
    'token_mismatch': '此反向客户端发送的Token与RP保存值不一致；保存NapCat设置后请重新连接该客户端。',
    'already_connected': '此账号已有连接；请停用重复的反向客户端，或在QQ接入页断开旧连接后重连。',
}


def message_id(value: object) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('消息编号无效。')
    text = str(value)
    if not text.isascii() or not text.lstrip('-').isdigit() or text.count('-') > 1 or len(text) > 21:
        raise ValueError('消息编号无效。')
    return str(int(text))


def normalize_private(account_id: str, bot_id: str, payload: dict) -> InputEvent | None:
    """保留段顺序；不把 CQ 字符串猜成数组，也不展开未知扩展内容。"""
    if payload.get('post_type') != 'message' or payload.get('message_type') != 'private':
        return None
    if qq_id(payload.get('self_id')) != bot_id:
        raise ValueError('事件机器人 QQ 与连接配置不符。')
    peer = qq_id(payload.get('user_id'))
    if peer == bot_id:
        return None
    segments = payload.get('message')
    if not isinstance(segments, list):
        raise ValueError('请将 NapCat 的消息格式改为 array。')
    if not 1 <= len(segments) <= 100:
        raise ValueError('消息段数量须在 1 至 100 之间。')
    timestamp = payload.get('time')
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp) or not 0 <= timestamp <= 32503680000:
        raise ValueError('事件时间无效。')
    parts, text_size, image_count = [], 0, 0
    for segment in segments:
        if not isinstance(segment, dict) or not isinstance(segment.get('type'), str) or not isinstance(segment.get('data'), dict):
            raise ValueError('消息段结构无效。')
        kind, data = segment['type'], segment['data']
        if kind == 'text':
            text = data.get('text')
            if not isinstance(text, str):
                raise ValueError('文字段无效。')
            text_size += len(text.encode('utf-8'))
            parts.append({'type': 'text', 'text': text})
        elif kind == 'image':
            image_count += 1
            url = data.get('url')
            # file 往往是另一台机器的缓存路径，绝不能当作 RP 的文件路径。
            if not isinstance(url, str) or len(url) > 8192:
                url = ''
            part = {'type': 'image', 'url': url}
            market = {key: str(data[key]) for key in ('emoji_id', 'emoji_package_id', 'key')
                      if isinstance(data.get(key), (str, int)) and not isinstance(data[key], bool)
                      and 0 < len(str(data[key])) <= 2048}
            if len(market) == 3: part['market'] = market
            if type(data.get('sub_type')) is int: part['sub_type'] = data['sub_type']
            parts.append(part)
        elif kind == 'reply':
            parts.append({'type': 'reply', 'message_id': message_id(data.get('id'))})
        else:
            parts.append({'type': 'unsupported', 'kind': kind[:48]})
    if text_size > 60000 or image_count > 8:
        raise ValueError('单条消息文字最多 60000 字节，图片最多 8 张。')
    sender = payload.get('sender')
    nickname = sender.get('nickname', '') if isinstance(sender, dict) else ''
    return InputEvent(account_id, bot_id, peer, message_id(payload.get('message_id')), float(timestamp),
                      nickname[:80] if isinstance(nickname, str) else '', tuple(parts))


@dataclass
class GatewayConnection:
    websocket: WebSocket
    connected_at: float = field(default_factory=time)
    last_event_at: float = field(default_factory=time)
    last_error: str | None = None
    received: int = 0
    duplicates: int = 0
    accepting: bool = True
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: dict = field(default_factory=dict)


class OneBotGateway:
    def __init__(self, accounts: QQAccounts, inputs: InputService, events: EventBus, wake_images):
        self.accounts, self.inputs, self.events, self.wake_images = accounts, inputs, events, wake_images
        self.connections: dict[str, GatewayConnection] = {}
        self.last_status: dict[str, dict] = {}
        self.rejection_logs: dict[tuple[str, str], float] = {}
        self.closing = False
        self.on_receipt = None

    async def send_private(self, account_id: str, peer_id: str, text: str, echo: str, timeout: int, on_start):
        """文字或已校验消息段；收件人来自服务器实例，echo 为持久投递编号。"""
        connection = self.connections.get(account_id)
        unknown = {'status': 'unknown', 'error': '未取得明确回执，不会自动重发。'}
        not_started = {'status': 'not_started'}
        if connection is None or not connection.accepting:
            return not_started
        future = asyncio.get_running_loop().create_future()
        connection.pending[echo] = future
        started = False
        try:
            async with asyncio.timeout(timeout):
                async with connection.send_lock:
                    if not connection.accepting:
                        return not_started
                    # 等待同一连接发送锁后才检查版本并标记 sending，中间没有网络等待。
                    if not on_start():
                        return not_started
                    started = True
                    await connection.websocket.send_json({'action': 'send_private_msg', 'echo': echo,
                        'params': {'user_id': int(peer_id), 'message': [{'type': 'text', 'data': {'text': text}}] if isinstance(text, str) else text}})
                return await future
        except (TimeoutError, RuntimeError, OSError):
            return unknown if started else not_started
        finally:
            connection.pending.pop(echo, None)

    @staticmethod
    def parse_receipt(payload: dict):
        if payload.get('status') == 'ok' and type(payload.get('retcode')) is int and payload['retcode'] == 0:
            data = payload.get('data')
            try:
                result_id = message_id(data.get('message_id') if isinstance(data, dict) else None)
            except ValueError:
                return {'status': 'unknown', 'error': '成功回执缺少有效消息编号。'}
            return {'status': 'succeeded', 'message_id': result_id}
        if payload.get('status') == 'failed' and type(payload.get('retcode')) is int and payload['retcode'] not in {0, 1}:
            notes = []
            for key in ('wording', 'message'):
                value = payload.get(key)
                if not isinstance(value, str) or not value.strip():
                    continue
                value = re.sub(r'[\x00-\x1f\x7f]', ' ', value[:1000])
                value = re.sub(r'(?i)(?:https?|wss?)://\S+', '[地址已隐藏]', value)
                value = re.sub(r'(?i)Bearer\s+\S+', 'Bearer [已隐藏]', value)
                value = re.sub(r'''(?i)["']?(authorization|access_token|api[_-]?key|token|password|secret)["']?\s*[:=]\s*["']?[^\s,;，；]+''', r'\1=[已隐藏]', value).strip()[:300]
                if value not in notes:
                    notes.append(value)
            detail = ' ' + '；'.join(notes) if notes else ''
            return {'status': 'failed', 'error': f"网关明确拒绝，代码 {payload['retcode']}。{detail}"}
        if payload.get('status') == 'async' and payload.get('retcode') == 1:
            return {'status': 'unknown', 'error': '网关只确认异步排队，无法确认送达；不会自动重发。'}
        # 非标准组合不能冒认为失败或送达。
        return None

    def state(self, account_id: str):
        connection = self.connections.get(account_id)
        attempt = self.last_status.get(account_id, {})
        if connection is None:
            return {'connected': False, **attempt}
        return {**{key: value for key, value in attempt.items() if key.startswith('handshake_')},
                'connected': connection.accepting, 'connected_at': connection.connected_at,
                'last_event_at': connection.last_event_at, 'last_error': connection.last_error,
                'received': connection.received, 'duplicates': connection.duplicates}

    def _retire(self, account_id: str, connection: GatewayConnection):
        """先解除账号占用；关闭握手被取消也不能把失效连接留在表里。"""
        connection.accepting = False
        for future in connection.pending.values():
            if not future.done():
                future.set_result({'status': 'unknown', 'error': '网关连接中断，投递结果未知。'})
        # 旧连接退出时，不得删除已经接替它的新连接或覆盖其状态。
        if self.connections.get(account_id) is connection:
            del self.connections[account_id]
            self.last_status[account_id] = {**self.last_status.get(account_id, {}),
                'disconnected_at': time(), 'last_error': connection.last_error}
            self.events.publish('qq')

    async def disconnect(self, account_id: str):
        connection = self.connections.get(account_id)
        if connection:
            self._retire(account_id, connection)
            try:
                async with asyncio.timeout(3):
                    await connection.websocket.close(code=1001)
            except (RuntimeError, OSError, TimeoutError):
                pass

    async def close(self):
        self.closing = True
        await asyncio.gather(*(self.disconnect(key) for key in list(self.connections)), return_exceptions=True)
        self.on_receipt = None

    async def reject(self, websocket: WebSocket, account_id: str, code: str):
        """accept之前关闭仍是HTTP403；具体原因仅在本地日志和受保护管理页显示。"""
        # 路由参数来自未鉴权请求，不能原样写日志或无限扩张状态字典。
        known = bool(re.fullmatch(r'[0-9a-f]{32}', account_id)
                     and self.accounts.database.one('SELECT id FROM qq_accounts WHERE id=?', (account_id,)))
        label = account_id if known else 'unknown'
        message = HANDSHAKE_ERRORS[code]
        previous = self.last_status.get(account_id, {}) if known else {}
        now = time()
        if known:
            self.last_status[account_id] = {**previous, 'handshake_error': message,
                'handshake_error_code': code, 'handshake_failed_at': now}
        # NapCat会频繁重试；相同失败最多每30秒一次，避免日志及页面刷新风暴。
        log_key = (label, code)
        logged = now - self.rejection_logs.get(log_key, 0) >= 30
        if logged:
            logger.warning('OneBot握手拒绝，接入账号 %s，原因 %s：%s', label, code, message)
            self.rejection_logs[log_key] = now
        if known and (logged or code != previous.get('handshake_error_code')):
            self.events.publish('qq')
        await websocket.close(code=1008)

    async def serve(self, websocket: WebSocket, account_id: str):
        if self.closing:
            await self.reject(websocket, account_id, 'server_closing')
            return
        if websocket.query_params:
            await self.reject(websocket, account_id, 'query_not_supported')
            return
        self_id = websocket.headers.get('x-self-id', '').strip()
        if not self_id:
            await self.reject(websocket, account_id, 'self_id_missing')
            return
        try:
            bot_id = qq_id(self_id)
        except ValueError:
            await self.reject(websocket, account_id, 'self_id_invalid')
            return
        role = websocket.headers.get('x-client-role', '').strip().lower()
        if role != 'universal':
            await self.reject(websocket, account_id, 'client_role_unsupported' if role else 'client_role_missing')
            return
        error = self.accounts.authorization_error(account_id, bot_id, websocket.headers.get('authorization', ''))
        if error:
            await self.reject(websocket, account_id, error)
            return
        previous = self.connections.get(account_id)
        if previous:
            if previous.accepting and previous.websocket.client_state != WebSocketState.DISCONNECTED:
                await self.reject(websocket, account_id, 'already_connected')
                return
            self._retire(account_id, previous)
        connection = GatewayConnection(websocket)
        # 在第一次 await 前登记，防止两个连接同时通过重复检查。
        self.connections[account_id] = connection
        try:
            await websocket.accept()
            self.last_status.pop(account_id, None)
            self.events.publish('qq')
            while connection.accepting and not self.closing:
                raw = await websocket.receive_text()
                if not connection.accepting or self.closing:
                    break
                if len(raw.encode('utf-8')) > MAX_FRAME_BYTES:
                    connection.last_error = '事件超过 1 MiB 限额。'
                    await websocket.close(code=1009)
                    break
                payload = None
                try:
                    payload = json.loads(raw)
                    if not isinstance(payload, dict):
                        raise ValueError('网关事件必须是 JSON 对象。')
                    if 'self_id' in payload and qq_id(payload['self_id']) != bot_id:
                        raise ValueError('事件机器人 QQ 与连接配置不符。')
                    connection.last_event_at = time()
                    if 'echo' in payload and 'post_type' not in payload:
                        echo = payload['echo']
                        result = self.parse_receipt(payload)
                        if isinstance(echo, str) and len(echo) == 32 and result is not None:
                            # 即使请求已超时，也允许对应账号的迟到回执消除 unknown。
                            if self.on_receipt:
                                self.on_receipt(account_id, echo, result)
                            future = connection.pending.get(echo)
                            if future is not None and not future.done():
                                future.set_result(result)
                        continue
                    event = normalize_private(account_id, bot_id, payload)
                    if event:
                        outcome = self.inputs.ingest(event)
                        if outcome == 'received':
                            connection.received += 1
                            self.wake_images()
                        elif outcome == 'duplicate':
                            connection.duplicates += 1
                        elif outcome == 'contact_limit':
                            connection.last_error = '该账号联系人数量已达上限。'
                    # 心跳不触发管理端完整刷新，刷新按钮可读取最近心跳时间。
                except (ValueError, TypeError, OverflowError, RecursionError):
                    # 不回显 URL、消息正文或令牌；固定诊断说明对应可操作原因。
                    connection.last_error = ('请将 NapCat 的消息格式改为 array。' if isinstance(payload, dict)
                                             and payload.get('post_type') == 'message'
                                             and payload.get('message_type') == 'private'
                                             and not isinstance(payload.get('message'), list)
                                             else '事件格式、身份或大小无效；请检查 OneBot 配置。')
                    self.events.publish('qq')
        except WebSocketDisconnect:
            pass
        except (RuntimeError, OSError):
            connection.last_error = '网关连接已中断。'
        except Exception as exc:
            connection.last_error = '消息接收失败，连接已关闭；未确认保存的输入不能视为已接收。'
            logger.error('QQ 接收中断，账号 %s，异常类型 %s', account_id, type(exc).__name__)
        finally:
            self._retire(account_id, connection)
            try:
                async with asyncio.timeout(3):
                    await websocket.close(code=1011 if connection.last_error else 1000)
            except (RuntimeError, OSError, TimeoutError):
                pass
