"""只在管理配置边界补全端点；旧快照和自定义完整地址保持原义。"""
from urllib.parse import urlsplit, urlunsplit


_OPERATIONS = {
    'chat_completions': '/chat/completions',
    'responses': '/responses',
    'anthropic_messages': '/messages',
    'embedding_openai': '/embeddings',
    'embedding_ollama': '/api/embed',
}


def endpoint_base(url: str, protocol: str) -> str | None:
    """仅识别当前协议的标准后缀，不猜测私人网关的自定义路由。"""
    address = urlsplit(url)
    path = address.path.rstrip('/')
    if protocol == 'embedding_gemini':
        return url.rstrip('/')
    if protocol == 'gemini':
        suffix = '/models/{model}:generateContent'
    else:
        suffix = _OPERATIONS[protocol]
    if not path.endswith(suffix):
        return None
    return urlunsplit(address._replace(path=path[:-len(suffix)]))


def resolve_endpoint(url: str, protocol: str, mode: str) -> str:
    if mode == 'full':
        return url
    # 完整标准地址也可粘贴到基础地址栏，不重复添加操作路径。
    if protocol != 'embedding_gemini' and endpoint_base(url, protocol) is not None:
        return url.rstrip('/')
    address = urlsplit(url)
    path = address.path.rstrip('/')
    if address.hostname == 'api.deepseek.com' and protocol == 'anthropic_messages' and path in {'', '/anthropic'}:
        path = '/anthropic/v1'
    # 版本只对已知官方根地址补全；反代的 /v1 或私人路径由用户提供。
    if not path:
        if address.hostname == 'api.openai.com' and protocol in {'chat_completions', 'responses', 'embedding_openai'}:
            path = '/v1'
        elif address.hostname == 'api.anthropic.com' and protocol == 'anthropic_messages':
            path = '/v1'
        elif protocol in {'gemini', 'embedding_gemini'}:
            path = '/v1beta'
    if protocol == 'gemini':
        path += '/models/{model}:generateContent'
    elif protocol != 'embedding_gemini':
        path += _OPERATIONS[protocol]
    return urlunsplit(address._replace(path=path))
