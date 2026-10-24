"""提取自自有 Harness 的粗估预算；不是厂商 tokenizer 或账单。"""
import json
import math
from dataclasses import asdict

from ..llm.messages import ImageContent, Message, ModelMessage
from ..llm.models import ModelRequest


def effective_window(model_window: int | None, configured_window: int | None = None) -> int | None:
    """会话预算不能扩大已知模型窗口；未知容量不由模型名字猜测。"""
    known = [value for value in (model_window, configured_window) if value is not None]
    return min(known) if known else None


def _estimate(value: object) -> int:
    """每两个 UTF-8 字节粗估一个 token；原生续接数据也计入，不保证精确。"""
    content = json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return math.ceil(len(content.encode('utf-8')) / 2)


def estimate_message_tokens(message: Message) -> int:
    data = message.replay_state.payload if isinstance(message, ModelMessage) and message.replay_state is not None else asdict(message)
    data = dict(data)
    image_count = 0
    if isinstance(message, ModelMessage):
        for field in ('runtime_context_id', 'runtime_context_kind', 'skills', 'question_reply', 'context_layer', 'context_metadata'):
            data.pop(field, None)
        image_count = sum(isinstance(part, ImageContent) for part in message.content)
        if message.replay_state is None:
            data.pop('replay_state', None)
            data['content'] = [part for part in data['content'] if part['type'] != 'image']
    # 每图 4096 是估算策略，不是实际用量或可靠上界。
    return _estimate(data) + 8 + image_count * 4096


def estimate_request_tokens(request: ModelRequest) -> int:
    """测量准备发送的资料、消息和工具声明，不累计过去的请求用量。"""
    return sum(estimate_message_tokens(message) for message in request.messages) + _estimate([
        asdict(tool) for tool in request.tools
    ]) + (_estimate(request.output_schema) if request.output_schema is not None else 0) + 16
