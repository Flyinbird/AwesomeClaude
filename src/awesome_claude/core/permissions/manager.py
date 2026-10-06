"""权限管理者 - 编排策略判定与审批通道，并记录权限轨迹。"""

from typing import TYPE_CHECKING

from awesome_claude.core.permissions.broker import PermissionBroker
from awesome_claude.core.permissions.policy import PermissionPolicy
from awesome_claude.core.permissions.types import (
    PermissionDecision,
    PermissionOutcome,
    PermissionRequest,
)
from awesome_claude.shared.types import TraceStage

if TYPE_CHECKING:
    from awesome_claude.core.observability.trace_recorder import TraceRecorder


class PermissionManager:
    """一次 Run 作用域内的工具执行权限把关者。

    编排「查策略 → 询问态委托审批通道 → 记录权限轨迹」，对上层暴露单一的
    `authorize` 入口。轨迹记录器可选，缺省时不产生权限事件。
    """

    def __init__(
        self,
        policy: PermissionPolicy,
        broker: PermissionBroker,
        *,
        recorder: "TraceRecorder | None" = None,
    ) -> None:
        """初始化权限管理者。

        Args:
            policy: 进程级权限策略。
            broker: 询问态审批通道。
            recorder: 本次 Run 的轨迹记录器（可选）。
        """
        self._policy = policy
        self._broker = broker
        self._recorder = recorder

    async def authorize(self, request: PermissionRequest) -> PermissionOutcome:
        """对一次工具调用做权限判定。

        Args:
            request: 权限判定请求。

        Returns:
            最终裁决；判定为询问时委托审批通道，且结果恒为允许或拒绝。
        """
        decision = self._policy.evaluate(request, request.spec)
        await self._record(TraceStage.PERMISSION_REQUESTED, request, decision)
        if decision is PermissionDecision.ASK:
            outcome = await self._broker.ask(request)
        else:
            outcome = PermissionOutcome(decision, decision.value)
        stage = (
            TraceStage.PERMISSION_GRANTED
            if outcome.is_allowed
            else TraceStage.PERMISSION_DENIED
        )
        await self._record(stage, request, outcome.decision, outcome.reason)
        return outcome

    def cancel_pending_for_run(self, run_id: str) -> None:
        """Run 被取消时，清理其下所有 pending 审批。

        Args:
            run_id: 目标 Run 标识。
        """
        if hasattr(self._broker, "cancel_for_run"):
            self._broker.cancel_for_run(run_id)

    async def _record(
        self,
        stage: TraceStage,
        request: PermissionRequest,
        decision: PermissionDecision,
        reason: str = "",
    ) -> None:
        """记录一条权限轨迹事件（无记录器时为无操作）。"""
        if self._recorder is None:
            return
        await self._recorder.record(
            stage,
            {
                "tool": request.tool,
                "resources": list(request.resources),
                "decision": decision.value,
                "reason": reason,
            },
            step_index=request.step_index,
        )
