"""端到端测试：Run 生命周期（断连取消、共享会话续播、并发拒绝）。"""

import asyncio
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
from awesome_claude.protocol.errors import SESSION_BUSY
from awesome_claude.protocol.methods import (
    METHOD_CHAT,
    METHOD_SESSION_ATTACH,
    NOTIFY_CHAT_STREAM,
)
from awesome_claude.shared.logging.trace_store import TraceStore
from awesome_claude.shared.types import TokenUsage


class GatedLLM:
    """受控 LLM：发出 started 后阻塞，直到 release 被设置。"""

    def __init__(self, text: str = "done") -> None:
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


async def _build_server(tmp_path: Path, llm: Any) -> tuple[TCPServer, SessionRegistry]:
    trace_store = TraceStore(str(tmp_path / "runs"))
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

    server = TCPServer(
        config.host, config.port, create_dispatcher(), context_factory, registry
    )
    await server.start()
    return server, registry


async def _wait_for(predicate: Any, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等待条件超时")


def _collector(store: list[dict[str, Any]]) -> Any:
    async def handler(params: dict[str, Any]) -> None:
        store.append(params)

    return handler


async def test_disconnect_cancels_run_and_cleans_up(tmp_path: Path) -> None:
    """客户端在对话执行中断开：服务端立即取消并清理，无残留 Run/死订阅。"""
    llm = GatedLLM()
    server, registry = await _build_server(tmp_path, llm)
    conn = ClientConnection(*server.bound_addr)
    try:
        await conn.connect()
        await conn.send_request(METHOD_SESSION_ATTACH, {"session_id": "shared"})
        chat = asyncio.create_task(
            conn.send_request(METHOD_CHAT, {"message": "hi", "session_id": "shared"})
        )
        await asyncio.wait_for(llm.started.wait(), timeout=2.0)

        await conn.disconnect()
        await asyncio.gather(chat, return_exceptions=True)

        await _wait_for(
            lambda: (
                registry.get("shared") is not None
                and registry.get("shared").active_run is None
            )
        )
        session = registry.get("shared")
        assert session is not None
        assert session.sinks == set()
        assert session.history == []
    finally:
        await server.stop()


async def test_other_subscriber_still_receives_stream(tmp_path: Path) -> None:
    """共享会话中一个订阅者断开，另一订阅者仍收到完整流式输出。"""
    llm = GatedLLM()
    server, _ = await _build_server(tmp_path, llm)
    c1 = ClientConnection(*server.bound_addr)
    c2 = ClientConnection(*server.bound_addr)
    try:
        await c1.connect()
        await c2.connect()
        await c1.send_request(METHOD_SESSION_ATTACH, {"session_id": "shared"})
        await c2.send_request(METHOD_SESSION_ATTACH, {"session_id": "shared"})

        stream: list[dict[str, Any]] = []
        c2.on_notification(NOTIFY_CHAT_STREAM, _collector(stream))

        chat = asyncio.create_task(
            c1.send_request(METHOD_CHAT, {"message": "hi", "session_id": "shared"})
        )
        await asyncio.wait_for(llm.started.wait(), timeout=2.0)

        await c1.disconnect()
        llm.release.set()

        await _wait_for(lambda: any(p["is_final"] for p in stream))
        deltas = [p["text"] for p in stream if not p["is_final"]]
        assert deltas == ["done"]
        await asyncio.gather(chat, return_exceptions=True)
    finally:
        await c2.disconnect()
        await server.stop()


async def test_concurrent_chat_rejected(tmp_path: Path) -> None:
    """同一会话并发对话被 -32004 拒绝，已有 Run 不受影响。"""
    llm = GatedLLM()
    server, _ = await _build_server(tmp_path, llm)
    c1 = ClientConnection(*server.bound_addr)
    c2 = ClientConnection(*server.bound_addr)
    try:
        await c1.connect()
        await c2.connect()
        await c1.send_request(METHOD_SESSION_ATTACH, {"session_id": "shared"})
        await c2.send_request(METHOD_SESSION_ATTACH, {"session_id": "shared"})

        chat = asyncio.create_task(
            c1.send_request(METHOD_CHAT, {"message": "first", "session_id": "shared"})
        )
        await asyncio.wait_for(llm.started.wait(), timeout=2.0)

        busy = await c2.send_request(
            METHOD_CHAT, {"message": "second", "session_id": "shared"}
        )
        assert "error" in busy
        assert busy["error"]["code"] == SESSION_BUSY

        llm.release.set()
        first = await chat
        assert first["result"]["text"] == "done"
    finally:
        await c1.disconnect()
        await c2.disconnect()
        await server.stop()
