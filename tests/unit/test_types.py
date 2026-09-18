"""shared/types.py 单元测试。"""

import json
from dataclasses import asdict

from awesome_claude.shared.types import (
    ChatResponse,
    StopReason,
    StreamChunk,
    TokenUsage,
    TraceEvent,
    TraceStage,
)


class TestTraceStage:
    """TraceStage 枚举测试。"""

    def test_values(self) -> None:
        assert TraceStage.RUN_CREATED.value == "run_created"
        assert TraceStage.CONTEXT_BUILT.value == "context_built"
        assert TraceStage.STEP_STARTED.value == "step_started"
        assert TraceStage.LLM_REQUEST_SENT.value == "llm_request_sent"
        assert TraceStage.LLM_STREAMING.value == "llm_streaming"
        assert TraceStage.LLM_RESPONSE_DONE.value == "llm_response_done"
        assert TraceStage.TOOL_STARTED.value == "tool_started"
        assert TraceStage.TOOL_COMPLETED.value == "tool_completed"
        assert TraceStage.TOOL_FAILED.value == "tool_failed"
        assert TraceStage.TASK_ADDED.value == "task_added"
        assert TraceStage.TASK_STARTED.value == "task_started"
        assert TraceStage.TASK_COMPLETED.value == "task_completed"
        assert TraceStage.TASK_REOPENED.value == "task_reopened"
        assert TraceStage.TASK_SUSPENDED.value == "task_suspended"
        assert TraceStage.RUN_COMPLETED.value == "run_completed"
        assert TraceStage.RUN_FAILED.value == "run_failed"
        assert TraceStage.RUN_INTERRUPTED.value == "run_interrupted"
        assert TraceStage.RUN_CANCELLED.value == "run_cancelled"

    def test_member_count(self) -> None:
        assert len(TraceStage) == 18

    def test_is_str_enum(self) -> None:
        assert isinstance(TraceStage.RUN_CREATED, str)
        assert str(TraceStage.RUN_CREATED) == "run_created"


class TestStopReason:
    """StopReason 枚举测试。"""

    def test_from_raw_known(self) -> None:
        assert StopReason.from_raw("end_turn") is StopReason.END_TURN
        assert StopReason.from_raw("tool_use") is StopReason.TOOL_USE
        assert StopReason.from_raw("max_steps") is StopReason.MAX_STEPS

    def test_from_raw_unknown(self) -> None:
        assert StopReason.from_raw("weird") is StopReason.UNKNOWN
        assert StopReason.from_raw("") is StopReason.UNKNOWN

    def test_is_str_enum(self) -> None:
        assert isinstance(StopReason.MAX_STEPS, str)
        assert StopReason.MAX_STEPS == "max_steps"


class TestTraceEvent:
    """TraceEvent dataclass 测试。"""

    def test_creation(self) -> None:
        event = TraceEvent(
            run_id="t1",
            stage=TraceStage.RUN_CREATED,
            timestamp="2026-08-27T00:00:00+00:00",
            duration_ms=1.5,
            data={"k": "v"},
        )
        assert event.run_id == "t1"
        assert event.stage is TraceStage.RUN_CREATED
        assert event.timestamp == "2026-08-27T00:00:00+00:00"
        assert event.duration_ms == 1.5
        assert event.data == {"k": "v"}
        assert event.step_index is None

    def test_step_index(self) -> None:
        event = TraceEvent(
            run_id="t1",
            stage=TraceStage.LLM_RESPONSE_DONE,
            timestamp="2026-08-27T00:00:00+00:00",
            duration_ms=1.5,
            data={},
            step_index=2,
        )
        assert event.step_index == 2

    def test_serialization_to_json(self) -> None:
        event = TraceEvent(
            run_id="t1",
            stage=TraceStage.RUN_CREATED,
            timestamp="2026-08-27T00:00:00+00:00",
            duration_ms=1.5,
            data={"k": "v"},
        )
        obj = json.loads(json.dumps(asdict(event), ensure_ascii=False))
        assert obj["run_id"] == "t1"
        assert obj["stage"] == "run_created"
        assert obj["timestamp"] == "2026-08-27T00:00:00+00:00"
        assert obj["duration_ms"] == 1.5
        assert obj["data"] == {"k": "v"}


class TestStreamChunk:
    """StreamChunk dataclass 测试。"""

    def test_creation(self) -> None:
        chunk = StreamChunk(run_id="t1", chunk_index=0, text="hello", is_final=False)
        assert chunk.run_id == "t1"
        assert chunk.chunk_index == 0
        assert chunk.text == "hello"
        assert not chunk.is_final

    def test_final_chunk(self) -> None:
        chunk = StreamChunk(run_id="t1", chunk_index=1, text="", is_final=True)
        assert chunk.is_final
        assert chunk.text == ""


class TestTokenUsage:
    """TokenUsage dataclass 测试。"""

    def test_defaults(self) -> None:
        usage = TokenUsage(input_tokens=10, output_tokens=20)
        assert usage.cache_creation_input_tokens == 0
        assert usage.cache_read_input_tokens == 0

    def test_full(self) -> None:
        usage = TokenUsage(
            input_tokens=10,
            output_tokens=20,
            cache_creation_input_tokens=5,
            cache_read_input_tokens=6,
        )
        assert usage.input_tokens == 10
        assert usage.output_tokens == 20
        assert usage.cache_creation_input_tokens == 5
        assert usage.cache_read_input_tokens == 6


class TestChatResponse:
    """ChatResponse dataclass 测试。"""

    def test_creation(self) -> None:
        usage = TokenUsage(input_tokens=10, output_tokens=20)
        resp = ChatResponse(
            run_id="t1",
            text="hello",
            stop_reason=StopReason.END_TURN,
            usage=usage,
            duration_ms=100.0,
            model="claude-sonnet-4-20250514",
        )
        assert resp.run_id == "t1"
        assert resp.text == "hello"
        assert resp.stop_reason == "end_turn"
        assert resp.usage is usage
        assert resp.duration_ms == 100.0
        assert resp.model == "claude-sonnet-4-20250514"

    def test_serialization_to_json(self) -> None:
        usage = TokenUsage(input_tokens=10, output_tokens=20)
        resp = ChatResponse(
            run_id="t1",
            text="hello",
            stop_reason=StopReason.END_TURN,
            usage=usage,
            duration_ms=100.0,
            model="m",
        )
        obj = json.loads(json.dumps(asdict(resp)))
        assert obj["run_id"] == "t1"
        assert obj["text"] == "hello"
        assert obj["stop_reason"] == "end_turn"
        assert obj["usage"]["input_tokens"] == 10
        assert obj["usage"]["output_tokens"] == 20
        assert obj["usage"]["cache_read_input_tokens"] == 0
        assert obj["duration_ms"] == 100.0
        assert obj["model"] == "m"
