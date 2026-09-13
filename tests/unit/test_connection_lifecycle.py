"""连接生命周期测试：读循环解耦、断开取消、发送串行化。"""

import asyncio
import json
from pathlib import Path
from typing import Any

from awesome_claude.core.agent.loop import AgentLoop
from awesome_claude.core.config import ServerConfig
from awesome_claude.core.llm.events import DoneEvent, TextDeltaEvent
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.core.router.dispatcher import Dispatcher, create_dispatcher
from awesome_claude.core.server.session import ClientSession
from awesome_claude.core.server.tcp import TCPServer
from awesome_claude.core.session.channel import SessionChannel
from awesome_claude.core.session.registry import SessionRegistry
from awesome_claude.core.session.run import RunState
from awesome_claude.core.task.manager import TaskManager
from awesome_claude.core.tools.registry import ToolRegistry
from awesome_claude.protocol.errors import SESSION_BUSY
from awesome_claude.protocol.jsonrpc import (
    build_request,
    decode_message,
    encode_message,
)
from awesome_claude.protocol.methods import METHOD_CHAT, METHOD_PING
from awesome_claude.shared.logging.task_tracker import TaskTracker
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


class SlowWriter:
    """带 drain 延时的假 writer，用于验证发送串行化。"""

    def __init__(self) -> None:
        self.chunks: list[bytes] = []

    def get_extra_info(self, name: str) -> Any:
        return ("127.0.0.1", 1234)

    def write(self, data: bytes) -> None:
        self.chunks.append(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


async def _build_server(
    tmp_path: Path, llm: Any
) -> tuple[TCPServer, Path, SessionRegistry]:
    tasks_dir = tmp_path / "tasks"
    tracker = TaskTracker(str(tasks_dir))
    task_manager = TaskManager(tracker)
    config = ServerConfig(api_key="k", model="m", host="127.0.0.1", port=0)
    registry = SessionRegistry(task_manager)

    def context_factory(channel: SessionChannel) -> HandlerContext:
        return HandlerContext(
            task_manager=task_manager,
            llm_client=llm,
            sessions=channel,
            config=config,
            agent_loop=AgentLoop(llm, ToolRegistry()),
        )

    server = TCPServer(
        config.host, config.port, create_dispatcher(), context_factory, registry
    )
    await server.start()
    return server, tasks_dir, registry


async def _read_until_response(
    reader: asyncio.StreamReader, request_id: int, *, timeout: float = 2.0
) -> dict[str, Any] | None:
    async def _loop() -> dict[str, Any] | None:
        while True:
            raw = await reader.readline()
            if not raw:
                return None
            msg = decode_message(raw)
            if msg.get("id") == request_id:
                return msg

    return await asyncio.wait_for(_loop(), timeout=timeout)


async def _read_until_final_stream(
    reader: asyncio.StreamReader, *, timeout: float = 2.0
) -> dict[str, Any] | None:
    async def _loop() -> dict[str, Any] | None:
        while True:
            raw = await reader.readline()
            if not raw:
                return None
            msg = decode_message(raw)
            if (
                msg.get("method") == "chat.stream"
                and msg.get("params", {}).get("is_final") is True
            ):
                return msg

    return await asyncio.wait_for(_loop(), timeout=timeout)


async def _wait_for(predicate: Any, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等待条件超时")


def _read_events(tasks_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in tasks_dir.glob("*/*.jsonl"):
        events.extend(
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        )
    return events


class TestReadLoopDecoupling:
    """3.1 读循环与请求执行解耦。"""

    async def test_ping_answered_during_chat(self, tmp_path: Path) -> None:
        llm = GatedLLM()
        server, _, _ = await _build_server(tmp_path, llm)
        reader, writer = await asyncio.open_connection(*server.bound_addr)
        try:
            writer.write(
                encode_message(build_request(METHOD_CHAT, {"message": "hi"}, 1))
            )
            await writer.drain()
            await asyncio.wait_for(llm.started.wait(), timeout=2.0)

            writer.write(encode_message(build_request(METHOD_PING, None, 2)))
            await writer.drain()

            ping = await _read_until_response(reader, 2)
            assert ping is not None
            assert ping["result"]["status"] == "ok"
            assert not llm.release.is_set()

            llm.release.set()
            chat = await _read_until_response(reader, 1)
            assert chat is not None
            assert chat["result"]["text"] == "done"
        finally:
            writer.close()
            await writer.wait_closed()
            await server.stop()


class TestDisconnectCancellation:
    """3.2 断开检测与取消编排。"""

    async def test_disconnect_cancels_run_and_records_terminal(
        self, tmp_path: Path
    ) -> None:
        llm = GatedLLM()
        server, tasks_dir, registry = await _build_server(tmp_path, llm)
        reader, writer = await asyncio.open_connection(*server.bound_addr)
        try:
            writer.write(
                encode_message(build_request("session.attach", {"session_id": "s"}, 1))
            )
            await writer.drain()
            attach = await _read_until_response(reader, 1)
            assert attach is not None

            writer.write(
                encode_message(
                    build_request(METHOD_CHAT, {"message": "hi", "session_id": "s"}, 2)
                )
            )
            await writer.drain()
            await asyncio.wait_for(llm.started.wait(), timeout=2.0)

            session = registry.get("s")
            assert session is not None
            assert session.active_run is not None
            assert session.history == []
        finally:
            writer.close()
            await writer.wait_closed()

        await _wait_for(
            lambda: (
                registry.get("s") is not None and registry.get("s").active_run is None
            )
        )
        session = registry.get("s")
        assert session is not None
        assert session.sinks == set()
        assert session.history == []

        await _wait_for(
            lambda: any(e["stage"] == "task_cancelled" for e in _read_events(tasks_dir))
        )
        cancelled = [
            e for e in _read_events(tasks_dir) if e["stage"] == "task_cancelled"
        ]
        assert len(cancelled) == 1

        await server.stop()

    async def test_disconnect_with_other_subscriber_keeps_run(
        self, tmp_path: Path
    ) -> None:
        llm = GatedLLM()
        server, _, registry = await _build_server(tmp_path, llm)
        r1, w1 = await asyncio.open_connection(*server.bound_addr)
        r2, w2 = await asyncio.open_connection(*server.bound_addr)
        try:
            for reader, writer, rid in ((r1, w1, 1), (r2, w2, 2)):
                writer.write(
                    encode_message(
                        build_request("session.attach", {"session_id": "s"}, rid)
                    )
                )
                await writer.drain()
                assert await _read_until_response(reader, rid) is not None

            w1.write(
                encode_message(
                    build_request(METHOD_CHAT, {"message": "hi", "session_id": "s"}, 3)
                )
            )
            await w1.drain()
            await asyncio.wait_for(llm.started.wait(), timeout=2.0)

            w1.close()
            await w1.wait_closed()
            await asyncio.sleep(0.1)

            session = registry.get("s")
            assert session is not None
            assert session.active_run is not None
            assert session.active_run.is_active

            llm.release.set()
            final = await _read_until_final_stream(r2)
            assert final is not None
            assert final["params"]["is_final"] is True
        finally:
            w2.close()
            await w2.wait_closed()
            await server.stop()


class TestSendSerialization:
    """3.3 发送串行化与关闭后安全丢弃。"""

    def _make_session(self, writer: Any) -> ClientSession:
        return ClientSession(
            reader=asyncio.StreamReader(),
            writer=writer,
            dispatcher=Dispatcher(),
            context_factory=lambda channel: None,  # type: ignore[arg-type,return-value]
            registry=SessionRegistry(),
        )

    async def test_concurrent_sends_not_interleaved(self) -> None:
        writer = SlowWriter()
        session = self._make_session(writer)
        await asyncio.gather(
            *(
                session._send({"jsonrpc": "2.0", "id": i, "result": {"i": i}})
                for i in range(50)
            )
        )
        data = b"".join(writer.chunks)
        lines = [line for line in data.split(b"\n") if line]
        assert len(lines) == 50
        ids = sorted(json.loads(line)["id"] for line in lines)
        assert ids == list(range(50))

    async def test_send_after_close_dropped(self) -> None:
        writer = SlowWriter()
        session = self._make_session(writer)
        session._closed = True
        await session._send({"jsonrpc": "2.0", "id": 1, "result": {}})
        assert writer.chunks == []


class TestSessionBusy:
    """4.3 每会话单活跃 Run，并发对话被拒绝。"""

    async def test_second_chat_rejected(self, tmp_path: Path) -> None:
        llm = GatedLLM()
        server, _, registry = await _build_server(tmp_path, llm)
        reader, writer = await asyncio.open_connection(*server.bound_addr)
        try:
            writer.write(
                encode_message(build_request("session.attach", {"session_id": "s"}, 1))
            )
            await writer.drain()
            assert await _read_until_response(reader, 1) is not None

            writer.write(
                encode_message(
                    build_request(
                        METHOD_CHAT, {"message": "first", "session_id": "s"}, 2
                    )
                )
            )
            await writer.drain()
            await asyncio.wait_for(llm.started.wait(), timeout=2.0)
            session = registry.get("s")
            assert session is not None and session.active_run is not None
            first_run_id = session.active_run.run_id

            writer.write(
                encode_message(
                    build_request(
                        METHOD_CHAT, {"message": "second", "session_id": "s"}, 3
                    )
                )
            )
            await writer.drain()
            busy = await _read_until_response(reader, 3)
            assert busy is not None
            assert busy["error"]["code"] == SESSION_BUSY

            assert session.active_run is not None
            assert session.active_run.run_id == first_run_id
            assert session.active_run.is_active

            llm.release.set()
            first = await _read_until_response(reader, 2)
            assert first is not None
            assert first["result"]["text"] == "done"

            writer.write(
                encode_message(
                    build_request(
                        METHOD_CHAT, {"message": "again", "session_id": "s"}, 3
                    )
                )
            )
            await writer.drain()
            second = await _read_until_response(reader, 3)
            assert second is not None
            assert second["result"]["text"] == "done"
        finally:
            writer.close()
            await writer.wait_closed()
            await server.stop()

    async def test_attach_reports_inflight_run(self, tmp_path: Path) -> None:
        """订阅正在执行对话的会话时，返回在途 Run 标识。"""
        llm = GatedLLM()
        server, _, registry = await _build_server(tmp_path, llm)
        r1, w1 = await asyncio.open_connection(*server.bound_addr)
        r2, w2 = await asyncio.open_connection(*server.bound_addr)
        try:
            w1.write(
                encode_message(build_request("session.attach", {"session_id": "s"}, 1))
            )
            await w1.drain()
            assert await _read_until_response(r1, 1) is not None
            w1.write(
                encode_message(
                    build_request(METHOD_CHAT, {"message": "hi", "session_id": "s"}, 2)
                )
            )
            await w1.drain()
            await asyncio.wait_for(llm.started.wait(), timeout=2.0)

            session = registry.get("s")
            assert session is not None and session.active_run is not None
            run_id = session.active_run.run_id

            w2.write(
                encode_message(build_request("session.attach", {"session_id": "s"}, 1))
            )
            await w2.drain()
            attach = await _read_until_response(r2, 1)
            assert attach is not None
            assert run_id in attach["result"]["active_tasks"]

            llm.release.set()
            assert await _read_until_response(r1, 2) is not None
        finally:
            w1.close()
            w2.close()
            await asyncio.gather(w1.wait_closed(), w2.wait_closed())
            await server.stop()

    async def test_cross_connection_concurrent_chat(self, tmp_path: Path) -> None:
        """两个连接同时向同一会话发起对话：恰好一个成功、一个被拒绝。"""
        llm = GatedLLM()
        server, _, registry = await _build_server(tmp_path, llm)
        r1, w1 = await asyncio.open_connection(*server.bound_addr)
        r2, w2 = await asyncio.open_connection(*server.bound_addr)
        try:
            for reader, writer, rid in ((r1, w1, 1), (r2, w2, 2)):
                writer.write(
                    encode_message(
                        build_request("session.attach", {"session_id": "s"}, rid)
                    )
                )
                await writer.drain()
                assert await _read_until_response(reader, rid) is not None

            w1.write(
                encode_message(
                    build_request(METHOD_CHAT, {"message": "a", "session_id": "s"}, 3)
                )
            )
            w2.write(
                encode_message(
                    build_request(METHOD_CHAT, {"message": "b", "session_id": "s"}, 4)
                )
            )
            await asyncio.gather(w1.drain(), w2.drain())
            await asyncio.sleep(0.05)

            session = registry.get("s")
            assert session is not None
            assert session.active_run is not None

            llm.release.set()
            resp1 = await _read_until_response(r1, 3)
            resp2 = await _read_until_response(r2, 4)
            responses = [resp1, resp2]
            errors = [r for r in responses if r is not None and "error" in r]
            results = [r for r in responses if r is not None and "result" in r]
            assert len(errors) == 1
            assert errors[0]["error"]["code"] == SESSION_BUSY
            assert len(results) == 1
            assert results[0]["result"]["text"] == "done"
        finally:
            w1.close()
            w2.close()
            await asyncio.gather(w1.wait_closed(), w2.wait_closed())
            await server.stop()


class TestCancelledRunHistory:
    """4.2 取消路径不向会话历史追加轮次。"""

    async def test_cancel_does_not_append_history(self, tmp_path: Path) -> None:
        llm = GatedLLM()
        server, _, registry = await _build_server(tmp_path, llm)
        reader, writer = await asyncio.open_connection(*server.bound_addr)
        try:
            writer.write(
                encode_message(build_request("session.attach", {"session_id": "s"}, 1))
            )
            await writer.drain()
            assert await _read_until_response(reader, 1) is not None

            writer.write(
                encode_message(
                    build_request(METHOD_CHAT, {"message": "hi", "session_id": "s"}, 2)
                )
            )
            await writer.drain()
            await asyncio.wait_for(llm.started.wait(), timeout=2.0)

            session = registry.get("s")
            assert session is not None
            run = session.active_run
            assert run is not None
            assert session.history == []

            await registry.cancel_run(run)

            assert run.state is RunState.CANCELLED
            assert session.history == []
            assert session.active_run is None
        finally:
            writer.close()
            await writer.wait_closed()
            await server.stop()


class TestServerShutdown:
    """3.4 服务端关闭取消全部在途 Run。"""

    async def test_stop_cancels_inflight_run(self, tmp_path: Path) -> None:
        llm = GatedLLM()
        server, tasks_dir, registry = await _build_server(tmp_path, llm)
        reader, writer = await asyncio.open_connection(*server.bound_addr)
        writer.write(
            encode_message(build_request("session.attach", {"session_id": "s"}, 1))
        )
        await writer.drain()
        assert await _read_until_response(reader, 1) is not None

        writer.write(
            encode_message(
                build_request(METHOD_CHAT, {"message": "hi", "session_id": "s"}, 2)
            )
        )
        await writer.drain()
        await asyncio.wait_for(llm.started.wait(), timeout=2.0)

        session = registry.get("s")
        assert session is not None
        run = session.active_run
        assert run is not None

        await server.stop()

        assert run.state is RunState.CANCELLED
        assert run.task is not None and run.task.done()
        assert session.active_run is None
        assert any(e["stage"] == "task_cancelled" for e in _read_events(tasks_dir))
        writer.close()


class TestEphemeralSession:
    """4.4 未携带 session_id 时使用临时会话，空置即销毁。"""

    async def test_ephemeral_created_then_destroyed(self, tmp_path: Path) -> None:
        llm = GatedLLM()
        server, _, registry = await _build_server(tmp_path, llm)
        reader, writer = await asyncio.open_connection(*server.bound_addr)
        try:
            writer.write(
                encode_message(build_request(METHOD_CHAT, {"message": "hi"}, 1))
            )
            await writer.drain()
            await asyncio.wait_for(llm.started.wait(), timeout=2.0)

            ephemeral = [s for s in registry._sessions.values() if s.ephemeral]
            assert len(ephemeral) == 1
            assert ephemeral[0].active_run is not None

            llm.release.set()
            response = await _read_until_response(reader, 1)
            assert response is not None
            assert response["result"]["text"] == "done"

            await _wait_for(
                lambda: not any(s.ephemeral for s in registry._sessions.values())
            )
        finally:
            writer.close()
            await writer.wait_closed()
            await server.stop()
