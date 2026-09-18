"""core/handlers/chat 处理器测试（mock LLMClient 与 TraceRecorder）。"""

import json
import time
from typing import Any
from unittest.mock import MagicMock

from awesome_claude.core.agent.loop import AgentLoop
from awesome_claude.core.config import ServerConfig
from awesome_claude.core.handlers.chat import handle_chat
from awesome_claude.core.llm.events import DoneEvent, TextDeltaEvent, ToolUseEndEvent
from awesome_claude.core.llm.exceptions import LLMAuthError, LLMTimeoutError
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.core.session.channel import SessionChannel
from awesome_claude.core.session.registry import ConnectionSink, SessionRegistry
from awesome_claude.core.session.run import Run, RunState
from awesome_claude.core.tools.base import Tool
from awesome_claude.core.tools.registry import ToolRegistry
from awesome_claude.protocol.errors import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    LLM_AUTH_ERROR,
    LLM_TIMEOUT_ERROR,
)
from awesome_claude.protocol.methods import NOTIFY_CHAT_INTERRUPTED, NOTIFY_CHAT_STREAM
from awesome_claude.shared.types import StopReason, TokenUsage, TraceStage


class FakeRecorder:
    """记录调用历史的 TraceRecorder 替身。"""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.stages: list[tuple[TraceStage, dict, int | None]] = []
        self.completed: list[dict] = []
        self.interrupted: list[tuple[dict, int | None]] = []
        self.failed: list[tuple[TraceStage, str, str, int | None]] = []

    async def run_created(self, data: dict) -> None:
        self.created.append(data)

    async def record(
        self,
        stage: TraceStage,
        data: dict,
        *,
        step_index: int | None = None,
    ) -> None:
        self.stages.append((stage, data, step_index))

    async def run_completed(self, data: dict) -> None:
        self.completed.append(data)

    async def run_interrupted(
        self, data: dict, *, step_index: int | None = None
    ) -> None:
        self.interrupted.append((data, step_index))

    async def run_failed(
        self,
        error: Exception,
        failed_stage: TraceStage,
        *,
        step_index: int | None = None,
    ) -> None:
        self.failed.append((failed_stage, type(error).__name__, str(error), step_index))


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
    llm: FakeLLMClient,
    recorder: FakeRecorder,
    *,
    agent_loop: AgentLoop | None = None,
) -> tuple[HandlerContext, NotificationRecorder]:
    """构造带替身的 HandlerContext（绑定一个已带记录器的 Run）。"""
    notif = NotificationRecorder()
    channel = SessionChannel(ConnectionSink(notif.send), SessionRegistry())
    run = Run("run1", "s1", time.monotonic(), recorder=recorder)
    context = HandlerContext(
        trace_store=MagicMock(),
        llm_client=llm,
        sessions=channel,
        config=ServerConfig(api_key="k", model="m"),
        agent_loop=agent_loop or AgentLoop(llm, ToolRegistry()),
        run=run,
    )
    return context, notif


class TestHandleChat:
    """handle_chat 完整流程测试。"""

    async def test_full_flow(self) -> None:
        recorder = FakeRecorder()
        context, notif = make_context(FakeLLMClient(_stream_events()), recorder)

        resp = await handle_chat({"message": "hi"}, context)

        assert resp["run_id"] == "run1"
        assert resp["text"] == "hello world!"
        assert resp["stop_reason"] == "end_turn"
        assert resp["usage"]["input_tokens"] == 12
        assert resp["usage"]["output_tokens"] == 7
        assert resp["model"] == "m"
        assert resp["duration_ms"] >= 0

        assert len(recorder.completed) == 1
        stage_list = [s[0] for s in recorder.stages]
        assert stage_list == [
            TraceStage.CONTEXT_BUILT,
            TraceStage.STEP_STARTED,
            TraceStage.LLM_REQUEST_SENT,
            TraceStage.LLM_STREAMING,
            TraceStage.LLM_RESPONSE_DONE,
        ]
        step_indices = [
            s[2] for s in recorder.stages if s[0] != TraceStage.CONTEXT_BUILT
        ]
        assert step_indices == [1, 1, 1, 1]

        assert len(notif.sent) == 4
        assert all(method == NOTIFY_CHAT_STREAM for method, _ in notif.sent)
        deltas = [params for _, params in notif.sent if not params["is_final"]]
        finals = [params for _, params in notif.sent if params["is_final"]]
        assert [d["text"] for d in deltas] == ["hello ", "world", "!"]
        assert [d["chunk_index"] for d in deltas] == [1, 2, 3]
        assert all(d["run_id"] == "run1" for d in deltas)
        assert len(finals) == 1
        assert finals[0]["is_final"] is True
        assert finals[0]["run_id"] == "run1"

    async def test_invalid_params_no_stages(self) -> None:
        recorder = FakeRecorder()
        context, _ = make_context(FakeLLMClient(), recorder)

        resp = await handle_chat({}, context)
        assert resp["error"]["code"] == INVALID_PARAMS
        assert recorder.stages == []

    async def test_missing_message(self) -> None:
        recorder = FakeRecorder()
        context, _ = make_context(FakeLLMClient(), recorder)

        resp = await handle_chat(None, context)
        assert resp["error"]["code"] == INVALID_PARAMS
        assert recorder.stages == []

    async def test_llm_auth_error_fails_run(self) -> None:
        recorder = FakeRecorder()
        context, _ = make_context(FakeLLMClient(exc=LLMAuthError("bad key")), recorder)

        resp = await handle_chat({"message": "hi"}, context)
        assert resp["error"]["code"] == LLM_AUTH_ERROR
        assert len(recorder.failed) == 1
        assert recorder.failed[0][0] == TraceStage.LLM_REQUEST_SENT
        assert recorder.failed[0][1] == "LLMAuthError"

    async def test_llm_timeout_error(self) -> None:
        recorder = FakeRecorder()
        context, _ = make_context(
            FakeLLMClient(exc=LLMTimeoutError("timeout")), recorder
        )

        resp = await handle_chat({"message": "hi"}, context)
        assert resp["error"]["code"] == LLM_TIMEOUT_ERROR
        assert recorder.failed[0][0] == TraceStage.LLM_STREAMING

    async def test_generic_error_maps_to_internal_error(self) -> None:
        recorder = FakeRecorder()
        context, _ = make_context(FakeLLMClient(exc=RuntimeError("boom")), recorder)

        resp = await handle_chat({"message": "hi"}, context)
        assert resp["error"]["code"] == INTERNAL_ERROR
        assert len(recorder.failed) == 1
        assert recorder.failed[0][1] == "RuntimeError"

    async def test_max_steps_interruption(self) -> None:
        async def noop(args: dict[str, Any], ctx: Any, scope: Any = None) -> str:
            return "ok"

        tool_events = [
            ToolUseEndEvent("t1", "noop", {}),
            DoneEvent(
                stop_reason=StopReason.TOOL_USE,
                full_text="",
                usage=TokenUsage(input_tokens=1, output_tokens=1),
                message={
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "noop",
                            "input": {},
                        }
                    ],
                },
            ),
        ]
        llm = FakeLLMClient(tool_events)
        registry = ToolRegistry()
        registry.register(
            Tool(name="noop", description="d", input_schema={}, handler=noop)
        )
        loop = AgentLoop(llm, registry, max_steps=2)

        recorder = FakeRecorder()
        context, notif = make_context(llm, recorder, agent_loop=loop)

        resp = await handle_chat({"message": "hi"}, context)

        assert resp["stop_reason"] == "max_steps"
        assert len(recorder.completed) == 0
        assert len(recorder.interrupted) == 1
        interrupted = [
            method for method, _ in notif.sent if method == NOTIFY_CHAT_INTERRUPTED
        ]
        assert len(interrupted) == 1

    async def test_long_tool_result_clipped_in_trace(self) -> None:
        async def emit(args: dict[str, Any], ctx: Any, scope: Any = None) -> str:
            return "x" * 300

        tool_events = [
            ToolUseEndEvent("t1", "emit", {}),
            DoneEvent(
                stop_reason=StopReason.TOOL_USE,
                full_text="",
                usage=TokenUsage(input_tokens=1, output_tokens=1),
                message={
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "emit",
                            "input": {},
                        }
                    ],
                },
            ),
        ]
        llm = FakeLLMClient(tool_events)
        registry = ToolRegistry()
        registry.register(
            Tool(name="emit", description="d", input_schema={}, handler=emit)
        )
        loop = AgentLoop(llm, registry, max_steps=2)

        recorder = FakeRecorder()
        context, _ = make_context(llm, recorder, agent_loop=loop)

        await handle_chat({"message": "hi"}, context)

        tool_done = [
            (data, step)
            for stage, data, step in recorder.stages
            if stage == TraceStage.TOOL_COMPLETED and data["tool_name"] == "emit"
        ]
        assert tool_done, "应存在 TOOL_COMPLETED 阶段"
        for data, _ in tool_done:
            assert data["content_truncated"] is True
            assert "省略" in data["content"]
            assert data["content"].startswith("x" * 100)
            assert data["content"].endswith("x" * 100)
            assert len(data["content"]) < 300

        request_step2 = [
            data
            for stage, data, step in recorder.stages
            if stage == TraceStage.LLM_REQUEST_SENT and step == 2
        ]
        assert request_step2, "应存在 step 2 的 LLM_REQUEST_SENT"
        clipped = json.dumps(request_step2[0], ensure_ascii=False)
        assert "省略" in clipped
        assert ("x" * 300) not in clipped


class TestHandleChatRun:
    """对话处理器包装为 Run 的终态记录测试。"""

    async def test_run_completed(self) -> None:
        recorder = FakeRecorder()
        context, _ = make_context(FakeLLMClient(_stream_events()), recorder)
        assert context.run is not None

        resp = await handle_chat({"message": "hi"}, context)

        assert resp["text"] == "hello world!"
        assert context.run.state is RunState.COMPLETED

    async def test_run_failed_on_llm_error(self) -> None:
        recorder = FakeRecorder()
        context, _ = make_context(FakeLLMClient(exc=LLMAuthError("bad key")), recorder)
        assert context.run is not None

        resp = await handle_chat({"message": "hi"}, context)

        assert resp["error"]["code"] == LLM_AUTH_ERROR
        assert context.run.state is RunState.FAILED

    async def test_run_interrupted_on_max_steps(self) -> None:
        async def noop(args: dict[str, Any], ctx: Any, scope: Any = None) -> str:
            return "ok"

        tool_events = [
            ToolUseEndEvent("t1", "noop", {}),
            DoneEvent(
                stop_reason=StopReason.TOOL_USE,
                full_text="",
                usage=TokenUsage(input_tokens=1, output_tokens=1),
                message={
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "t1",
                            "name": "noop",
                            "input": {},
                        }
                    ],
                },
            ),
        ]
        llm = FakeLLMClient(tool_events)
        registry = ToolRegistry()
        registry.register(
            Tool(name="noop", description="d", input_schema={}, handler=noop)
        )
        loop = AgentLoop(llm, registry, max_steps=2)

        recorder = FakeRecorder()
        context, _ = make_context(llm, recorder, agent_loop=loop)
        assert context.run is not None

        resp = await handle_chat({"message": "hi"}, context)

        assert resp["stop_reason"] == "max_steps"
        assert context.run.state is RunState.INTERRUPTED
