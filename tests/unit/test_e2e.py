"""端到端集成测试：新架构 TCPServer ↔ 原始 TCP 客户端 完整链路。"""

import asyncio
from pathlib import Path
from typing import Any

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
from awesome_claude.protocol.jsonrpc import (
    build_request,
    decode_message,
    encode_message,
    parse_message,
)
from awesome_claude.protocol.methods import (
    METHOD_CHAT,
    METHOD_ECHO,
    METHOD_PING,
    METHOD_SHUTDOWN,
    NOTIFY_CHAT_STREAM,
)
from awesome_claude.shared.logging.task_tracker import TaskTracker
from awesome_claude.shared.types import TokenUsage
from tests.conftest import RpcTestClient


class FakeLLM:
    """e2e 用 fake LLM，产出可控事件序列。"""

    def __init__(self, events: list[Any]) -> None:
        self._events = list(events)

    async def chat_stream(self, messages: list[dict], **kwargs: Any) -> Any:
        for event in self._events:
            yield event


def build_server(tmp_path: Path, llm: Any) -> TCPServer:
    """构建新架构 TCPServer。"""
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

    return TCPServer(config.host, config.port, dispatcher, context_factory, registry)


async def _run_until_shutdown(server: TCPServer) -> None:
    """等待 shutdown 事件并停止服务器。"""
    await server.wait_for_shutdown()
    await server.stop()


async def test_e2e_full_chain(tmp_path: Path) -> None:
    """ping → echo → shutdown 完整链路。"""
    server = build_server(tmp_path, FakeLLM([]))
    await server.start()
    run_task = asyncio.create_task(_run_until_shutdown(server))
    conn = RpcTestClient(*server.bound_addr)
    try:
        await conn.connect()

        ping = parse_message(await conn.call(METHOD_PING, req_id=1))
        assert ping.result["status"] == "ok"

        echo = parse_message(await conn.call(METHOD_ECHO, {"message": "e2e"}, req_id=2))
        assert echo.result == {"echo": "e2e"}

        await conn.notify(METHOD_SHUTDOWN)

        await asyncio.wait_for(run_task, timeout=5.0)
        assert run_task.done()
        assert server._stop_event.is_set()

        assert await conn.reader.readline() == b""
    finally:
        await conn.close()
        if not run_task.done():
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)


async def test_e2e_chat_streams_notifications(tmp_path: Path) -> None:
    """chat 请求 → 推送 chat.stream 通知流 → 最终响应。"""
    events = [
        TextDeltaEvent("Hello "),
        TextDeltaEvent("world"),
        TextDeltaEvent("!"),
        DoneEvent(
            stop_reason="end_turn",
            full_text="Hello world!",
            usage=TokenUsage(input_tokens=12, output_tokens=7),
            message={
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello world!"}],
            },
        ),
    ]
    server = build_server(tmp_path, FakeLLM(events))
    await server.start()
    conn = RpcTestClient(*server.bound_addr)
    try:
        await conn.connect()
        conn.writer.write(
            encode_message(build_request(METHOD_CHAT, {"message": "hi"}, id=1))
        )
        await conn.writer.drain()

        notifications = []
        response = None
        for _ in range(20):
            raw = await conn.reader.readline()
            if not raw:
                break
            msg = decode_message(raw)
            if msg.get("method") == NOTIFY_CHAT_STREAM:
                notifications.append(msg)
            elif msg.get("id") == 1:
                response = msg
                break

        assert response is not None
        assert response["result"]["text"] == "Hello world!"
        assert response["result"]["stop_reason"] == "end_turn"
        assert response["result"]["usage"]["input_tokens"] == 12
        assert response["result"]["task_id"]

        assert len(notifications) == 4
        assert all(n["method"] == NOTIFY_CHAT_STREAM for n in notifications)
        deltas = [n for n in notifications if not n["params"]["is_final"]]
        finals = [n for n in notifications if n["params"]["is_final"]]
        assert [d["params"]["text"] for d in deltas] == ["Hello ", "world", "!"]
        assert len(finals) == 1
        assert finals[0]["params"]["is_final"] is True
    finally:
        await conn.close()
        await server.stop()
