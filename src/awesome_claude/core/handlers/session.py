"""session 处理器 - 会话订阅与退订。"""

from typing import Any

from awesome_claude.core.handlers.base import register_handler
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.protocol.errors import INVALID_PARAMS, build_error_response
from awesome_claude.protocol.methods import (
    METHOD_SESSION_ATTACH,
    METHOD_SESSION_DETACH,
)


@register_handler(METHOD_SESSION_ATTACH)
async def handle_session_attach(
    params: dict[str, Any] | None, context: HandlerContext
) -> dict[str, Any]:
    """订阅到指定会话，返回含历史回放的会话状态。

    Args:
        params: 必须包含非空 session_id。
        context: 运行时上下文。

    Returns:
        会话回放状态（session_id、history、active_tasks），或错误响应。
    """
    if params is None or not isinstance(params, dict) or "session_id" not in params:
        return build_error_response(
            None, INVALID_PARAMS, "session.attach 需要参数 session_id"
        )
    session_id = params["session_id"]
    if not isinstance(session_id, str) or not session_id:
        return build_error_response(None, INVALID_PARAMS, "session_id 必须是非空字符串")
    return context.sessions.attach(session_id)


@register_handler(METHOD_SESSION_DETACH)
async def handle_session_detach(
    params: dict[str, Any] | None, context: HandlerContext
) -> dict[str, Any]:
    """退订当前会话。

    Args:
        params: 无参数。
        context: 运行时上下文。

    Returns:
        空结果。
    """
    context.sessions.detach()
    return {}
