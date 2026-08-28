"""core/task 任务管理测试。"""

import json
from datetime import UTC, datetime
from pathlib import Path

from awesome_claude.core.task.manager import TaskManager
from awesome_claude.shared.logging.task_tracker import TaskTracker
from awesome_claude.shared.types import TaskStage


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TestTaskManager:
    """TaskManager 生命周期事件记录测试。"""

    async def test_create_task_writes_event(self, tmp_path: Path) -> None:
        tracker = TaskTracker(str(tmp_path))
        manager = TaskManager(tracker)
        task_id, start_time = await manager.create_task("hello", "127.0.0.1:9000")

        assert len(task_id) == 8
        assert task_id.isalnum()
        assert start_time > 0

        date_dir = datetime.now(UTC).strftime("%Y-%m-%d")
        events = _read_lines(tmp_path / date_dir / f"{task_id}.jsonl")
        assert len(events) == 1
        assert events[0]["stage"] == "task_created"
        assert events[0]["data"]["user_input"] == "hello"
        assert events[0]["data"]["client_addr"] == "127.0.0.1:9000"

    async def test_task_id_unique_and_format(self, tmp_path: Path) -> None:
        manager = TaskManager(TaskTracker(str(tmp_path)))
        ids = {(await manager.create_task("a", "x"))[0] for _ in range(20)}
        assert len(ids) == 20
        for task_id in ids:
            assert len(task_id) == 8
            assert task_id.isalnum()

    async def test_stage_sequence(self, tmp_path: Path) -> None:
        tracker = TaskTracker(str(tmp_path))
        manager = TaskManager(tracker)
        task_id, start = await manager.create_task("hi", "x")
        await manager.record_stage(
            task_id, start, TaskStage.CONTEXT_BUILT, {"message_count": 1}
        )
        await manager.complete_task(task_id, start, {"ok": True})

        date_dir = datetime.now(UTC).strftime("%Y-%m-%d")
        events = _read_lines(tmp_path / date_dir / f"{task_id}.jsonl")
        stages = [e["stage"] for e in events]
        assert stages == ["task_created", "context_built", "task_completed"]

    async def test_fail_task_includes_traceback(self, tmp_path: Path) -> None:
        tracker = TaskTracker(str(tmp_path))
        manager = TaskManager(tracker)
        task_id, start = await manager.create_task("hi", "x")
        try:
            raise ValueError("boom")
        except ValueError as exc:
            await manager.fail_task(task_id, start, exc, TaskStage.LLM_STREAMING)

        date_dir = datetime.now(UTC).strftime("%Y-%m-%d")
        events = _read_lines(tmp_path / date_dir / f"{task_id}.jsonl")
        failed = events[-1]
        assert failed["stage"] == "task_failed"
        assert failed["data"]["failed_stage"] == "llm_streaming"
        assert failed["data"]["error_type"] == "ValueError"
        assert failed["data"]["error_message"] == "boom"
        assert "Traceback" in failed["data"]["traceback"]

    async def test_step_index_recorded(self, tmp_path: Path) -> None:
        tracker = TaskTracker(str(tmp_path))
        manager = TaskManager(tracker)
        task_id, start = await manager.create_task("hi", "x")
        await manager.record_stage(
            task_id, start, TaskStage.STEP_STARTED, {}, step_index=1
        )
        await manager.record_stage(
            task_id, start, TaskStage.LLM_RESPONSE_DONE, {}, step_index=1
        )
        await manager.record_stage(
            task_id, start, TaskStage.STEP_STARTED, {}, step_index=2
        )
        await manager.complete_task(task_id, start, {})

        date_dir = datetime.now(UTC).strftime("%Y-%m-%d")
        events = _read_lines(tmp_path / date_dir / f"{task_id}.jsonl")
        assert [e["step_index"] for e in events] == [None, 1, 1, 2, None]

    async def test_fail_task_with_step_index(self, tmp_path: Path) -> None:
        tracker = TaskTracker(str(tmp_path))
        manager = TaskManager(tracker)
        task_id, start = await manager.create_task("hi", "x")
        try:
            raise RuntimeError("boom")
        except RuntimeError as exc:
            await manager.fail_task(
                task_id, start, exc, TaskStage.LLM_STREAMING, step_index=2
            )

        date_dir = datetime.now(UTC).strftime("%Y-%m-%d")
        events = _read_lines(tmp_path / date_dir / f"{task_id}.jsonl")
        assert events[-1]["step_index"] == 2
