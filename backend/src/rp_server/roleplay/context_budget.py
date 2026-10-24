"""用同线同模型的真实输入量作预算参照；不改变请求，不将总量伪装为分项实测。"""
from collections import OrderedDict
from dataclasses import asdict, dataclass
import hashlib
import json
import math

from ..llm.messages import ModelMessage, TextContent, ToolResult
from ..llm.history_projection import same_origin
from ..llm.models import ModelRequest
from ..llm.tool_policy import ToolChoice
from .budget import estimate_message_tokens, estimate_request_tokens


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class ContinuationAnchor:
    instance_id: str
    run_id: int
    step_id: int
    signature: str
    messages: tuple
    actual: int
    raw: int


def plain_replay(message, config):
    """只识别现有Provider的文字／function回放；未知块不按纯文字外推。"""
    state = message.replay_state
    if not same_origin(state, config):
        return False
    payload = state.payload
    calls = message.tool_calls
    if state.api_style == 'chat_completions' and set(payload) == {'message'}:
        native = payload['message']
        if (not isinstance(native, dict) or native.get('role') != 'assistant'
                or set(native) - {'role', 'content', 'tool_calls', 'reasoning_content', 'reasoning', 'reasoning_details'}
                or native.get('content') is not None and not isinstance(native['content'], str)
                or (native.get('content') or '') != message.text
                or any(native.get(key) is not None and not isinstance(native[key], str) for key in ('reasoning_content', 'reasoning'))
                or native.get('reasoning_details') not in (None, [])):
            return False
        expected = [{'id': call.call_id, 'type': 'function',
                     'function': {'name': call.name, 'arguments': call.arguments}} for call in calls]
        return (native.get('tool_calls') or []) == expected
    text, found = [], []
    if state.api_style == 'responses' and set(payload) == {'output'} and isinstance(payload['output'], list):
        for item in payload['output']:
            if not isinstance(item, dict):
                return False
            if item.get('type') == 'function_call' and not set(item) - {'type', 'id', 'call_id', 'name', 'arguments', 'status'}:
                found.append((item.get('call_id'), item.get('name'), item.get('arguments')))
            elif (item.get('type') == 'message' and item.get('role') == 'assistant'
                    and not set(item) - {'type', 'id', 'role', 'status', 'content'} and isinstance(item.get('content'), list)):
                for block in item['content']:
                    if (not isinstance(block, dict) or block.get('type') != 'output_text'
                            or not isinstance(block.get('text'), str) or set(block) - {'type', 'text', 'annotations', 'logprobs'}
                            or block.get('annotations') not in (None, []) or block.get('logprobs') not in (None, [])):
                        return False
                    text.append(block['text'])
            else:
                return False
        return ''.join(text) == message.text and found == [(call.call_id, call.name, call.arguments) for call in calls]
    # 这两种协议原生工具参数是对象；不能把JSON解码失败当作可计量的对象参数。
    try:
        expected = [(call.call_id, call.name, json.loads(call.arguments)) for call in calls]
    except (ValueError, RecursionError):
        return False
    if state.api_style == 'anthropic_messages' and set(payload) == {'content'} and isinstance(payload['content'], list):
        for block in payload['content']:
            if not isinstance(block, dict):
                return False
            if block.get('type') == 'text' and set(block) <= {'type', 'text'} and isinstance(block.get('text'), str):
                text.append(block['text'])
            elif (block.get('type') == 'tool_use' and not set(block) - {'type', 'id', 'name', 'input'}
                    and isinstance(block.get('input'), dict)):
                found.append((block.get('id'), block.get('name'), block['input']))
            else:
                return False
    elif state.api_style == 'gemini' and set(payload) == {'content', 'call_ids'}:
        content, identifiers = payload['content'], payload['call_ids']
        if (not isinstance(content, dict) or set(content) != {'role', 'parts'} or content['role'] != 'model'
                or not isinstance(content['parts'], list) or not isinstance(identifiers, dict)
                or set(identifiers) != {call.call_id for call in calls}):
            return False
        functions = []
        for block in content['parts']:
            if not isinstance(block, dict):
                return False
            if set(block) == {'text'} and isinstance(block['text'], str):
                text.append(block['text'])
            elif set(block) == {'functionCall'} and isinstance(block['functionCall'], dict):
                function = block['functionCall']
                if set(function) - {'id', 'name', 'args'} or not isinstance(function.get('args', {}), dict):
                    return False
                functions.append(function)
            else:
                return False
        if len(functions) != len(calls):
            return False
        for call, function in zip(calls, functions):
            if identifiers[call.call_id] != function.get('id'):
                return False
            found.append((call.call_id, function.get('name'), function.get('args', {})))
    else:
        return False
    return ''.join(text) == message.text and found == expected


class ContextBudget:
    def __init__(self, store):
        self.database, self.events = store.database, store.events
        self._cache = OrderedDict()

    @staticmethod
    def signature(instance_id, connection_id, revision, parameters, request):
        # 指定工具是协议对象，先转成稳定数据，不能让预算记录阻断有效响应。
        choice = asdict(request.tool_choice) if isinstance(request.tool_choice, ToolChoice) else request.tool_choice
        return fingerprint([instance_id, connection_id, revision, parameters,
                            [asdict(tool) for tool in request.tools], choice,
                            request.reasoning_effort, request.temperature, request.max_output_tokens,
                            request.output_format, request.output_schema,
                            request.runtime_context_id, [asdict(item) for item in request.runtime_context_updates]])

    @staticmethod
    def continuation_messages(request, snapshot):
        """只接受文字续接及可解释的原生回放，不展开图片或猜测签名块用量。"""
        if request.runtime_context_id or request.runtime_context_updates:
            return None
        config, result = snapshot.config(), []
        for message in request.messages:
            if isinstance(message, ToolResult):
                value = asdict(message)
            elif isinstance(message, ModelMessage):
                if (message.skills or message.question_reply
                        or any(not isinstance(part, TextContent) for part in message.content)):
                    return None
                value = {'role': message.role, 'content': [asdict(part) for part in message.content],
                         'tool_calls': [asdict(call) for call in message.tool_calls]}
                if message.replay_state is not None:
                    if not plain_replay(message, config):
                        return None
                    # 文字推理若需原样回传，也完整计入后缀；只存哈希，不另存正文。
                    value['replay'] = message.replay_state.payload
            else:
                return None
            result.append((fingerprint(value), estimate_message_tokens(message)))
        return tuple(result)

    def capture_continuation(self, request, snapshot, instance_id, run_id, step_id, usage):
        if usage is None or type(usage.input_tokens) is not int or usage.input_tokens <= 0:
            return None
        messages = self.continuation_messages(request, snapshot)
        raw = estimate_request_tokens(request)
        if messages is None or usage.input_tokens < raw / 16:
            return None
        return ContinuationAnchor(instance_id, run_id, step_id,
            self.signature(instance_id, snapshot.connection_id, snapshot.revision, snapshot.model.model_dump(), request),
            messages, usage.input_tokens, raw)

    def estimate_continuation(self, request, snapshot, instance_id, run_id, raw, anchor):
        fallback = {'method': 'coarse', 'samples': 0, 'reason': '本轮续接的来源、参数或消息前缀不相容，沿用粗估'}
        if (anchor.instance_id != instance_id or anchor.run_id != run_id
                or anchor.signature != self.signature(instance_id, snapshot.connection_id, snapshot.revision, snapshot.model.model_dump(), request)):
            return raw, fallback
        messages = self.continuation_messages(request, snapshot)
        if messages is None or messages[:len(anchor.messages)] != anchor.messages:
            return raw, {**fallback, 'reason': '续接含图片、签名或未识别回放，或已发送消息发生变化，沿用粗估'}
        if anchor.actual > anchor.raw:
            return raw, {**fallback, 'reason': '本轮实际输入曾超过粗估，暂停缩减预算'}
        suffix = sum(tokens for _, tokens in messages[len(anchor.messages):])
        margin = max(512, math.ceil(anchor.actual * .1))
        value = anchor.actual + suffix + margin
        if value >= raw:
            return raw, {**fallback, 'reason': '本轮续接校准没有缩减空间，沿用粗估'}
        return value, {'method': 'continuation', 'samples': 1, 'anchor_step_id': anchor.step_id,
                       'anchor_input_tokens': anchor.actual, 'shared_messages': len(anchor.messages),
                       'suffix_tokens': suffix, 'margin_tokens': margin}

    @staticmethod
    def messages(request):
        # 图像、工具续接及厂商私有回放的计数不同，不用纯文字样本外推。
        result = []
        for message in request.messages:
            if (not isinstance(message, ModelMessage) or message.tool_calls or message.replay_state
                    or message.skills or message.question_reply
                    or any(not isinstance(part, TextContent) for part in message.content)):
                return None
            result.append((fingerprint([message.role, [asdict(part) for part in message.content]]),
                           estimate_message_tokens(message)))
        return result

    def anchors(self, instance_id, snapshot, signature):
        marker = (self.database.one("SELECT coalesce(max(id),0) AS id FROM rp_steps WHERE status='returned'")['id'],
                  self.events.revisions(('messages', 'context', 'information', 'models')))
        cached = self._cache.get(signature)
        if cached and cached[0] == marker:
            self._cache.move_to_end(signature)
            return cached[1]
        rows = self.database.all('''SELECT s.id,s.request_json,
            json_extract(s.response_json,'$.token_usage.input_tokens') AS actual,
            json_extract(r.model_json,'$.parameters') AS parameters
            FROM rp_steps s JOIN rp_runs r ON r.id=s.run_id
            WHERE r.instance_id=? AND r.context_deleted_at IS NULL AND r.context_archived_at IS NULL AND s.step=1 AND s.status='returned'
            AND json_extract(r.model_json,'$.connection_id')=?
            AND json_extract(r.model_json,'$.connection_revision')=?
            AND json_extract(r.model_json,'$.model_id')=?
            AND json_type(s.response_json,'$.token_usage.input_tokens')='integer'
            AND json_extract(s.response_json,'$.token_usage.input_tokens')>0 ORDER BY s.id DESC LIMIT 24''',
            (instance_id, snapshot.connection_id, snapshot.revision, snapshot.model.id))
        result = []
        for row in rows:
            try:
                request = ModelRequest(**json.loads(row['request_json']))
                parameters = json.loads(row['parameters'])
                if self.signature(instance_id, snapshot.connection_id, snapshot.revision, parameters, request) != signature:
                    continue
                messages = self.messages(request)
                if messages is None:
                    continue
                raw = estimate_request_tokens(request)
                # 极低用量不参与外推；超过粗估的样本保留，用于阻止缩减预算。
                if row['actual'] < raw / 16:
                    continue
                result.append({'step_id': row['id'], 'actual': row['actual'], 'raw': raw, 'messages': messages})
                if len(result) == 8:
                    break
            except (ValueError, TypeError, KeyError, RecursionError):
                # 旧记录不兼容时跳过，不让统计资料损坏阻断正常对话。
                continue
        self._cache[signature] = (marker, result)
        self._cache.move_to_end(signature)
        while len(self._cache) > 8:
            self._cache.popitem(last=False)
        return result

    def estimate(self, request, snapshot, instance_id, raw, *, run_id=None, continuation=None):
        fallback = {'method': 'coarse', 'samples': 0, 'reason': '尚无足够相近的真实请求'}
        if continuation is not None:
            return self.estimate_continuation(request, snapshot, instance_id, run_id, raw, continuation)
        if instance_id is None:
            return raw, fallback
        messages = self.messages(request)
        if messages is None:
            return raw, {**fallback, 'reason': '图片、私有回放或缺少相容本轮用量的工具续接使用原始粗估'}
        signature = self.signature(instance_id, snapshot.connection_id, snapshot.revision,
                                   snapshot.model.model_dump(), request)
        anchors = self.anchors(instance_id, snapshot, signature)
        candidates = []
        tools = raw - sum(tokens for _, tokens in messages)
        for anchor in anchors:
            shared = 0
            for old, current in zip(anchor['messages'], messages):
                if old[0] != current[0]:
                    break
                shared += 1
            covered = tools + sum(tokens for _, tokens in messages[:shared])
            if not shared or covered < raw * .75:
                continue
            if anchor['actual'] > anchor['raw']:
                return raw, {**fallback, 'reason': '相近请求的实际输入曾超过粗估，暂停缩减预算'}
            suffix = sum(tokens for _, tokens in messages[shared:])
            margin = max(512, math.ceil(anchor['actual'] * .1))
            # 不猜匹配前缀的真实分词量：沿用整份旧请求的实际量，再加完整新后缀及余量。
            value = anchor['actual'] + suffix + margin
            candidates.append((value, anchor, shared, margin))
        if len(candidates) < 3:
            return raw, {**fallback, 'samples': len(candidates)}
        candidates.sort(key=lambda item: item[0])
        value, anchor, shared, margin = candidates[2]
        if value >= raw:
            return raw, {**fallback, 'samples': len(candidates), 'reason': '校准没有缩减空间，沿用粗估'}
        return value, {'method': 'anchored', 'samples': len(candidates), 'anchor_step_id': anchor['step_id'],
                       'anchor_input_tokens': anchor['actual'], 'shared_messages': shared, 'margin_tokens': margin}
