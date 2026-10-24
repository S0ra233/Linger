"""管理员手动触发的隔离格式检查：只构造和解析，不执行返回的工具。"""
import json

from pydantic import ValidationError

from ..llm.messages import ModelMessage, TextContent, ToolSchema
from .decisions import RpDecision
from .plan_contract import prune_definitions, parse_json_plan, parse_tool_arguments
from ..llm.tool_policy import apply_rp_tool_policy, uses_json_plan


def probe_request(snapshot):
    # 从真实协议取最小子集，不带角色、私聊、状态、MCP或管理员提示词。
    schema = RpDecision.model_json_schema()
    schema['properties'] = {key: schema['properties'][key] for key in ('reply_mode', 'messages')}
    schema['required'] = ['reply_mode', 'messages']
    schema['properties']['reply_mode'] = {'type': 'string', 'enum': ['reply']}
    schema['properties']['messages'].update(minItems=1, maxItems=1)
    schema['$defs']['Bubble'].update(properties={'text': {'type': 'string', 'enum': ['OK']}}, required=['text'])
    tool = ToolSchema('submit_rp_plan', '格式检查：只提交reply及一个text为OK的气泡；不会执行或发送。', prune_definitions(schema))
    messages = [ModelMessage('system', [TextContent('这是隔离的工具格式检查。只通过接口原生工具调用一次submit_rp_plan，参数为{"reply_mode":"reply","messages":[{"text":"OK"}]}。不输出普通正文，不添加其他字段或工具；结果不会执行。')]),
        ModelMessage('user', [TextContent('请提交上述最小格式。')])]
    if uses_json_plan(snapshot.config()):
        schema['properties'].update({key: {'type': 'array', 'items': {'type': 'null'}, 'maxItems': 0}
                                     for key in ('followups', 'deliveries')})
        schema['required'] += ['followups', 'deliveries']
        messages = [ModelMessage('system', [TextContent('这是隔离的JSON计划格式检查。只返回完整JSON：'
            '{"reply_mode":"reply","messages":[{"text":"OK"}],"followups":[],"deliveries":[]}。'
            '不输出围栏、其他字段或思考，不调用工具；结果不会执行或发送。')]),
            ModelMessage('user', [TextContent('请返回上述最小JSON计划。')])]
        request = snapshot.request(messages)
    else:
        request = snapshot.request(messages, [tool])
    apply_rp_tool_policy(snapshot.config(), request, repair=False, schema=schema)
    return request


def inspect_probe(response, *, json_plan=False):
    issues = []
    if json_plan:
        if response.completion_status != 'completed' or response.tool_calls:
            issues.append({'path': 'completion_status', 'message': '需要完整JSON计划，不接受工具调用或未完成输出。'})
        fields = ('reply_mode', 'messages', 'followups', 'deliveries')
        try:
            raw = parse_json_plan(response.text, {'properties': dict.fromkeys(fields), 'required': fields})
            if raw['reply_mode'] != 'reply' or raw['messages'] != [{'text': 'OK'}] or raw['followups'] != [] or raw['deliveries'] != []:
                raise ValueError('应返回一个OK气泡，followups和deliveries必须为[]。')
            RpDecision.model_validate(raw)
        except ValidationError as exc:
            issues.extend({'path': '.'.join(map(str, item['loc'])), 'message': item['msg']}
                          for item in exc.errors(include_input=False)[:12])
        except (ValueError, RecursionError) as exc:
            issues.append({'path': 'json_plan', 'message': 'JSON计划无效或嵌套过深。' if isinstance(exc, (json.JSONDecodeError, RecursionError)) else str(exc)})
        return {'scope': 'minimal_plan', 'output': 'json', 'passed': not issues, 'issues': issues, 'returned_tools': []}
    if response.completion_status != 'requires_tool':
        issues.append({'path': 'completion_status', 'message': '需要完整结束的原生工具调用；普通正文JSON不算工具调用。'})
    if response.text.strip():
        issues.append({'path': 'text', 'message': '本次固定检查不应附带普通正文。'})
    if len(response.tool_calls) != 1:
        issues.append({'path': 'tool_calls', 'message': '应只返回一次submit_rp_plan。'})
    previews = []
    for call in response.tool_calls[:4]:
        previews.append({'name': call.name, 'call_id': call.call_id, 'arguments': call.arguments[:16000],
                         'arguments_truncated': len(call.arguments) > 16000})
        if call.name != 'submit_rp_plan' or not call.call_id.strip() or len(call.call_id) > 256:
            issues.append({'path': 'tool_calls', 'message': '工具名称或调用编号不符合本次声明。'})
            continue
        if len(call.arguments.encode('utf-8')) > 64000:
            issues.append({'path': 'arguments', 'message': '参数超过64KB，未解析。'})
            continue
        try:
            raw = parse_tool_arguments(call.arguments)
            if not isinstance(raw, dict) or set(raw) != {'reply_mode', 'messages'}:
                raise ValueError('顶层只能有reply_mode和messages。')
            if not isinstance(raw['messages'], list) or len(raw['messages']) != 1 or raw['messages'][0] != {'text': 'OK'}:
                raise ValueError('messages必须只含一个{text:"OK"}对象。')
            decision = RpDecision.model_validate(raw)
            if decision.reply_mode != 'reply':
                raise ValueError('reply_mode必须为reply。')
        except ValidationError as exc:
            issues.extend({'path': '.'.join(map(str, item['loc'])), 'message': item['msg']}
                          for item in exc.errors(include_input=False)[:12])
        except (ValueError, RecursionError) as exc:
            issues.append({'path': 'arguments', 'message': '参数JSON无效或嵌套过深。' if isinstance(exc, (json.JSONDecodeError, RecursionError)) else str(exc)})
    return {'scope': 'minimal_plan', 'passed': not issues, 'issues': issues, 'returned_tools': previews}
