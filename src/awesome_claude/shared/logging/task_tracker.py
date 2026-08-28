"""任务日志追踪器 - 将 TaskEvent 写入 JSONL 文件。"""

import asyncio
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from awesome_claude.shared.types import TaskEvent, TaskStage


class TaskTracker:
    """任务事件追踪器，按 {log_dir}/{date}/{task_id}.jsonl 追加写入 JSONL。"""

    def __init__(self, log_dir: str) -> None:
        """初始化追踪器。

        Args:
            log_dir: 日志根目录（如 "logs/tasks"）。
        """
        self._log_dir = Path(log_dir)
        self._lock = asyncio.Lock()

    async def log_event(
        self,
        task_id: str,
        stage: TaskStage,
        data: dict[str, Any],
        start_time: float,
    ) -> None:
        """记录一个任务阶段事件。

        Args:
            task_id: 任务 ID。
            stage: 任务阶段。
            data: 附加上下文数据。
            start_time: 阶段开始时间（秒级单调时钟，与 time.monotonic 一致）。
        """
        now = time.monotonic()
        event = TaskEvent(
            task_id=task_id,
            stage=stage,
            timestamp=datetime.now(UTC).isoformat(),
            duration_ms=(now - start_time) * 1000.0,
            data=data,
        )
        date_dir = datetime.now(UTC).strftime("%Y-%m-%d")
        path = self._log_dir / date_dir / f"{task_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(event), ensure_ascii=False) + "\n"
        async with self._lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)


def get_task_tracker() -> TaskTracker:
    """获取默认任务追踪器。

    Returns:
        写入 logs/tasks 目录的 TaskTracker。
    """
    return TaskTracker("logs/tasks")
