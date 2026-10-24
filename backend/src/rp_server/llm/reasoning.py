"""具体模型已确认的推理能力；不从厂商、地址或模型名称猜测。"""
from dataclasses import dataclass, field


EFFORT_LEVELS = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")
MESSAGES_DEFAULT_MAX_TOKENS = 4096
REASONING_ADAPTERS = {
    "unsupported": "已确认不支持推理设置",
    "openai": "OpenAI effort（Chat / Responses）",
    "deepseek": "DeepSeek thinking + effort",
    "anthropic": "Claude effort（不改 thinking 模式）",
    "anthropic_combined": "Claude 模式、effort 与预算分别配置",
    "anthropic_adaptive": "Claude adaptive thinking + effort",
    "anthropic_budget": "Claude thinking token 预算",
    "gemini_level": "Gemini thinkingLevel",
    "gemini_budget": "Gemini thinkingBudget",
}
NATIVE_EFFORTS = {
    'openai': list(EFFORT_LEVELS[:-1]), 'deepseek': ['none', 'low', 'high', 'max'],
    'anthropic': ['low', 'medium', 'high', 'xhigh', 'max'],
    'anthropic_combined': ['low', 'medium', 'high', 'xhigh', 'max'],
    'anthropic_adaptive': ['none', 'low', 'medium', 'high', 'xhigh', 'max'],
    'gemini_level': ['minimal', 'low', 'medium', 'high'],
}
ADAPTER_STYLES = {
    'unsupported': ['chat_completions', 'responses', 'anthropic_messages', 'gemini'],
    'openai': ['chat_completions', 'responses'],
    'deepseek': ['chat_completions', 'responses', 'anthropic_messages'],
    'anthropic': ['anthropic_messages'], 'anthropic_adaptive': ['anthropic_messages'],
    'anthropic_combined': ['anthropic_messages'],
    'anthropic_budget': ['anthropic_messages'], 'gemini_level': ['gemini'], 'gemini_budget': ['gemini'],
}


def reasoning_catalog():
    """仅返回已实现的语法，不将适配器范围宣称为具体模型能力。"""
    return {'efforts': EFFORT_LEVELS, 'adapters': [
        {'id': key, 'label': label, 'protocols': ADAPTER_STYLES[key],
         'native_levels': NATIVE_EFFORTS.get(key, []), 'budget': key.endswith('_budget')}
        for key, label in REASONING_ADAPTERS.items()]}


def is_disabled_effort(profile: "ReasoningProfile | None", effort: str | None) -> bool:
    """识别原生关闭档及其别名；None 只表示应用没有发送强度设置。"""
    return effort is not None and (effort == "none" or profile is not None
                                  and profile.effort_map.get(effort) in {"none", 0})


def parse_effort(value: str | None) -> str | None:
    """None 表示不发送；none 是模型条目明确开放的关闭档，二者不同。"""
    if value is not None and (not isinstance(value, str) or value not in EFFORT_LEVELS):
        raise ValueError(f"reasoning_effort 须为 {', '.join(EFFORT_LEVELS)}，或 null（不发送强度参数）。")
    return value


@dataclass(frozen=True)
class ReasoningProfile:
    """把应用档位映射到原生档位或预算；空的工具档位表示禁止该组合。"""

    adapter: str
    effort_map: dict[str, str | int] = field(default_factory=dict)
    # None 表示未声明额外组合限制，不等于服务保证支持工具。
    tool_efforts: list[str] | None = None
    # 未声明时不凭模型名称猜测；只有默认必定思考的条目才填写 True。
    default_thinking: bool | None = None
    temperature_efforts: list[str] | None = None
    image_efforts: list[str] | None = None
    min_output_tokens: int | None = None
    max_output_tokens: int | None = None
    max_thinking_tokens: int | None = None
    thinking_mode: str | None = None
    thinking_budget: int | None = None
    effort_delivery: str = 'top_level'
    baseline_effort: str | None = None

    def validate(self, api_style: str) -> None:
        """检查配置边界；具体模型能否使用某档位仍须由用户按文档确认。"""
        if self.default_thinking is not None and type(self.default_thinking) is not bool:
            raise ValueError("reasoning.default_thinking 必须是布尔值或 null。")
        if not isinstance(self.adapter, str) or api_style not in ADAPTER_STYLES.get(self.adapter, []):
            raise ValueError("reasoning.adapter 未实现，或与接口协议不匹配。")
        if self.thinking_mode not in {None, 'enabled', 'disabled', 'adaptive', 'between_tools'} or self.effort_delivery not in {'top_level', 'message'}:
            raise ValueError('推理模式或 effort 位置无效。')
        if self.adapter != 'anthropic_combined' and (self.thinking_mode is not None or self.thinking_budget is not None):
            raise ValueError('独立模式／预算请使用 Claude 组合适配。')
        if self.adapter == 'anthropic_combined':
            if self.thinking_mode is None or (self.thinking_mode == 'enabled') != (self.thinking_budget is not None):
                raise ValueError('组合适配必须明确模式；只有 enabled 需要 thinking_budget。')
            if self.thinking_budget is not None and (type(self.thinking_budget) is not int or not 1024 <= self.thinking_budget <= 1_000_000):
                raise ValueError('思考预算须为 1024—1000000。')
            if self.max_thinking_tokens is not None and self.thinking_budget is None:
                raise ValueError('组合适配只有固定 enabled 预算能声明思考 Token 硬上限。')
        if self.effort_delivery == 'message':
            if self.adapter not in {'anthropic', 'anthropic_adaptive', 'anthropic_combined'} or self.baseline_effort not in NATIVE_EFFORTS['anthropic']:
                raise ValueError('逐消息 effort 需要 Claude 适配及明确的固定基准档位。')
            if self.adapter == 'anthropic_combined' and self.thinking_mode != 'adaptive':
                raise ValueError('逐消息变更在本系统只开放已确认的 adaptive 模式。')
        elif self.baseline_effort is not None:
            raise ValueError('顶层 effort 不使用逐消息基准档位。')
        if not isinstance(self.effort_map, dict) or any(key not in EFFORT_LEVELS for key in self.effort_map):
            raise ValueError("reasoning.effort_map 须使用已支持的应用档位作为键。")
        if self.effort_delivery == 'message' and self.baseline_effort not in self.effort_map.values():
            raise ValueError('逐消息固定基准也必须属于已确认的原生映射。')
        for name in ('temperature_efforts', 'image_efforts'):
            choices = getattr(self, name)
            if choices is not None and (not isinstance(choices, list)
                    or any(not isinstance(key, str) or key not in self.effort_map for key in choices)
                    or len(set(choices)) != len(choices)):
                raise ValueError(f'{name} 须为已配置档位的不重复数组，或 null。')
        for name in ('min_output_tokens', 'max_output_tokens', 'max_thinking_tokens'):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or not 1 <= value <= 1_000_000):
                raise ValueError(f'{name} 须为 1—1000000 的整数，或 null。')
        if self.min_output_tokens and self.max_output_tokens and self.min_output_tokens > self.max_output_tokens:
            raise ValueError('最小输出上限不能大于最大输出上限。')
        if self.max_thinking_tokens is not None and self.adapter not in {'anthropic_budget', 'anthropic_combined', 'gemini_budget'}:
            raise ValueError('最大思考 Token 预算只适用于已实现的预算型适配，不能替代 effort 档位。')
        if self.adapter == "unsupported":
            if self.effort_map or self.tool_efforts is not None:
                raise ValueError("不支持推理设置的模型不能声明档位或工具档位。")
            return
        if not self.effort_map:
            raise ValueError("已确认的推理方式需要至少一个 effort_map 档位。")
        for value in self.effort_map.values():
            if self.adapter == "anthropic_budget":
                valid = type(value) is int and (value == 0 or value >= 1024)
            elif self.adapter == "gemini_budget":
                valid = type(value) is int and value >= -1
            else:
                allowed = NATIVE_EFFORTS[self.adapter]
                valid = isinstance(value, str) and value in allowed
            if not valid:
                raise ValueError(f"reasoning.effort_map 的值不符合 {self.adapter} 原生档位或预算要求。")
        if self.tool_efforts is not None and (
            not isinstance(self.tool_efforts, list)
            or any(not isinstance(key, str) or key not in self.effort_map for key in self.tool_efforts)
            or len(set(self.tool_efforts)) != len(self.tool_efforts)
        ):
            raise ValueError("reasoning.tool_efforts 须为 effort_map 中不重复的档位数组，或 null。")


def reasoning_profile_from_dict(data: dict) -> ReasoningProfile:
    """从用户配置或会话快照还原类型；此处不读取目录。"""
    if not isinstance(data, dict):
        raise ValueError("reasoning 须为对象或 null（未知能力）。")
    try:
        return ReasoningProfile(**data)
    except TypeError as exc:
        raise ValueError("推理能力包含未支持的字段，请使用当前模型配置界面。") from exc


def available_efforts(profile: ReasoningProfile | None, *, has_tools: bool = False) -> tuple[str | None, ...]:
    """选择器和试运行共用；合并同一原生档位别名，优先显示原生同名档。"""
    choices: list[str | None] = [None] if not has_tools or profile is None or profile.tool_efforts is None else []
    if profile is None:
        return tuple(choices)
    allowed = {level for level in EFFORT_LEVELS if level in profile.effort_map
               and (not has_tools or profile.tool_efforts is None or level in profile.tool_efforts)}
    seen = set()
    for level in EFFORT_LEVELS:
        if level not in allowed:
            continue
        native = profile.effort_map[level]
        if isinstance(native, str) and native in allowed and level != native:
            continue
        if native not in seen:
            choices.append(level)
            seen.add(native)
    return tuple(choices)


def resolve_effort(
    profile: ReasoningProfile | None, effort: str | None, *, has_tools: bool = False,
    temperature: float | None = None, max_output_tokens: int | None = None, has_images: bool = False,
) -> str | int | None:
    """检查选择及参数组合；运行层不需要生成厂商 JSON 来判断能否开始。"""
    if profile is not None:
        if profile.adapter == 'anthropic_combined' and profile.thinking_budget is not None:
            if max_output_tokens is None or profile.thinking_budget >= max_output_tokens:
                raise ValueError('固定思考预算须小于输出上限。')
            if profile.max_thinking_tokens and profile.thinking_budget > profile.max_thinking_tokens:
                raise ValueError('固定思考预算超过已确认上限。')
        if profile.effort_delivery == 'message' and effort is None:
            raise ValueError('逐消息 effort 必须选择明确档位。')
        if (effort is None and temperature is not None and profile.adapter == 'deepseek'
                and profile.default_thinking is True):
            raise ValueError('此模型已确认默认思考，温度不会生效；请清空温度。')
        if max_output_tokens is not None and (
                profile.min_output_tokens is not None and max_output_tokens < profile.min_output_tokens
                or profile.max_output_tokens is not None and max_output_tokens > profile.max_output_tokens):
            raise ValueError('输出上限超出此模型已确认的范围；请调整参数，不会自动增加或裁剪。')
        for used, choices, label in ((temperature is not None, profile.temperature_efforts, '温度'),
                                     (has_images, profile.image_efforts, '图片')):
            if used and choices is not None and effort not in choices:
                raise ValueError(f'此模型未确认当前强度可配合{label}；请选择允许的档位或移除对应输入／参数。')
    if effort is None:
        if has_tools and profile is not None and profile.tool_efforts is not None:
            if not profile.tool_efforts:
                raise ValueError("此模型没有已确认可用的工具档位，请更换模型或修改能力配置。")
            raise ValueError("此模型已声明工具档位限制，请开启强度设置并选择已确认的档位；不发送参数无法确认当前组合。")
        return None
    if profile is None or effort not in profile.effort_map:
        raise ValueError("此模型未确认支持所选 reasoning_effort；请配置能力或关闭强度设置。")
    if has_tools and profile.tool_efforts is not None and effort not in profile.tool_efforts:
        raise ValueError("此模型的所选推理档位不支持当前工具请求；请使用已确认的协议和档位。")
    value = profile.effort_map[effort]
    if value == -1 and profile.max_thinking_tokens is not None:
        raise ValueError('动态思考预算不能保证指定上限；请选择明确预算或清除该上限。')
    if type(value) is int and value > 0 and profile.max_thinking_tokens is not None and value > profile.max_thinking_tokens:
        raise ValueError('思考预算超过此模型已确认的上限。')
    # DeepSeek 明确忽略思考模式中的温度，故提示而不假装已经生效。
    # 其他服务的温度限制依具体模型不同，不按厂商一律禁止。
    if temperature is not None and profile.adapter == "deepseek" and value != "none":
        raise ValueError("DeepSeek 思考模式不会应用 temperature；请清空温度后重试。")
    if profile.adapter == "anthropic_budget" and value != 0:
        maximum = max_output_tokens if max_output_tokens is not None else MESSAGES_DEFAULT_MAX_TOKENS
        if value >= maximum:
            raise ValueError("Claude thinking 预算须小于最大输出 token；摘要也需要足够的输出上限。")
    return value
