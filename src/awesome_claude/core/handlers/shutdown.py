"""shutdown 处理器 - 通知服务端关闭。"""

from typing import Any

from awesome_claude.core.handlers.base import register_handler
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.protocol.methods import METHOD_SHUTDOWN


@register_handler(METHOD_SHUTDOWN)
async def handle_shutdown(
    params: dict[str, Any] | None, context: HandlerContext
) -> dict[str, Any]:
    """处理 shutdown：返回空结果，实际关闭由 session 层触发。

    Args:
        params: shutdown 参数（无）。
        context: 运行时上下文（未使用）。

    Returns:
        空结果。
    """
    return {}
