"""任务管理器 - 跟踪任务生命周期。"""

import time
import traceback
import uuid
from typing import Any

from awesome_claude.shared.logging.task_tracker import TaskTracker
from awesome_claude.shared.types import TaskStage


class TaskManager:
    """任务生命周期管理器，负责记录各阶段事件。"""

    def __init__(self, task_tracker: TaskTracker) -> None:
        """初始化任务管理器。

        Args:
            task_tracker: 底层 JSONL 任务追踪器。
        """
        self._tracker = task_tracker

    async def create_task(self, user_input: str, client_addr: str) -> tuple[str, float]:
        """创建任务并记录 TASK_CREATED 事件。

        Args:
            user_input: 用户输入。
            client_addr: 客户端地址。

        Returns:
            (task_id, start_time)：task_id 为 UUID 前 8 位，start_time 为单调时钟起点。
        """
        task_id = uuid.uuid4().hex[:8]
        start_time = time.monotonic()
        await self._tracker.log_event(
            task_id,
            TaskStage.TASK_CREATED,
            {"user_input": user_input, "client_addr": client_addr},
            start_time,
        )
        return task_id, start_time

    async def record_stage(
        self,
        task_id: str,
        start_time: float,
        stage: TaskStage,
        data: dict[str, Any],
        *,
        step_index: int | None = None,
    ) -> None:
        """记录一个阶段事件，自动计算 duration_ms。

        Args:
            task_id: 任务 ID。
            start_time: 任务开始时间。
            stage: 任务阶段。
            data: 附加上下文数据。
            step_index: 所属 step 序号（可选）。
        """
        await self._tracker.log_event(
            task_id, stage, data, start_time, step_index=step_index
        )

    async def complete_task(
        self, task_id: str, start_time: float, data: dict[str, Any]
    ) -> None:
        """记录 TASK_COMPLETED 事件。

        Args:
            task_id: 任务 ID。
            start_time: 任务开始时间。
            data: 完成数据。
        """
        await self._tracker.log_event(
            task_id, TaskStage.TASK_COMPLETED, data, start_time
        )

    async def fail_task(
        self,
        task_id: str,
        start_time: float,
        error: Exception,
        failed_stage: TaskStage,
        *,
        step_index: int | None = None,
    ) -> None:
        """记录 TASK_FAILED 事件，包含错误信息与 traceback。

        Args:
            task_id: 任务 ID。
            start_time: 任务开始时间。
            error: 引发的异常。
            failed_stage: 失败时所在的任务阶段。
            step_index: 失败时所在 step 序号（可选）。
        """
        data: dict[str, Any] = {
            "failed_stage": failed_stage.value,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
        }
        await self._tracker.log_event(
            task_id, TaskStage.TASK_FAILED, data, start_time, step_index=step_index
        )
