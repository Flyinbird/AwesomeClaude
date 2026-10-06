"""权限审批通道 - 询问态的外部审批接口与无交互实现。"""

import asyncio
import uuid
from dataclasses import dataclass
from typing import Protocol

from awesome_claude.core.permissions.types import (
    PermissionDecision,
    PermissionOutcome,
    PermissionRequest,
)
from awesome_claude.core.session.registry import SessionRegistry
from awesome_claude.protocol.methods import NOTIFY_CHAT_PERMISSION_REQUESTED
from awesome_claude.shared.logging.app_logger import get_app_logger


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


@dataclass
class _PendingItem:
    """一条 pending 审批请求的内部状态。"""

    future: asyncio.Future[PermissionOutcome]
    run_id: str
    request_id: str


class InteractiveBroker:
    """交互式审批通道：向客户端广播审批请求并等待响应。

    维护内存中的 pending 字典，通过 ``SessionRegistry`` 查找会话并向其
    所有订阅客户端广播 ``chat.permission_requested`` 通知；在收到
    ``permission.respond`` 或超时时解除阻塞。
    """

    def __init__(self, registry: SessionRegistry, timeout: int = 60) -> None:
        """初始化交互式审批通道。

        Args:
            registry: 会话注册表，用于按 run_id 查找会话并广播通知。
            timeout: 审批超时秒数，默认 60。
        """
        self._registry = registry
        self._timeout = timeout
        self._pending: dict[str, _PendingItem] = {}
        self._lock = asyncio.Lock()
        self._logger = get_app_logger("core.permissions.broker")

    async def ask(self, request: PermissionRequest) -> PermissionOutcome:
        """广播审批请求并等待客户端响应或超时。

        Args:
            request: 权限判定请求。

        Returns:
            最终裁决（允许或拒绝）。超时按拒绝处理。
        """
        request_id = uuid.uuid4().hex[:8]
        future = asyncio.get_running_loop().create_future()
        item = _PendingItem(future=future, run_id=request.run_id, request_id=request_id)
        async with self._lock:
            self._pending[request_id] = item

        try:
            session = self._registry.get_session_by_run_id(request.run_id)
            if session is not None:
                await self._registry.broadcast(
                    session.session_id,
                    NOTIFY_CHAT_PERMISSION_REQUESTED,
                    {
                        "request_id": request_id,
                        "tool_name": request.tool,
                        "action": request.action,
                        "resources": list(request.resources),
                        "run_id": request.run_id,
                        "step_index": request.step_index or 0,
                    },
                )
            self._logger.info(
                "permission request broadcast",
                request_id=request_id,
                tool=request.tool,
                run_id=request.run_id,
            )
            return await asyncio.wait_for(future, timeout=self._timeout)
        except TimeoutError:
            self._logger.warning(
                "permission request timed out",
                request_id=request_id,
                tool=request.tool,
                run_id=request.run_id,
            )
            return PermissionOutcome(PermissionDecision.DENY, "审批超时，按拒绝处理")
        finally:
            async with self._lock:
                self._pending.pop(request_id, None)

    def respond(self, request_id: str, decision: PermissionDecision) -> bool:
        """客户端回复审批决定。

        Args:
            request_id: 审批请求标识。
            decision: 客户端决定（允许或拒绝）。

        Returns:
            是否成功找到并唤醒对应的 pending 请求。
        """
        item = self._pending.pop(request_id, None)
        if item is None or item.future.done():
            return False
        reason = "用户允许" if decision is PermissionDecision.ALLOW else "用户拒绝"
        outcome = PermissionOutcome(decision, reason)
        item.future.set_result(outcome)
        self._logger.info(
            "permission responded",
            request_id=request_id,
            decision=decision.value,
            run_id=item.run_id,
        )
        return True

    def cancel_for_run(self, run_id: str) -> None:
        """Run 被取消时，清理其下所有 pending 审批并按拒绝处理。

        Args:
            run_id: 目标 Run 标识。
        """
        to_cancel = [
            req_id for req_id, item in self._pending.items() if item.run_id == run_id
        ]
        for req_id in to_cancel:
            item = self._pending.pop(req_id, None)
            if item is not None and not item.future.done():
                item.future.set_result(
                    PermissionOutcome(PermissionDecision.DENY, "Run 已取消")
                )
                self._logger.info(
                    "permission cancelled due to run termination",
                    request_id=req_id,
                    run_id=run_id,
                )
