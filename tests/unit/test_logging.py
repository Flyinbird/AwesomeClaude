"""shared/logging 日志系统测试。"""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from awesome_claude.shared.logging.app_logger import get_app_logger, setup_app_logging
from awesome_claude.shared.logging.task_tracker import TaskTracker, get_task_tracker
from awesome_claude.shared.types import TaskStage


def _flush_all_handlers() -> None:
    """强制刷出所有 logging handler 的缓冲内容。"""
    for handler in logging.getLogger().handlers:
        handler.flush()


class TestAppLogger:
    """app_logger 输出格式测试。"""

    def test_stdout_text_output(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        setup_app_logging(tmp_path / "server.log", level="INFO", colors=False)
        logger = get_app_logger("test.app")
        logger.info("hello", extra="world")
        out = capsys.readouterr().out
        assert "hello" in out
        assert "world" in out

    def test_file_json_output(self, tmp_path: Path) -> None:
        log_file = tmp_path / "server.log"
        setup_app_logging(log_file, level="INFO", colors=False)
        logger = get_app_logger("test.file")
        logger.info("file-marker", key="value")
        _flush_all_handlers()
        lines = log_file.read_text(encoding="utf-8").splitlines()
        assert lines
        record = json.loads(lines[0])
        assert record["event"] == "file-marker"
        assert record["key"] == "value"
        assert record["level"] == "info"
        assert "timestamp" in record

    def test_level_filtering(self, tmp_path: Path) -> None:
        log_file = tmp_path / "server.log"
        setup_app_logging(log_file, level="WARNING", colors=False)
        logger = get_app_logger("test.level")
        logger.info("should-not-appear")
        logger.warning("should-appear")
        _flush_all_handlers()
        content = log_file.read_text(encoding="utf-8")
        assert "should-not-appear" not in content
        assert "should-appear" in content

    def test_get_app_logger_returns_bound_logger(self) -> None:
        logger = get_app_logger("test.plain")
        assert logger is not None


class TestTaskTracker:
    """task_tracker JSONL 写入测试。"""

    async def test_write_jsonl(self, tmp_path: Path) -> None:
        tracker = TaskTracker(str(tmp_path))
        await tracker.log_event(
            "task-1",
            TaskStage.TASK_CREATED,
            {"k": "v"},
            start_time=time.monotonic() - 0.1,
        )
        await tracker.log_event(
            "task-1", TaskStage.TASK_COMPLETED, {}, time.monotonic()
        )

        date_dir = datetime.now(UTC).strftime("%Y-%m-%d")
        path = tmp_path / date_dir / "task-1.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2

        first = json.loads(lines[0])
        assert first["task_id"] == "task-1"
        assert first["stage"] == "task_created"
        assert first["timestamp"]
        assert first["duration_ms"] > 0
        assert first["data"] == {"k": "v"}

        second = json.loads(lines[1])
        assert second["stage"] == "task_completed"

    async def test_each_task_own_file(self, tmp_path: Path) -> None:
        tracker = TaskTracker(str(tmp_path))
        await tracker.log_event("task-1", TaskStage.TASK_CREATED, {}, time.monotonic())
        await tracker.log_event("task-2", TaskStage.TASK_CREATED, {}, time.monotonic())
        date_dir = datetime.now(UTC).strftime("%Y-%m-%d")
        assert (tmp_path / date_dir / "task-1.jsonl").exists()
        assert (tmp_path / date_dir / "task-2.jsonl").exists()

    async def test_concurrent_writes_same_file(self, tmp_path: Path) -> None:
        tracker = TaskTracker(str(tmp_path))
        start = time.monotonic()
        await asyncio.gather(
            *(
                tracker.log_event("task-x", TaskStage.LLM_STREAMING, {"i": i}, start)
                for i in range(50)
            )
        )
        date_dir = datetime.now(UTC).strftime("%Y-%m-%d")
        path = tmp_path / date_dir / "task-x.jsonl"
        assert path.exists()
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 50
        indices = {json.loads(line)["data"]["i"] for line in lines}
        assert indices == set(range(50))

    async def test_get_task_tracker_factory(self) -> None:
        tracker = get_task_tracker()
        assert isinstance(tracker, TaskTracker)
