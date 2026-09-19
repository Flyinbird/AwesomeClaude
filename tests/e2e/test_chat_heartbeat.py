"""端到端测试：对话心跳通知的周期推送与会话可见性。"""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from awesome_claude.client.transport.connection import ClientConnection
from awesome_claude.core.agent.loop import AgentLoop
from awesome_claude.core.config import ServerConfig
from awesome_claude.core.llm.events import DoneEvent, TextDeltaEvent
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.core.router.dispatcher import create_dispatcher
from awesome_claude.core.server.tcp import TCPServer
from awesome_claude.core.session.channel import SessionChannel
from awesome_claude.core.session.registry import SessionRegistry
from awesome_claude.core.tools.registry import ToolRegistry
from awesome_claude.protocol.methods import (
    METHOD_CHAT,
    METHOD_SESSION_ATTACH,
    NOTIFY_CHAT_COMPLETED,
    NOTIFY_CHAT_HEARTBEAT,
)
from awesome_claude.shared.logging.trace_store import TraceStore
from awesome_claude.shared.types import TokenUsage
from tests.conftest import expect_chat_terminal

_HEARTBEAT_INTERVAL = 0.05


class GatedLLM:
    """受控 LLM：发出 started 后阻塞，直到 release 被设置。"""

    def __init__(self, text: str = "hi") -> None:
        self.text = text
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def chat_stream(self, messages: list[dict], **kwargs: Any) -> Any:
        self.started.set()
        await self.release.wait()
        yield TextDeltaEvent(self.text)
        yield DoneEvent(
            stop_reason="end_turn",
            full_text=self.text,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            message={
                "role": "assistant",
                "content": [{"type": "text", "text": self.text}],
            },
        )


def _collector(store: list[dict[str, Any]]) -> Any:
    async def handler(params: dict[str, Any]) -> None:
        store.append(params)

    return handler


async def _build_server(tmp_path: Path, llm: Any) -> TCPServer:
    trace_store = TraceStore(str(tmp_path / "runs"))
    config = ServerConfig(
        api_key="k",
        model="m",
        host="127.0.0.1",
        port=0,
        heartbeat_interval=_HEARTBEAT_INTERVAL,
    )
    registry = SessionRegistry()

    def context_factory(channel: SessionChannel) -> HandlerContext:
        return HandlerContext(
            trace_store=trace_store,
            llm_client=llm,
            sessions=channel,
            config=config,
            agent_loop=AgentLoop(llm, ToolRegistry()),
        )

    server = TCPServer(
        config.host, config.port, create_dispatcher(), context_factory, registry
    )
    await server.start()
    return server


async def _wait_for(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等待条件超时")


async def test_heartbeat_broadcast_in_session(tmp_path: Path) -> None:
    """命名会话内所有订阅者都能收到该 Run 的心跳。"""
    llm = GatedLLM()
    server = await _build_server(tmp_path, llm)
    c1 = ClientConnection(*server.bound_addr)
    c2 = ClientConnection(*server.bound_addr)
    try:
        await c1.connect()
        await c2.connect()
        await c1.send_request(METHOD_SESSION_ATTACH, {"session_id": "shared"})
        await c2.send_request(METHOD_SESSION_ATTACH, {"session_id": "shared"})

        beats1: list[dict[str, Any]] = []
        beats2: list[dict[str, Any]] = []
        c1.on_notification(NOTIFY_CHAT_HEARTBEAT, _collector(beats1))
        c2.on_notification(NOTIFY_CHAT_HEARTBEAT, _collector(beats2))

        terminal_future = expect_chat_terminal(c1)
        ack = await c1.send_request(
            METHOD_CHAT, {"message": "hi", "session_id": "shared"}
        )
        run_id = ack["result"]["run_id"]
        await asyncio.wait_for(llm.started.wait(), timeout=2.0)

        await _wait_for(lambda: bool(beats1) and bool(beats2))
        assert beats1[0]["run_id"] == run_id
        assert beats1[0]["session_id"] == "shared"
        assert beats2[0]["run_id"] == run_id

        llm.release.set()
        terminal = await asyncio.wait_for(terminal_future, timeout=2.0)
        assert terminal["method"] == NOTIFY_CHAT_COMPLETED
    finally:
        await c1.disconnect()
        await c2.disconnect()
        await server.stop()


async def test_heartbeat_single_cast_for_ephemeral(tmp_path: Path) -> None:
    """未指定会话时，心跳仅投递给发起连接。"""
    llm = GatedLLM()
    server = await _build_server(tmp_path, llm)
    conn = ClientConnection(*server.bound_addr)
    try:
        await conn.connect()
        beats: list[dict[str, Any]] = []
        conn.on_notification(NOTIFY_CHAT_HEARTBEAT, _collector(beats))

        terminal_future = expect_chat_terminal(conn)
        ack = await conn.send_request(METHOD_CHAT, {"message": "hi"})
        run_id = ack["result"]["run_id"]
        await asyncio.wait_for(llm.started.wait(), timeout=2.0)

        await _wait_for(lambda: bool(beats))
        assert beats[0]["run_id"] == run_id
        assert beats[0]["session_id"].startswith("ephemeral-")

        llm.release.set()
        terminal = await asyncio.wait_for(terminal_future, timeout=2.0)
        assert terminal["method"] == NOTIFY_CHAT_COMPLETED
        assert terminal["params"]["text"] == "hi"
    finally:
        await conn.disconnect()
        await server.stop()
