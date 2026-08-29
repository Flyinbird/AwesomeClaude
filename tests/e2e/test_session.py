"""端到端测试：多客户端共享会话（attach / 广播 / 回放）。"""

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
from awesome_claude.core.task.manager import TaskManager
from awesome_claude.core.tools.registry import ToolRegistry
from awesome_claude.protocol.methods import (
    METHOD_CHAT,
    METHOD_SESSION_ATTACH,
    NOTIFY_CHAT_STREAM,
    NOTIFY_CHAT_USER_MESSAGE,
)
from awesome_claude.shared.logging.task_tracker import TaskTracker
from awesome_claude.shared.types import TokenUsage


class FakeLLM:
    """e2e 用 fake LLM，产出固定文本回复。"""

    def __init__(self, events: list[Any]) -> None:
        self._events = list(events)

    async def chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        for event in self._events:
            yield event


def _done(text: str) -> DoneEvent:
    return DoneEvent(
        stop_reason="end_turn",
        full_text=text,
        usage=TokenUsage(input_tokens=3, output_tokens=1),
        message={"role": "assistant", "content": [{"type": "text", "text": text}]},
    )


def _collector(store: list[dict[str, Any]]) -> Any:
    async def handler(params: dict[str, Any]) -> None:
        store.append(params)

    return handler


async def _build_server(tmp_path: Path, llm: Any) -> TCPServer:
    tracker = TaskTracker(str(tmp_path / "tasks"))
    task_manager = TaskManager(tracker)
    dispatcher = create_dispatcher()
    config = ServerConfig(api_key="k", model="m", host="127.0.0.1", port=0)
    registry = SessionRegistry()

    def context_factory(channel: SessionChannel) -> HandlerContext:
        return HandlerContext(
            task_manager=task_manager,
            llm_client=llm,
            sessions=channel,
            config=config,
            agent_loop=AgentLoop(llm, ToolRegistry()),
        )

    server = TCPServer(config.host, config.port, dispatcher, context_factory, registry)
    await server.start()
    return server


async def _wait_for(predicate: Any, timeout: float = 1.0) -> None:
    """轮询等待条件成立，超时则失败。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等待条件超时")


async def test_two_clients_share_session_stream(tmp_path: Path) -> None:
    """两个客户端 attach 同一会话，一个发起 chat，两个都收到流式通知。"""
    server = await _build_server(tmp_path, FakeLLM([TextDeltaEvent("hi"), _done("hi")]))
    try:
        c1 = ClientConnection(*server.bound_addr)
        c2 = ClientConnection(*server.bound_addr)
        await c1.connect()
        await c2.connect()
        try:
            await c1.send_request(METHOD_SESSION_ATTACH, {"session_id": "shared"})
            await c2.send_request(METHOD_SESSION_ATTACH, {"session_id": "shared"})

            stream1: list[dict[str, Any]] = []
            stream2: list[dict[str, Any]] = []
            user1: list[dict[str, Any]] = []
            user2: list[dict[str, Any]] = []
            c1.on_notification(NOTIFY_CHAT_STREAM, _collector(stream1))
            c2.on_notification(NOTIFY_CHAT_STREAM, _collector(stream2))
            c1.on_notification(NOTIFY_CHAT_USER_MESSAGE, _collector(user1))
            c2.on_notification(NOTIFY_CHAT_USER_MESSAGE, _collector(user2))

            resp = await c1.send_request(
                METHOD_CHAT, {"message": "hello", "session_id": "shared"}
            )
            assert resp["result"]["text"] == "hi"

            await _wait_for(lambda: len(stream1) == 2 and len(stream2) == 2)

            assert [p["text"] for p in stream1 if not p["is_final"]] == ["hi"]
            assert [p["text"] for p in stream2 if not p["is_final"]] == ["hi"]
            # c2 收到 c1 的用户输入广播，c1 自己被排除
            await _wait_for(lambda: len(user2) == 1)
            assert user2[0]["message"] == "hello"
            assert user1 == []
        finally:
            await c1.disconnect()
            await c2.disconnect()
    finally:
        await server.stop()


async def test_attach_replays_history(tmp_path: Path) -> None:
    """后 attach 的客户端回放到会话历史。"""
    server = await _build_server(tmp_path, FakeLLM([TextDeltaEvent("hi"), _done("hi")]))
    try:
        c1 = ClientConnection(*server.bound_addr)
        await c1.connect()
        try:
            await c1.send_request(METHOD_SESSION_ATTACH, {"session_id": "shared"})
            resp = await c1.send_request(
                METHOD_CHAT, {"message": "hello", "session_id": "shared"}
            )
            assert resp["result"]["text"] == "hi"
        finally:
            await c1.disconnect()

        c2 = ClientConnection(*server.bound_addr)
        await c2.connect()
        try:
            attach = await c2.send_request(
                METHOD_SESSION_ATTACH, {"session_id": "shared"}
            )
            history = attach["result"]["history"]
            assert len(history) == 2
            assert history[0]["role"] == "user"
            assert history[0]["content"] == "hello"
            assert history[1]["role"] == "assistant"
            assert history[1]["content"] == "hi"
        finally:
            await c2.disconnect()
    finally:
        await server.stop()
