"""端到端测试：ClientConnection ↔ CoreServer 完整链路（含 chat 流式）。"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from awesome_claude.client.transport.connection import ClientConnection
from awesome_claude.core.agent.loop import AgentLoop
from awesome_claude.core.config import ServerConfig
from awesome_claude.core.llm.events import DoneEvent, TextDeltaEvent
from awesome_claude.core.llm.exceptions import LLMAuthError
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.core.router.dispatcher import create_dispatcher
from awesome_claude.core.server.tcp import TCPServer
from awesome_claude.core.session.channel import SessionChannel
from awesome_claude.core.session.registry import SessionRegistry
from awesome_claude.core.tools.registry import ToolRegistry
from awesome_claude.protocol.errors import LLM_AUTH_ERROR
from awesome_claude.protocol.methods import (
    METHOD_CHAT,
    METHOD_ECHO,
    METHOD_PING,
    NOTIFY_CHAT_COMPLETED,
    NOTIFY_CHAT_FAILED,
    NOTIFY_CHAT_STREAM,
)
from awesome_claude.shared.logging.trace_store import TraceStore
from awesome_claude.shared.types import TokenUsage
from tests.conftest import expect_chat_terminal


class FakeLLM:
    """可控事件序列 / 异常的 fake LLM。"""

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


def make_collector(
    store: list[dict[str, Any]],
) -> Callable[[dict[str, Any]], Awaitable[None]]:
    """构造将 notification params 收集进 list 的异步 handler。"""

    async def handler(params: dict[str, Any]) -> None:
        store.append(params)

    return handler


async def _build_server(tmp_path: Path, llm: Any) -> tuple[TCPServer, Path]:
    """构建新架构 TCPServer，返回 (server, 轨迹根目录)。"""
    runs_dir = tmp_path / "logs" / "runs"
    trace_store = TraceStore(str(runs_dir))
    dispatcher = create_dispatcher()
    config = ServerConfig(api_key="k", model="m", host="127.0.0.1", port=0)
    registry = SessionRegistry()

    def context_factory(channel: SessionChannel) -> HandlerContext:
        return HandlerContext(
            trace_store=trace_store,
            llm_client=llm,
            sessions=channel,
            config=config,
            agent_loop=AgentLoop(llm, ToolRegistry()),
        )

    server = TCPServer(config.host, config.port, dispatcher, context_factory, registry)
    await server.start()
    return server, runs_dir


def _read_trace_events(runs_dir: Path) -> list[dict[str, Any]]:
    """读取轨迹根目录下全部 JSONL 事件。"""
    events: list[dict[str, Any]] = []
    for path in runs_dir.glob("*/*.jsonl"):
        events.extend(
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        )
    return events


async def test_ping_echo_still_work(tmp_path: Path) -> None:
    """ping / echo 在新客户端连接下正常工作。"""
    server, _ = await _build_server(tmp_path, FakeLLM([]))
    try:
        conn = ClientConnection(*server.bound_addr)
        await conn.connect()
        try:
            ping = await conn.send_request(METHOD_PING)
            assert ping["result"]["status"] == "ok"

            echo = await conn.send_request(METHOD_ECHO, {"message": "hello"})
            assert echo["result"] == {"echo": "hello"}
        finally:
            await conn.disconnect()
    finally:
        await server.stop()


async def test_chat_full_flow(tmp_path: Path) -> None:
    """chat 完整流程：收到多个 chat.stream 通知 + 最终响应含文本与用量。"""
    events = [
        TextDeltaEvent("你好"),
        TextDeltaEvent("，"),
        TextDeltaEvent("世界"),
        DoneEvent(
            stop_reason="end_turn",
            full_text="你好，世界",
            usage=TokenUsage(input_tokens=5, output_tokens=8),
            message={
                "role": "assistant",
                "content": [{"type": "text", "text": "你好，世界"}],
            },
        ),
    ]
    server, _ = await _build_server(tmp_path, FakeLLM(events))
    notifications: list[dict[str, Any]] = []
    try:
        conn = ClientConnection(*server.bound_addr)
        await conn.connect()
        try:
            conn.on_notification(NOTIFY_CHAT_STREAM, make_collector(notifications))
            terminal_future = expect_chat_terminal(conn)
            ack = await conn.send_request(METHOD_CHAT, {"message": "hi"})
            assert ack["result"]["accepted"] is True
            run_id = ack["result"]["run_id"]

            terminal = await asyncio.wait_for(terminal_future, timeout=2.0)
            assert terminal["method"] == NOTIFY_CHAT_COMPLETED
            result = terminal["params"]
            assert result["text"] == "你好，世界"
            assert result["stop_reason"] == "end_turn"
            assert result["usage"]["input_tokens"] == 5
            assert result["usage"]["output_tokens"] == 8
            assert result["run_id"] == run_id
            assert result["model"] == "m"

            assert len(notifications) == 4
            deltas = [p for p in notifications if not p["is_final"]]
            finals = [p for p in notifications if p["is_final"]]
            assert [d["text"] for d in deltas] == ["你好", "，", "世界"]
            assert len(finals) == 1
            assert finals[0]["is_final"] is True
        finally:
            await conn.disconnect()
    finally:
        await server.stop()


async def test_trace_logs_full_lifecycle(tmp_path: Path) -> None:
    """轨迹日志：JSONL 包含完整阶段事件序列。"""
    events = [
        TextDeltaEvent("hi"),
        DoneEvent(
            stop_reason="end_turn",
            full_text="hi",
            usage=TokenUsage(input_tokens=3, output_tokens=1),
            message={
                "role": "assistant",
                "content": [{"type": "text", "text": "hi"}],
            },
        ),
    ]
    server, runs_dir = await _build_server(tmp_path, FakeLLM(events))
    try:
        conn = ClientConnection(*server.bound_addr)
        await conn.connect()
        try:
            terminal_future = expect_chat_terminal(conn)
            ack = await conn.send_request(METHOD_CHAT, {"message": "hello"})
            run_id = ack["result"]["run_id"]
            assert len(run_id) == 8
            terminal = await asyncio.wait_for(terminal_future, timeout=2.0)
            assert terminal["method"] == NOTIFY_CHAT_COMPLETED
        finally:
            await conn.disconnect()
    finally:
        await server.stop()

    date_dir = datetime.now(UTC).strftime("%Y-%m-%d")
    path = runs_dir / date_dir / f"{run_id}.jsonl"
    assert path.exists()
    stages = [json.loads(line)["stage"] for line in path.read_text().splitlines()]
    assert stages == [
        "run_created",
        "context_built",
        "step_started",
        "llm_request_sent",
        "llm_streaming",
        "llm_response_done",
        "run_completed",
    ]


async def test_llm_failure_records_run_failed(tmp_path: Path) -> None:
    """LLM 失败：客户端收到错误响应，日志记录 RUN_FAILED。"""
    server, runs_dir = await _build_server(
        tmp_path, FakeLLM(exc=LLMAuthError("bad key"))
    )
    try:
        conn = ClientConnection(*server.bound_addr)
        await conn.connect()
        try:
            terminal_future = expect_chat_terminal(conn)
            ack = await conn.send_request(METHOD_CHAT, {"message": "hi"})
            assert ack["result"]["accepted"] is True
            terminal = await asyncio.wait_for(terminal_future, timeout=2.0)
            assert terminal["method"] == NOTIFY_CHAT_FAILED
            assert terminal["params"]["error"]["code"] == LLM_AUTH_ERROR
        finally:
            await conn.disconnect()
    finally:
        await server.stop()

    events = _read_trace_events(runs_dir)
    assert events
    failed = [e for e in events if e["stage"] == "run_failed"]
    assert len(failed) == 1
    assert failed[0]["data"]["error_type"] == "LLMAuthError"
    assert failed[0]["data"]["error_message"] == "bad key"
    assert "traceback" in failed[0]["data"]
