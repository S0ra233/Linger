"""读取 RP 原生与酒馆聊天补全预设；不把任意厂商字段透传给 API。"""
import hashlib
from pathlib import PurePath

from .preset_models import PresetContent
from ..information.imports import decode_json


def import_preset(source: bytes, filename: str):
    raw = decode_json(source)
    warnings = []
    name = PurePath(filename).stem[:80] or '导入预设'
    if raw.get('format') == 'rp_preset_v1':
        config = PresetContent.model_validate(raw.get('config')).model_dump()
        name = raw.get('name', name)
        kind = 'rp_preset_v1'
    elif 'prompts' in raw or any(key in raw for key in ('main_prompt', 'jailbreak_prompt', 'openai_max_context')):
        kind = 'sillytavern_chat_completion'
        context = {'role_prompt': '', 'post_history_prompt': '', 'prompt_blocks': []}
        for source_key, target in [('openai_max_context', 'context_window')]:
            if source_key in raw:
                context[target] = raw[source_key]
        config = {'context': context}
        for source_key, target in [('openai_max_tokens', 'max_output_tokens'), ('temperature', 'temperature')]:
            if source_key in raw:
                config[target] = raw[source_key]
        prompts = raw.get('prompts', [])
        if not isinstance(prompts, list) or any(not isinstance(item, dict) or not isinstance(item.get('identifier'), str) for item in prompts):
            raise ValueError('酒馆 prompts 必须是带 identifier 的对象数组。')
        if len({item['identifier'] for item in prompts}) != len(prompts):
            raise ValueError('酒馆提示词编号重复。')
        lookup = {item['identifier']: item for item in prompts}
        orders = raw.get('prompt_order', [])
        if not isinstance(orders, list) or any(not isinstance(item, dict) for item in orders):
            raise ValueError('酒馆 prompt_order 无效。')
        selected = next((item for item in orders if item.get('character_id') == 100000), orders[0] if orders else None)
        if selected:
            order = selected.get('order')
            warnings.append(f"采用 prompt_order 分组 {selected.get('character_id', '未标注')}；其他分组仅保留。")
        else:
            order = [{'identifier': item['identifier'], 'enabled': item.get('enabled', False)} for item in prompts]
            if prompts:
                warnings.append('未提供启用顺序，只采用明确 enabled=true 的提示词段。')
        if not isinstance(order, list) or any(not isinstance(item, dict) or not isinstance(item.get('identifier'), str) or type(item.get('enabled')) is not bool for item in order):
            raise ValueError('提示词顺序需要 identifier 与布尔 enabled。')
        if len({item['identifier'] for item in order}) != len(order):
            raise ValueError('提示词顺序编号重复。')
        after = False
        for item in order:
            identifier, enabled = item['identifier'], item['enabled']
            if identifier == 'chatHistory':
                after = True
                continue
            prompt = lookup.get(identifier)
            if not prompt:
                if enabled:
                    warnings.append(f'未找到提示词定义：{identifier}，未采用。')
                continue
            if prompt.get('marker'):
                continue
            content = prompt.get('content', '')
            role = prompt.get('role', 'system')
            triggers = prompt.get('injection_trigger', [])
            # normal 与真实输入可明确对应；quiet 同时可能代表各种维护，不猜成后台生活。
            run_types = ['input'] if triggers == ['normal'] else []
            if identifier in {'main', 'jailbreak'} and role == 'system' and not prompt.get('injection_position', 0) and not prompt.get('injection_trigger'):
                context['role_prompt' if identifier == 'main' else 'post_history_prompt'] = content if enabled else ''
                continue
            if prompt.get('injection_position', 0) != 0 or (triggers and not run_types):
                warnings.append(f'提示词 {identifier} 使用深度或无法对应的生成类型条件，默认关闭；请手动确认适用运行。')
                enabled = False
            context['prompt_blocks'].append({'id': identifier, 'name': prompt.get('name', identifier), 'role': role,
                'position': 'after_history' if after else 'before_character', 'content': content, 'enabled': enabled,
                'run_types': run_types})
            if run_types:
                warnings.append(f'提示词 {identifier} 的 normal 映射为普通回复；未映射为生活或摘要。')
        if not prompts:
            context['role_prompt'] = raw.get('main_prompt', '')
            context['post_history_prompt'] = raw.get('jailbreak_prompt', '')
            if raw.get('nsfw_prompt'):
                context['prompt_blocks'].append({'id': 'auxiliary', 'name': '辅助提示词', 'content': raw['nsfw_prompt']})
        warnings.append('角色、世界书及真实历史由 RP 固定装配；不复刻酒馆动态标记重排、深度注入、扩展、宏脚本或所有采样参数。')
        if any('{{' in str(item.get('content', '')) for item in prompts):
            warnings.append('仅支持 {{char}}／{{user}}／<char>／<bot>，主／历史后提示词支持 {{original}}；其他宏保持原文，不执行。')
        if 'reasoning_effort' in raw:
            effort = raw['reasoning_effort']
            if effort is None or isinstance(effort, str) and effort in {'', 'none','minimal','low','medium','high','xhigh','max','ultra'}:
                config['reasoning_effort'] = effort or None
                warnings.append('导入期望强度；采用到具体模型时仍校验其能力，不推断原生支持。')
            else:
                warnings.append('未支持的原生强度设置仅保留在导入原文，采用前请明确配置。')
        known = {'prompts', 'prompt_order', 'main_prompt', 'jailbreak_prompt', 'nsfw_prompt', 'openai_max_context', 'openai_max_tokens', 'temperature', 'reasoning_effort'}
        unused = sorted(set(raw) - known)
        if unused:
            warnings.append('其他设置仅保留：' + '、'.join(unused))
        config = PresetContent.model_validate(config).model_dump()
    else:
        raise ValueError('仅支持 RP 原生预设或酒馆 Chat Completion JSON 预设。')
    return {'name': name, 'config': config, 'revision': 0, 'source': {'format': kind, 'filename': filename[:200],
        'sha256': hashlib.sha256(source).hexdigest(), 'raw': raw, 'warnings': warnings}}
