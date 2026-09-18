"""Run 作用域的轨迹记录器：绑定 run_id 与单调起点，记录阶段事件。"""

import traceback
from typing import Any

from awesome_claude.shared.logging.trace_store import TraceStore
from awesome_claude.shared.types import TraceStage


class TraceRecorder:
    """绑定单个 Run 的轨迹记录器。

    构造时绑定 `run_id` 与单调时钟起点，调用点只需给出阶段与数据，
    无需重复传递 Run 标识与起点。
    """

    def __init__(self, store: TraceStore, run_id: str, start_time: float) -> None:
        """初始化记录器。

        Args:
            store: 底层轨迹存储。
            run_id: 所属 Run 标识。
            start_time: Run 的单调时钟起点。
        """
        self._store = store
        self._run_id = run_id
        self._start_time = start_time

    @property
    def run_id(self) -> str:
        """所属 Run 标识。"""
        return self._run_id

    async def run_created(self, data: dict[str, Any]) -> None:
        """记录 Run 创建事件。

        Args:
            data: 创建上下文（如用户输入、客户端地址）。
        """
        await self._store.log_event(
            self._run_id, TraceStage.RUN_CREATED, data, self._start_time
        )

    async def record(
        self,
        stage: TraceStage,
        data: dict[str, Any],
        *,
        step_index: int | None = None,
    ) -> None:
        """记录一个阶段事件，耗时相对 Run 起点计算。

        Args:
            stage: 轨迹阶段。
            data: 附加上下文数据。
            step_index: 所属 step 序号（可选）。
        """
        await self._store.log_event(
            self._run_id, stage, data, self._start_time, step_index=step_index
        )

    async def run_completed(self, data: dict[str, Any]) -> None:
        """记录 Run 完成事件。

        Args:
            data: 完成数据。
        """
        await self._store.log_event(
            self._run_id, TraceStage.RUN_COMPLETED, data, self._start_time
        )

    async def run_interrupted(
        self, data: dict[str, Any], *, step_index: int | None = None
    ) -> None:
        """记录 Run 中断事件（达到步数上限）。

        Args:
            data: 中断数据。
            step_index: 中断时所在 step 序号（可选）。
        """
        await self._store.log_event(
            self._run_id,
            TraceStage.RUN_INTERRUPTED,
            data,
            self._start_time,
            step_index=step_index,
        )

    async def run_cancelled(self, data: dict[str, Any]) -> None:
        """记录 Run 取消事件。

        Args:
            data: 取消上下文。
        """
        await self._store.log_event(
            self._run_id, TraceStage.RUN_CANCELLED, data, self._start_time
        )

    async def run_failed(
        self,
        error: Exception,
        failed_stage: TraceStage,
        *,
        step_index: int | None = None,
    ) -> None:
        """记录 Run 失败事件，包含错误信息与 traceback。

        Args:
            error: 引发的异常。
            failed_stage: 失败时所在的轨迹阶段。
            step_index: 失败时所在 step 序号（可选）。
        """
        data: dict[str, Any] = {
            "failed_stage": failed_stage.value,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "traceback": traceback.format_exc(),
        }
        await self._store.log_event(
            self._run_id,
            TraceStage.RUN_FAILED,
            data,
            self._start_time,
            step_index=step_index,
        )
