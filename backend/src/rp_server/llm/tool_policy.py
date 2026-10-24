"""工具控制与严格参数投影；不执行工具，也不改变角色正文。"""
from copy import deepcopy
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import ModelConfig, ModelRequest

TOOL_POLICY_VERSION = 6


def uses_json_plan(config: 'ModelConfig') -> bool:
    """模型级选择优先；旧配置继续沿用原提供商默认，不按模型名猜测。"""
    selected = config.tool_policy.plan_output
    return selected == 'json' or (selected == 'system' and config.provider_kind == 'deepseek'
                                 and config.api_style in {'chat_completions', 'responses'})


@dataclass(frozen=True)
class ToolPolicy:
    normal_mode: str = 'system'
    repair_mode: str = 'system'
    force_with_thinking: bool = False
    schema_mode: str = 'compatible'
    parallel_mode: str = 'default'
    plan_output: str = 'system'

    def __post_init__(self):
        for value, choices in [(self.normal_mode, {'system', 'auto', 'required'}),
                               (self.repair_mode, {'system', 'prompt', 'named', 'allowed'}),
                               (self.schema_mode, {'compatible', 'strict'}),
                               (self.parallel_mode, {'default', 'serial'})]:
            if not isinstance(value, str) or value not in choices:
                raise ValueError('工具策略选项无效。')
        if type(self.force_with_thinking) is not bool:
            raise ValueError('思考模式强制工具能力须为布尔值。')
        if not isinstance(self.plan_output, str) or self.plan_output not in {'system', 'tool', 'json'}:
            raise ValueError('计划输出须为system、tool或json。')

    def validate(self, protocol: str):
        if protocol == 'anthropic_messages' and self.schema_mode != 'compatible':
            raise ValueError('Messages当前仅使用兼容参数结构；工具选择按实际提供商与思考能力处理。')
        if self.repair_mode == 'allowed' and protocol not in {'responses', 'gemini'}:
            raise ValueError('工具允许名单仅对Responses和Gemini开放；Chat可选择指定工具。')
        if self.parallel_mode == 'serial' and protocol not in {'chat_completions', 'responses'}:
            raise ValueError('单次单工具控制仅适用于Chat或Responses。')


@dataclass(frozen=True)
class ToolChoice:
    mode: str
    names: tuple[str, ...]

    def __post_init__(self):
        if any(not isinstance(name, str) or not name.strip() for name in self.names):
            raise ValueError('工具名称不能为空。')
        if self.mode not in {'named', 'allowed'} or not self.names or len(set(self.names)) != len(self.names):
            raise ValueError('工具选择需要有效模式及不重复的名称。')
        if self.mode == 'named' and len(self.names) != 1:
            raise ValueError('指定工具只能包含一个名称。')


def thinking_state(config: 'ModelConfig', request: 'ModelRequest') -> bool | None:
    from .providers.reasoning import reasoning_fields
    fields = reasoning_fields(config, request)
    thinking = fields.get('thinking') or {}
    if thinking.get('type') == 'disabled': return False
    if thinking.get('type') in {'enabled', 'adaptive', 'between_tools'}: return True
    effort = fields.get('reasoning_effort') or (fields.get('reasoning') or {}).get('effort')
    if effort is not None: return effort != 'none'
    gemini = fields.get('thinkingConfig') or {}
    if 'thinkingBudget' in gemini: return gemini['thinkingBudget'] != 0
    if gemini.get('thinkingLevel') is not None: return True
    return config.reasoning.default_thinking if config.reasoning else None


def forced_tool_limitation(config: 'ModelConfig', request: 'ModelRequest', *, choice: str | ToolChoice, automatic=False) -> str | None:
    """区分接口语法和已知能力，不按模型名猜测，也不以关闭思考换强制调用。"""
    protocol, provider = config.api_style, config.provider_kind
    active = thinking_state(config, request)
    if provider == 'deepseek' and active is not False:
        if protocol in {'chat_completions', 'responses'}:
            return 'DeepSeek此协议的思考模式不支持强制或指定工具；保留思考并使用兼容引导。'
        if protocol == 'anthropic_messages' and isinstance(choice, ToolChoice):
            return 'DeepSeek Messages思考模式支持必须调用，但不支持指定工具；保留思考和必调约束。'
    if protocol == 'anthropic_messages' and provider != 'deepseek':
        if active is not False:
            return '此Messages思考组合可能拒绝强制工具或跳过思考；保留原思考并使用兼容引导。'
        if automatic and not config.tool_policy.force_with_thinking:
            return 'Messages强制工具存在模型差异，未明确确认当前模型支持，使用兼容引导。'
    known = ((provider == 'deepseek' and protocol in {'responses', 'anthropic_messages'})
             or (provider == 'deepseek' and protocol == 'chat_completions' and active is False)
             or (provider == 'openai' and protocol in {'chat_completions', 'responses'})
             or (provider == 'google' and protocol == 'gemini'))
    if not known and not config.tool_policy.force_with_thinking and (automatic or active is not False):
        return '兼容端点未确认原生强制工具能力，使用兼容引导；可在模型工具策略中明确配置。'
    return None


def apply_rp_tool_policy(config: 'ModelConfig', request: 'ModelRequest', *, repair: bool, schema=None) -> dict:
    """只设置选择字段；普通与修复请求保留相同工具定义和思考配置。"""
    policy = config.tool_policy
    requested = policy.repair_mode if repair else policy.normal_mode
    if uses_json_plan(config):
        from .structured_output import json_plan_format, plan_output_schema, output_controls
        # 查询工具仍是原生调用；最终计划由JSON正文提交，不再强选终结工具。
        request.output_format = json_plan_format(config)
        request.output_schema = plan_output_schema(schema) if request.output_format == 'json_schema' else None
        request.tool_choice = 'auto' if request.tools else None
        trace = {'version': TOOL_POLICY_VERSION, 'stage': 'repair' if repair else 'normal',
                 'requested': requested, 'effective': 'auto', 'plan_output': 'json',
                 'plan_output_source': policy.plan_output, 'json_format': request.output_format,
                 'schema_mode': policy.schema_mode, 'parallel_mode': policy.parallel_mode,
                 'reason': '查询按需调用原生工具；最终返回完整JSON计划，经业务校验后提交。',
                 'source': 'system' if policy.plan_output == 'system' else 'custom',
                 'native': {**native_tool_control(config, request), **output_controls(config, request)}}
        request.tool_policy_trace = trace
        return trace
    choice = None
    if repair and requested in {'system', 'named', 'allowed'}:
        choice = ToolChoice('named' if requested == 'system' else requested, ('submit_rp_plan',))
    elif not repair and requested in {'system', 'required'}:
        choice = 'required'
    reason = None
    if choice is not None:
        reason = forced_tool_limitation(config, request, choice=choice, automatic=requested == 'system')
        if reason:
            # Messages已确认any可与思考共用；不能因指定工具受限而一并丢掉必调能力。
            choice = ('required' if config.provider_kind == 'deepseek' and config.api_style == 'anthropic_messages'
                      and isinstance(choice, ToolChoice) else None)
    if isinstance(choice, ToolChoice) and any(name not in {t.name for t in request.tools} for name in choice.names):
        raise ValueError('修复工具不在本次授权工具中。')
    request.tool_choice = choice
    trace = {'version': TOOL_POLICY_VERSION, 'stage': 'repair' if repair else 'normal', 'requested': requested, 'plan_output': 'tool',
             'plan_output_source': policy.plan_output,
             'effective': choice.mode if isinstance(choice, ToolChoice) else choice or 'auto',
             'schema_mode': policy.schema_mode, 'parallel_mode': policy.parallel_mode, 'reason': reason,
             'source': 'system' if requested == 'system' else 'custom',
             'native': native_tool_control(config, request)}
    request.tool_policy_trace = trace
    return trace


def native_tool_control(config: 'ModelConfig', request: 'ModelRequest') -> dict:
    """Provider共用调用意图映射，不对模型名称作推测。"""
    choice, protocol, result = request.tool_choice, config.api_style, {}
    if not request.tools: return result
    if choice == 'required' or isinstance(choice, ToolChoice):
        limitation = forced_tool_limitation(config, request, choice=choice)
        if limitation:
            raise ValueError(limitation)
    if config.tool_policy.parallel_mode == 'serial':
        result['parallel_tool_calls'] = False
    if protocol == 'gemini':
        mode = {'none': 'NONE', 'auto': 'AUTO', 'required': 'ANY'}.get(choice) if isinstance(choice, str) else None
        names = list(choice.names) if isinstance(choice, ToolChoice) else None
        if names: mode = 'ANY'
        if mode is None and config.tool_policy.schema_mode == 'strict': mode = 'VALIDATED'
        if mode:
            control = {'mode': mode}
            if names: control['allowedFunctionNames'] = names
            result['toolConfig'] = {'functionCallingConfig': control}
    elif protocol in {'chat_completions', 'responses'}:
        if isinstance(choice, ToolChoice):
            if choice.mode == 'allowed':
                if protocol != 'responses': raise ValueError('当前协议没有启用工具允许名单映射。')
                result['tool_choice'] = {'type': 'allowed_tools', 'mode': 'required',
                                         'tools': [{'type': 'function', 'name': name} for name in choice.names]}
            else:
                result['tool_choice'] = ({'type': 'function', 'function': {'name': choice.names[0]}}
                                         if protocol == 'chat_completions' else {'type': 'function', 'name': choice.names[0]})
        elif choice is not None: result['tool_choice'] = choice
    elif protocol == 'anthropic_messages' and choice is not None:
        if isinstance(choice, ToolChoice):
            if choice.mode != 'named':
                raise ValueError('Messages仅支持指定单个工具，不支持工具允许名单。')
            result['tool_choice'] = {'type': 'tool', 'name': choice.names[0]}
        else:
            result['tool_choice'] = {'type': 'any' if choice == 'required' else choice}
    return result


def _resolve(node: dict, root: dict) -> dict:
    seen = set()
    while '$ref' in node:
        reference = node['$ref']
        if not isinstance(reference, str) or not reference.startswith('#/$defs/'):
            raise ValueError('严格工具Schema只支持本地$defs引用。')
        if reference in seen: raise ValueError('工具Schema存在循环别名。')
        seen.add(reference)
        node = root.get('$defs', {}).get(reference.removeprefix('#/$defs/'))
        if not isinstance(node, dict): raise ValueError('工具Schema引用不存在。')
    return node


def _allows_null(node: dict, root: dict, depth=0) -> bool:
    if depth > 40: raise ValueError('工具Schema联合结构过深。')
    node = _resolve(node, root)
    kind = node.get('type')
    return kind == 'null' or isinstance(kind, list) and 'null' in kind or any(
        _allows_null(item, root, depth + 1) for item in node.get('anyOf', []))


def strict_parameters(schema: dict, *, optional_defaults=False) -> dict:
    """稳定生成严格结构；动态字典投影成键值项，业务约束仍由原模型验证。"""
    def visit(node, depth=0):
        if depth > 40 or not isinstance(node, dict):
            raise ValueError('工具Schema结构过深或不是对象。')
        if any(key in node for key in ('allOf', 'oneOf', 'not', 'if', 'then', 'else', 'prefixItems')):
            raise ValueError('当前严格工具映射不支持此Schema组合，请使用兼容模式。')
        if isinstance(node.get('type'), list):
            if not node['type'] or any(not isinstance(kind, str) for kind in node['type']):
                raise ValueError('工具Schema的联合类型无效。')
            return {'anyOf': [visit({**node, 'type': kind}, depth + 1) for kind in node['type']]}
        if '$ref' in node:
            _resolve(node, schema)
            return {'$ref': node['$ref']}
        result = {key: deepcopy(node[key]) for key in ('type', 'description', 'enum') if key in node}
        if 'const' in node: result['enum'] = [node['const']]
        if 'anyOf' in node: result['anyOf'] = [visit(item, depth + 1) for item in node['anyOf']]
        if node.get('type') == 'object':
            properties, extra = node.get('properties', {}), node.get('additionalProperties', False)
            if isinstance(extra, dict):
                if properties: raise ValueError('严格映射不支持固定字段与动态字段混合的对象。')
                return {'type': 'array', 'description': node.get('description', '') + ' 以key/value项表示字典，key不得重复；无条目用空数组。',
                        'items': {'type': 'object', 'properties': {'key': {'type': 'string'}, 'value': visit(extra, depth + 1)},
                                  'required': ['key', 'value'], 'additionalProperties': False}}
            if extra is not False: raise ValueError('无类型的动态对象不能使用严格模式。')
            required = node.get('required', [])
            projected = {}
            for name, child in properties.items():
                value = visit(child, depth + 1)
                # 原来可省略的字段用null表示缺省，解码时恢复为缺省而非编造业务值。
                if name not in required and not _allows_null(child, schema):
                    has_default = 'default' in child and child['default'] is not None
                    empty_collection = child.get('type') in ('array', 'object') and not child.get('minItems') and not child.get('minProperties')
                    if optional_defaults and (has_default or empty_collection):
                        default = child.get('default', [] if child.get('type') == 'array' or isinstance(child.get('additionalProperties'), dict) else {})
                        value['description'] = ' '.join(filter(None, (value.get('description'),
                            '未使用时填写默认值：' + json.dumps(default, ensure_ascii=False, separators=(',', ':')) + '。')))
                    else:
                        value = {'anyOf': [value, {'type': 'null'}]}
                projected[name] = value
            result.update(properties=projected, required=list(properties), additionalProperties=False)
        elif node.get('type') == 'array':
            if not isinstance(node.get('items'), dict): raise ValueError('严格数组必须有确定的元素类型。')
            result['items'] = visit(node['items'], depth + 1)
        elif 'anyOf' not in node and node.get('type') not in {'string', 'integer', 'number', 'boolean', 'null'}:
            raise ValueError('严格工具参数缺少支持的明确类型。')
        return result
    result = visit(schema)
    if result.get('type') != 'object': raise ValueError('工具根参数必须为固定字段对象。')
    if schema.get('$defs'): result['$defs'] = {name: visit(node) for name, node in schema['$defs'].items()}
    return result


def decode_strict_arguments(schema: dict, raw: Any) -> Any:
    """只还原已声明的API投影；未知字段、缺失字段和业务值仍交原校验处理。"""
    def matches(node, value):
        node = _resolve(node, schema)
        if 'const' in node and value != node['const']: return False
        if 'enum' in node and value not in node['enum']: return False
        if 'anyOf' in node: return any(matches(item, value) for item in node['anyOf'])
        kind = node.get('type')
        if isinstance(kind, list): return any(matches({**node, 'type': item}, value) for item in kind)
        if kind == 'object' and isinstance(node.get('additionalProperties'), dict): return isinstance(value, list)
        return {'object': isinstance(value, dict), 'array': isinstance(value, list), 'null': value is None,
                'string': isinstance(value, str), 'integer': type(value) is int, 'number': type(value) in (int, float),
                'boolean': type(value) is bool}.get(kind, False)
    def visit(node, value, depth=0):
        if depth > 64: raise ValueError('工具参数嵌套过深。')
        node = _resolve(node, schema)
        if isinstance(node.get('type'), list):
            node = {'anyOf': [{**node, 'type': kind} for kind in node['type']]}
        if 'anyOf' in node:
            candidates = [item for item in node['anyOf'] if matches(item, value)]
            decoded = [visit(item, value, depth + 1) for item in candidates]
            if decoded and any(item != decoded[0] for item in decoded[1:]):
                raise ValueError('联合类型的参数投影不唯一，未猜测业务分支。')
            return decoded[0] if decoded else value
        if node.get('type') == 'object':
            extra = node.get('additionalProperties')
            if isinstance(extra, dict) and isinstance(value, list):
                mapped = {}
                for item in value:
                    if not isinstance(item, dict) or set(item) != {'key', 'value'} or not isinstance(item['key'], str) or item['key'] in mapped:
                        raise ValueError('字典投影需要不重复的key/value项。')
                    mapped[item['key']] = visit(extra, item['value'], depth + 1)
                return mapped
            if isinstance(value, dict):
                props, required = node.get('properties', {}), node.get('required', [])
                def default_null(key, item):
                    return key in props and key not in required and item is None and not _allows_null(props[key], schema)
                return {key: visit(props[key], item, depth + 1) if key in props else item
                        for key, item in value.items() if not default_null(key, item)}
        if node.get('type') == 'array' and isinstance(value, list):
            return [visit(node['items'], item, depth + 1) for item in value]
        return value
    return visit(schema, raw)
