"""权限审批通道 - 询问态的外部审批接口与无交互实现。"""

from typing import Protocol

from awesome_claude.core.permissions.types import (
    PermissionDecision,
    PermissionOutcome,
    PermissionRequest,
)


class PermissionBroker(Protocol):
    """询问态的审批通道协议。

    判定为询问时，权限管理者委托本协议获取最终裁决；实现可对接交互式
    审批（向用户请求），或提供无交互的降级策略。
    """

    async def ask(self, request: PermissionRequest) -> PermissionOutcome:
        """就一次工具调用向外部请求审批。

        Args:
            request: 权限判定请求。

        Returns:
            最终裁决（允许或拒绝）。
        """
        ...


class NonInteractiveBroker:
    """无交互审批通道：询问一律按拒绝处理（fail-closed）。"""

    async def ask(self, request: PermissionRequest) -> PermissionOutcome:
        """将询问按拒绝处理。

        Args:
            request: 权限判定请求。

        Returns:
            恒为拒绝的结果。
        """
        return PermissionOutcome(PermissionDecision.DENY, "无交互审批通道，按拒绝处理")
