"""最终计划的协议格式映射；不执行正文，不替换原生查询或思考续接。"""
from copy import deepcopy

from .tool_policy import strict_parameters


def json_plan_format(config):
    """显式配置确定能力边界，不按名称猜测，不在失败后自动降级。"""
    if config.api_style in {'chat_completions', 'responses'}:
        return 'json_schema' if config.provider_kind == 'openai' else 'json_object'
    if config.api_style == 'anthropic_messages':
        if config.provider_kind == 'deepseek':
            raise ValueError('DeepSeek Messages只支持output_config.effort；JSON计划请使用Chat或Responses。')
        return 'json_schema'
    if config.api_style == 'gemini':
        return 'json_schema'
    raise ValueError('当前协议不支持RP的JSON计划输出。')


def plan_output_schema(schema):
    if not isinstance(schema, dict):
        raise ValueError('JSON结构化输出缺少本轮计划Schema。')
    # 使用原默认值表示空资料，避免每个可选标量都额外引入nullable联合。
    # 动态字典沿用已有key/value投影，返回时按原Schema还原后再做业务校验。
    return strict_parameters(schema, optional_defaults=True)


def output_fields(config, request):
    """统一格式在Provider末端序列化；嵌套字段需与effort等现有设置合并。"""
    if request.output_format is None:
        return {}
    kind = request.output_format
    if kind not in {'json_object', 'json_schema'}:
        raise ValueError('不支持的输出格式。')
    if kind == 'json_schema':
        if not isinstance(request.output_schema, dict) or request.output_schema.get('type') != 'object':
            raise ValueError('结构化输出需要对象Schema。')
        schema = deepcopy(request.output_schema)
    else:
        schema = None
    if config.api_style == 'chat_completions':
        return {'response_format': {'type': kind, **({'json_schema': {
            'name': 'rp_plan', 'strict': True, 'schema': schema}} if schema is not None else {})}}
    if config.api_style == 'responses':
        return {'text': {'format': {'type': kind, **({'name': 'rp_plan', 'strict': True,
            'schema': schema} if schema is not None else {})}}}
    if config.api_style == 'anthropic_messages' and schema is not None and config.provider_kind != 'deepseek':
        return {'output_config': {'format': {'type': 'json_schema', 'schema': schema}}}
    if config.api_style == 'gemini' and schema is not None:
        return {'generationConfig': {'responseMimeType': 'application/json', 'responseJsonSchema': schema}}
    raise ValueError('此协议／提供商不支持所选JSON格式；未忽略配置或自动改用其他模式。')


def output_controls(config, request):
    """诊断只展示控制类型，不重复记录Schema正文。"""
    fields = output_fields(config, request)
    if 'response_format' in fields:
        return {'response_format': {'type': request.output_format}}
    if 'text' in fields:
        return {'text': {'format': {'type': request.output_format}}}
    if 'output_config' in fields:
        return {'output_config': {'format': {'type': request.output_format}}}
    if 'generationConfig' in fields:
        return {'generationConfig': {'responseMimeType': 'application/json', 'responseJsonSchema': {'type': 'object'}}}
    return {}
