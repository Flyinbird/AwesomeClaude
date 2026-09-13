"""Run - 会话拥有的一次对话执行实体及其唯一终态状态机。"""

import asyncio
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from awesome_claude.core.session.registry import ConnectionSink


class RunState(StrEnum):
    """Run 生命周期状态。

    RUNNING 为唯一非终态；其余四者为终态，进入后不可再迁移。
    """

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATES: frozenset[RunState] = frozenset(
    {
        RunState.COMPLETED,
        RunState.FAILED,
        RunState.INTERRUPTED,
        RunState.CANCELLED,
    }
)


@dataclass(frozen=True, slots=True)
class RunInitiator:
    """Run 的发起方，用于将最终响应回写至发起请求。

    Args:
        request_id: 发起请求的 JSON-RPC id（notification 发起时为 None）。
        sink: 发起连接的发送端点（可选）。
    """

    request_id: str | int | None
    sink: "ConnectionSink | None" = None


class Run:
    """一次对话执行实体。

    由目标会话拥有，底层承载一个 asyncio 任务，并保证恰好进入一个终态。
    重复取消或对已终态 Run 再次 finish 均为无操作。
    """

    def __init__(
        self,
        run_id: str,
        session_id: str,
        start_time: float,
        *,
        initiator: RunInitiator | None = None,
    ) -> None:
        """初始化 Run（状态为 RUNNING）。

        Args:
            run_id: Run 标识（与底层任务 ID 一致）。
            session_id: 所属会话标识。
            start_time: 单调时钟起点，用于计算持续时间。
            initiator: 发起方信息（可选）。
        """
        self.run_id = run_id
        self.session_id = session_id
        self.start_time = start_time
        self.state: RunState = RunState.RUNNING
        self.task: asyncio.Task[Any] | None = None
        self.initiator = initiator

    @property
    def is_active(self) -> bool:
        """是否仍处于 RUNNING（在途）。"""
        return self.state is RunState.RUNNING

    @property
    def is_terminal(self) -> bool:
        """是否已进入终态。"""
        return self.state in TERMINAL_RUN_STATES

    def set_task(self, task: asyncio.Task[Any]) -> None:
        """绑定底层执行任务。

        Args:
            task: 承载本次对话执行的 asyncio 任务。
        """
        self.task = task

    def finish(self, state: RunState) -> bool:
        """尝试迁移到终态；已终态则无操作。

        Args:
            state: 目标终态。

        Returns:
            本次调用是否完成了状态迁移。
        """
        if self.is_terminal:
            return False
        self.state = state
        return True

    def cancel(self) -> bool:
        """请求取消：迁移到 CANCELLED 并取消底层任务。

        已终态、未绑定任务或任务已结束时返回 False，不做任何变更，
        因此重复取消为无操作。

        Returns:
            是否实际发起了取消。
        """
        if self.is_terminal:
            return False
        task = self.task
        if task is None or task.done():
            return False
        self.state = RunState.CANCELLED
        task.cancel()
        return True
