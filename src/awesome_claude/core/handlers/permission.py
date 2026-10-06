"""permission.respond 处理器 - 接收客户端审批响应并唤醒 pending 审批。"""

from typing import Any

from awesome_claude.core.handlers.base import register_handler
from awesome_claude.core.permissions.types import PermissionDecision
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.protocol.errors import INVALID_PARAMS, build_error_response
from awesome_claude.protocol.methods import METHOD_PERMISSION_RESPOND


@register_handler(METHOD_PERMISSION_RESPOND)
async def handle_permission_respond(
    params: dict[str, Any] | None, context: HandlerContext
) -> dict[str, Any]:
    """处理客户端对审批请求的响应。

    将决定委托给 ``InteractiveBroker.respond()``；若找不到对应 pending
    请求或请求已超时，返回错误。

    Args:
        params: 必须包含 ``request_id`` 与 ``decision``（``allow`` 或 ``deny``）。
        context: 运行时上下文。

    Returns:
        成功时返回 ``{"ok": True}``；参数错误或请求已过期时返回错误响应。
    """
    if params is None or not isinstance(params, dict):
        return build_error_response(None, INVALID_PARAMS, "参数必须是对象")

    request_id = params.get("request_id")
    decision_raw = params.get("decision")
    if not isinstance(request_id, str) or not request_id:
        return build_error_response(None, INVALID_PARAMS, "request_id 是必填字符串")
    if decision_raw not in ("allow", "deny"):
        return build_error_response(
            None, INVALID_PARAMS, "decision 必须是 allow 或 deny"
        )

    decision = (
        PermissionDecision.ALLOW if decision_raw == "allow" else PermissionDecision.DENY
    )

    broker = context.permission_broker
    if broker is None:
        return build_error_response(None, INVALID_PARAMS, "当前未启用交互式审批通道")

    ok = broker.respond(request_id, decision)
    if not ok:
        return build_error_response(None, INVALID_PARAMS, "审批请求已过期或不存在")
    return {"ok": True}
