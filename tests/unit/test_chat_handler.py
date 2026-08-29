"""core/handlers/chat 处理器测试（mock LLMClient 与 TaskManager）。"""

import time
from typing import Any

from awesome_claude.core.agent.loop import AgentLoop
from awesome_claude.core.config import ServerConfig
from awesome_claude.core.handlers.chat import handle_chat
from awesome_claude.core.llm.events import DoneEvent, TextDeltaEvent
from awesome_claude.core.llm.exceptions import LLMAuthError, LLMTimeoutError
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.core.session.channel import SessionChannel
from awesome_claude.core.session.registry import ConnectionSink, SessionRegistry
from awesome_claude.core.tools.registry import ToolRegistry
from awesome_claude.protocol.errors import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    LLM_AUTH_ERROR,
    LLM_TIMEOUT_ERROR,
)
from awesome_claude.protocol.methods import NOTIFY_CHAT_STREAM
from awesome_claude.shared.types import TaskStage, TokenUsage


class FakeTaskManager:
    """记录调用历史的 TaskManager 替身。"""

    def __init__(self) -> None:
        self.created: list[tuple[str, str, str]] = []
        self.stages: list[tuple[str, TaskStage, dict, int | None]] = []
        self.completed: list[tuple[str, dict]] = []
        self.failed: list[tuple[str, TaskStage, str, str, int | None]] = []
        self._counter = 0

    async def create_task(self, user_input: str, client_addr: str) -> tuple[str, float]:
        self._counter += 1
        task_id = f"task{self._counter}"
        self.created.append((task_id, user_input, client_addr))
        return task_id, time.monotonic()

    async def record_stage(
        self,
        task_id: str,
        start_time: float,
        stage: TaskStage,
        data: dict,
        *,
        step_index: int | None = None,
    ) -> None:
        self.stages.append((task_id, stage, data, step_index))

    async def complete_task(self, task_id: str, start_time: float, data: dict) -> None:
        self.completed.append((task_id, data))

    async def fail_task(
        self,
        task_id: str,
        start_time: float,
        error: Exception,
        failed_stage: TaskStage,
        *,
        step_index: int | None = None,
    ) -> None:
        self.failed.append(
            (task_id, failed_stage, type(error).__name__, str(error), step_index)
        )


class FakeLLMClient:
    """可控事件序列 / 异常的 LLMClient 替身。"""

    def __init__(
        self, events: list[Any] | None = None, exc: Exception | None = None
    ) -> None:
        self._events = list(events or [])
        self._exc = exc

    async def chat_stream(self, messages: list[dict], **kwargs: Any) -> Any:
        if self._exc is not None:
            raise self._exc
        for event in self._events:
            yield event


class NotificationRecorder:
    """记录广播通知调用的替身发送端点。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send(self, method: str, params: dict) -> None:
        self.sent.append((method, params))


def _stream_events() -> list[Any]:
    return [
        TextDeltaEvent("hello "),
        TextDeltaEvent("world"),
        TextDeltaEvent("!"),
        DoneEvent(
            stop_reason="end_turn",
            full_text="hello world!",
            usage=TokenUsage(input_tokens=12, output_tokens=7),
            message={
                "role": "assistant",
                "content": [{"type": "text", "text": "hello world!"}],
            },
        ),
    ]


def make_context(
    llm: FakeLLMClient, tm: FakeTaskManager
) -> tuple[HandlerContext, NotificationRecorder]:
    """构造带替身的 HandlerContext。"""
    recorder = NotificationRecorder()
    channel = SessionChannel(ConnectionSink(recorder.send), SessionRegistry())
    context = HandlerContext(
        task_manager=tm,
        llm_client=llm,
        sessions=channel,
        config=ServerConfig(api_key="k", model="m"),
        agent_loop=AgentLoop(llm, ToolRegistry()),
    )
    return context, recorder


class TestHandleChat:
    """handle_chat 完整流程测试。"""

    async def test_full_flow(self) -> None:
        tm = FakeTaskManager()
        context, recorder = make_context(FakeLLMClient(_stream_events()), tm)

        resp = await handle_chat({"message": "hi"}, context)

        assert resp["task_id"] == "task1"
        assert resp["text"] == "hello world!"
        assert resp["stop_reason"] == "end_turn"
        assert resp["usage"]["input_tokens"] == 12
        assert resp["usage"]["output_tokens"] == 7
        assert resp["model"] == "m"
        assert resp["duration_ms"] >= 0

        assert tm.created == [("task1", "hi", "unknown")]
        assert len(tm.completed) == 1
        assert tm.completed[0][0] == "task1"

        stage_list = [s[1] for s in tm.stages]
        assert stage_list == [
            TaskStage.CONTEXT_BUILT,
            TaskStage.STEP_STARTED,
            TaskStage.LLM_REQUEST_SENT,
            TaskStage.LLM_STREAMING,
            TaskStage.LLM_RESPONSE_DONE,
        ]
        step_indices = [s[3] for s in tm.stages if s[1] != TaskStage.CONTEXT_BUILT]
        assert step_indices == [1, 1, 1, 1]

        assert len(recorder.sent) == 4
        assert all(method == NOTIFY_CHAT_STREAM for method, _ in recorder.sent)
        deltas = [params for _, params in recorder.sent if not params["is_final"]]
        finals = [params for _, params in recorder.sent if params["is_final"]]
        assert [d["text"] for d in deltas] == ["hello ", "world", "!"]
        assert [d["chunk_index"] for d in deltas] == [1, 2, 3]
        assert all(d["task_id"] == "task1" for d in deltas)
        assert len(finals) == 1
        assert finals[0]["is_final"] is True
        assert finals[0]["task_id"] == "task1"

    async def test_invalid_params_no_task(self) -> None:
        tm = FakeTaskManager()
        context, _ = make_context(FakeLLMClient(), tm)

        resp = await handle_chat({}, context)
        assert resp["error"]["code"] == INVALID_PARAMS
        assert tm.created == []

    async def test_missing_message(self) -> None:
        tm = FakeTaskManager()
        context, _ = make_context(FakeLLMClient(), tm)

        resp = await handle_chat(None, context)
        assert resp["error"]["code"] == INVALID_PARAMS
        assert tm.created == []

    async def test_llm_auth_error_fails_task(self) -> None:
        tm = FakeTaskManager()
        context, _ = make_context(FakeLLMClient(exc=LLMAuthError("bad key")), tm)

        resp = await handle_chat({"message": "hi"}, context)
        assert resp["error"]["code"] == LLM_AUTH_ERROR
        assert len(tm.failed) == 1
        assert tm.failed[0][1] == TaskStage.LLM_REQUEST_SENT
        assert tm.failed[0][2] == "LLMAuthError"
        assert tm.created != []

    async def test_llm_timeout_error(self) -> None:
        tm = FakeTaskManager()
        context, _ = make_context(FakeLLMClient(exc=LLMTimeoutError("timeout")), tm)

        resp = await handle_chat({"message": "hi"}, context)
        assert resp["error"]["code"] == LLM_TIMEOUT_ERROR
        assert tm.failed[0][1] == TaskStage.LLM_STREAMING

    async def test_generic_error_maps_to_internal_error(self) -> None:
        tm = FakeTaskManager()
        context, _ = make_context(FakeLLMClient(exc=RuntimeError("boom")), tm)

        resp = await handle_chat({"message": "hi"}, context)
        assert resp["error"]["code"] == INTERNAL_ERROR
        assert len(tm.failed) == 1
        assert tm.failed[0][2] == "RuntimeError"
