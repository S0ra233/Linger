"""独立服务的协议缺省值；模型能力不匹配名称或读取桌面配置。"""
from .rules import ProviderProfile, ProviderProtocol, RequestRules


def load_provider_profiles() -> dict[str, ProviderProfile]:
    # 管理服务正常传入解析后的规则快照；直接使用 ModelConfig 时也只有明确协议缺省。
    return {"compatible": ProviderProfile("显式协议", {
        "chat_completions": ProviderProtocol("", RequestRules(output_limit_field="max_tokens", stream_usage=False)),
        "responses": ProviderProtocol("", RequestRules(include_encrypted_reasoning=False)),
        "anthropic_messages": ProviderProtocol("", RequestRules()),
        "gemini": ProviderProtocol("", RequestRules()),
    })}
