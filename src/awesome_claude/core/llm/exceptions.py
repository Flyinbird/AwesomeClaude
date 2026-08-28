"""LLM 相关异常定义。"""


class LLMError(Exception):
    """LLM 调用异常基类。"""


class LLMAuthError(LLMError):
    """认证失败（API key 无效）。"""


class LLMTimeoutError(LLMError):
    """请求超时。"""


class LLMRateLimitError(LLMError):
    """速率限制。"""


class LLMContentFilterError(LLMError):
    """内容被安全过滤。"""
