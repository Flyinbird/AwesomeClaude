"""shared/types.py 单元测试。"""

import json
from dataclasses import asdict

from awesome_claude.shared.types import (
    ChatResponse,
    StreamChunk,
    TaskEvent,
    TaskStage,
    TokenUsage,
)


class TestTaskStage:
    """TaskStage 枚举测试。"""

    def test_values(self) -> None:
        assert TaskStage.TASK_CREATED.value == "task_created"
        assert TaskStage.CONTEXT_BUILT.value == "context_built"
        assert TaskStage.LLM_REQUEST_SENT.value == "llm_request_sent"
        assert TaskStage.LLM_STREAMING.value == "llm_streaming"
        assert TaskStage.LLM_RESPONSE_DONE.value == "llm_response_done"
        assert TaskStage.TASK_COMPLETED.value == "task_completed"
        assert TaskStage.TASK_FAILED.value == "task_failed"

    def test_member_count(self) -> None:
        assert len(TaskStage) == 7

    def test_is_str_enum(self) -> None:
        assert isinstance(TaskStage.TASK_CREATED, str)
        assert str(TaskStage.TASK_CREATED) == "task_created"


class TestTaskEvent:
    """TaskEvent dataclass 测试。"""

    def test_creation(self) -> None:
        event = TaskEvent(
            task_id="t1",
            stage=TaskStage.TASK_CREATED,
            timestamp="2026-08-27T00:00:00+00:00",
            duration_ms=1.5,
            data={"k": "v"},
        )
        assert event.task_id == "t1"
        assert event.stage is TaskStage.TASK_CREATED
        assert event.timestamp == "2026-08-27T00:00:00+00:00"
        assert event.duration_ms == 1.5
        assert event.data == {"k": "v"}

    def test_serialization_to_json(self) -> None:
        event = TaskEvent(
            task_id="t1",
            stage=TaskStage.TASK_CREATED,
            timestamp="2026-08-27T00:00:00+00:00",
            duration_ms=1.5,
            data={"k": "v"},
        )
        obj = json.loads(json.dumps(asdict(event), ensure_ascii=False))
        assert obj["task_id"] == "t1"
        assert obj["stage"] == "task_created"
        assert obj["timestamp"] == "2026-08-27T00:00:00+00:00"
        assert obj["duration_ms"] == 1.5
        assert obj["data"] == {"k": "v"}


class TestStreamChunk:
    """StreamChunk dataclass 测试。"""

    def test_creation(self) -> None:
        chunk = StreamChunk(task_id="t1", chunk_index=0, text="hello", is_final=False)
        assert chunk.task_id == "t1"
        assert chunk.chunk_index == 0
        assert chunk.text == "hello"
        assert not chunk.is_final

    def test_final_chunk(self) -> None:
        chunk = StreamChunk(task_id="t1", chunk_index=1, text="", is_final=True)
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
            task_id="t1",
            text="hello",
            stop_reason="end_turn",
            usage=usage,
            duration_ms=100.0,
            model="claude-sonnet-4-20250514",
        )
        assert resp.task_id == "t1"
        assert resp.text == "hello"
        assert resp.stop_reason == "end_turn"
        assert resp.usage is usage
        assert resp.duration_ms == 100.0
        assert resp.model == "claude-sonnet-4-20250514"

    def test_serialization_to_json(self) -> None:
        usage = TokenUsage(input_tokens=10, output_tokens=20)
        resp = ChatResponse(
            task_id="t1",
            text="hello",
            stop_reason="end_turn",
            usage=usage,
            duration_ms=100.0,
            model="m",
        )
        obj = json.loads(json.dumps(asdict(resp)))
        assert obj["task_id"] == "t1"
        assert obj["text"] == "hello"
        assert obj["stop_reason"] == "end_turn"
        assert obj["usage"]["input_tokens"] == 10
        assert obj["usage"]["output_tokens"] == 20
        assert obj["usage"]["cache_read_input_tokens"] == 0
        assert obj["duration_ms"] == 100.0
        assert obj["model"] == "m"
