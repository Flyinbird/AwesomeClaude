"""轨迹存储 - 将 TraceEvent 写入 JSONL 文件。"""

import asyncio
import json
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from awesome_claude.shared.types import TraceEvent, TraceStage


class TraceStore:
    """执行轨迹存储，按 {log_dir}/{date}/{run_id}.jsonl 追加写入 JSONL。"""

    def __init__(self, log_dir: str) -> None:
        """初始化存储。

        Args:
            log_dir: 轨迹根目录（如 "logs/runs"）。
        """
        self._log_dir = Path(log_dir)
        self._lock = asyncio.Lock()

    async def log_event(
        self,
        run_id: str,
        stage: TraceStage,
        data: dict[str, Any],
        start_time: float,
        *,
        step_index: int | None = None,
    ) -> None:
        """记录一个轨迹阶段事件。

        Args:
            run_id: Run 标识。
            stage: 轨迹阶段。
            data: 附加上下文数据。
            start_time: 阶段开始时间（秒级单调时钟，与 time.monotonic 一致）。
            step_index: 所属 step 序号（多轮 agent 循环时区分轮次，可选）。
        """
        now = time.monotonic()
        event = TraceEvent(
            run_id=run_id,
            stage=stage,
            timestamp=datetime.now(UTC).isoformat(),
            duration_ms=(now - start_time) * 1000.0,
            data=data,
            step_index=step_index,
        )
        date_dir = datetime.now(UTC).strftime("%Y-%m-%d")
        path = self._log_dir / date_dir / f"{run_id}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(event), ensure_ascii=False) + "\n"
        async with self._lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)


def get_trace_store() -> TraceStore:
    """获取默认轨迹存储。

    Returns:
        写入 logs/runs 目录的 TraceStore。
    """
    return TraceStore("logs/runs")
