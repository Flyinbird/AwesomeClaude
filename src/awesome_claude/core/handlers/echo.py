"""echo 处理器 - 回显测试。"""

from typing import Any

from awesome_claude.core.handlers.base import register_handler
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.protocol.methods import METHOD_ECHO


@register_handler(METHOD_ECHO)
async def handle_echo(
    params: dict[str, Any] | None, context: HandlerContext
) -> dict[str, Any]:
    """回显测试：原样返回 message。

    Args:
        params: 必须包含 message 字段。
        context: 运行时上下文（未使用）。

    Returns:
        包含回显内容的结果。

    Raises:
        ValueError: params 缺失或缺少 message 字段。
    """
    if params is None or "message" not in params:
        raise ValueError("echo 需要参数 message")
    return {"echo": params["message"]}
