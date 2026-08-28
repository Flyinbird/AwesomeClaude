"""ping 处理器 - 健康检查。"""

from datetime import UTC, datetime
from typing import Any

from awesome_claude.core.handlers.base import register_handler
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.protocol.methods import METHOD_PING


@register_handler(METHOD_PING)
async def handle_ping(
    params: dict[str, Any] | None, context: HandlerContext
) -> dict[str, Any]:
    """健康检查：返回状态与当前 UTC ISO 时间戳。

    Args:
        params: ping 参数（无）。
        context: 运行时上下文（未使用）。

    Returns:
        包含 status 与 timestamp 的结果。
    """
    return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}
