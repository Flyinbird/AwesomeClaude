"""core/observability TraceRecorder 测试。"""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from awesome_claude.core.observability.trace_recorder import TraceRecorder
from awesome_claude.shared.logging.trace_store import TraceStore
from awesome_claude.shared.types import TraceStage


def _read_events(root: Path, run_id: str) -> list[dict]:
    date_dir = datetime.now(UTC).strftime("%Y-%m-%d")
    path = root / date_dir / f"{run_id}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


class TestTraceRecorder:
    """TraceRecorder 绑定 Run 后记录阶段事件测试。"""

    async def test_run_created_writes_event(self, tmp_path: Path) -> None:
        recorder = TraceRecorder(TraceStore(str(tmp_path)), "run1", time.monotonic())
        await recorder.run_created({"user_input": "hello", "client_addr": "1.2.3.4"})

        events = _read_events(tmp_path, "run1")
        assert len(events) == 1
        assert events[0]["run_id"] == "run1"
        assert events[0]["stage"] == "run_created"
        assert events[0]["data"]["user_input"] == "hello"
        assert events[0]["data"]["client_addr"] == "1.2.3.4"

    async def test_record_binds_run_id_and_start_time(self, tmp_path: Path) -> None:
        start = time.monotonic() - 0.05
        recorder = TraceRecorder(TraceStore(str(tmp_path)), "run2", start)
        await recorder.run_created({})
        await recorder.record(TraceStage.STEP_STARTED, {"k": 1}, step_index=3)
        await recorder.record(TraceStage.LLM_RESPONSE_DONE, {}, step_index=3)
        await recorder.run_completed({"ok": True})

        events = _read_events(tmp_path, "run2")
        assert [e["stage"] for e in events] == [
            "run_created",
            "step_started",
            "llm_response_done",
            "run_completed",
        ]
        assert all(e["run_id"] == "run2" for e in events)
        assert [e["step_index"] for e in events] == [None, 3, 3, None]
        # 耗时相对 Run 起点，而非记录时刻
        assert events[1]["duration_ms"] > 40

    async def test_run_interrupted_carries_step_index(self, tmp_path: Path) -> None:
        recorder = TraceRecorder(TraceStore(str(tmp_path)), "run3", time.monotonic())
        await recorder.run_created({})
        await recorder.run_interrupted({"stop_reason": "max_steps"}, step_index=5)

        events = _read_events(tmp_path, "run3")
        assert events[-1]["stage"] == "run_interrupted"
        assert events[-1]["step_index"] == 5

    async def test_run_cancelled(self, tmp_path: Path) -> None:
        recorder = TraceRecorder(TraceStore(str(tmp_path)), "run4", time.monotonic())
        await recorder.run_created({})
        await recorder.run_cancelled({"session_id": "s"})

        events = _read_events(tmp_path, "run4")
        assert events[-1]["stage"] == "run_cancelled"
        assert events[-1]["data"]["session_id"] == "s"

    async def test_run_failed_includes_traceback(self, tmp_path: Path) -> None:
        recorder = TraceRecorder(TraceStore(str(tmp_path)), "run5", time.monotonic())
        await recorder.run_created({})
        try:
            raise ValueError("boom")
        except ValueError as exc:
            await recorder.run_failed(exc, TraceStage.LLM_STREAMING, step_index=2)

        events = _read_events(tmp_path, "run5")
        failed = events[-1]
        assert failed["stage"] == "run_failed"
        assert failed["data"]["failed_stage"] == "llm_streaming"
        assert failed["data"]["error_type"] == "ValueError"
        assert failed["data"]["error_message"] == "boom"
        assert "Traceback" in failed["data"]["traceback"]
        assert failed["step_index"] == 2
