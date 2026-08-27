"""客户端测试：ClientConnection 集成 + CLI 逻辑。"""

import socket
from typing import Any

import pytest

from awesome_claude.client.cli import (
    HELP_TEXT,
    dispatch_command,
    format_response,
    parse_args,
    process_line,
)
from awesome_claude.client.connection import ClientConnection
from awesome_claude.core.config import ServerConfig
from awesome_claude.core.server import CoreServer
from awesome_claude.protocol.jsonrpc import (
    METHOD_NOT_FOUND,
    build_response,
    parse_message,
)
from awesome_claude.protocol.methods import METHOD_ECHO, METHOD_PING, METHOD_SHUTDOWN


class FakeConnection:
    """模拟客户端连接的测试替身。"""

    def __init__(self, responses: list[dict[str, Any]] | None = None) -> None:
        self.responses = list(responses or [])
        self.calls: list[tuple[str, Any, Any]] = []

    async def send_request(self, method: str, params: Any = None) -> dict[str, Any]:
        self.calls.append(("request", method, params))
        return self.responses.pop(0)

    async def send_notification(self, method: str, params: Any = None) -> None:
        self.calls.append(("notification", method, params))


@pytest.fixture
async def server():
    srv = CoreServer(ServerConfig(host="127.0.0.1", port=0))
    await srv.start()
    yield srv
    await srv.stop()


class TestParseArgs:
    """命令行参数解析测试。"""

    def test_defaults(self) -> None:
        args = parse_args([])
        assert args.host == "127.0.0.1"
        assert args.port == 9527

    def test_custom(self) -> None:
        args = parse_args(["--host", "localhost", "--port", "1234"])
        assert args.host == "localhost"
        assert args.port == 1234


class TestFormatResponse:
    """响应格式化测试。"""

    def test_success(self) -> None:
        assert format_response({"result": {"echo": "hi"}}) == '{"echo": "hi"}'

    def test_error(self) -> None:
        text = format_response(
            {"error": {"code": METHOD_NOT_FOUND, "message": "Method not found"}}
        )
        assert "32601" in text
        assert "Method not found" in text

    def test_error_with_data(self) -> None:
        text = format_response(
            {"error": {"code": -32000, "message": "bad", "data": {"x": 1}}}
        )
        assert "data={'x': 1}" in text


class TestDispatch:
    """命令分发测试。"""

    async def test_ping(self) -> None:
        conn = FakeConnection([build_response({"status": "ok", "timestamp": "t"}, 1)])
        text = await dispatch_command(conn, "/ping")
        assert text == '{"status": "ok", "timestamp": "t"}'
        assert conn.calls == [("request", METHOD_PING, None)]

    async def test_echo(self) -> None:
        conn = FakeConnection([build_response({"echo": "hello"}, 2)])
        text = await dispatch_command(conn, "/echo hello")
        assert text == '{"echo": "hello"}'
        assert conn.calls == [("request", METHOD_ECHO, {"message": "hello"})]

    async def test_echo_no_message_shows_usage(self) -> None:
        conn = FakeConnection([])
        text = await dispatch_command(conn, "/echo")
        assert text.startswith("用法:")
        assert conn.calls == []

    async def test_echo_whitespace_only_shows_usage(self) -> None:
        conn = FakeConnection([])
        text = await dispatch_command(conn, "/echo   ")
        assert text.startswith("用法:")
        assert conn.calls == []

    async def test_plain_text_sends_echo(self) -> None:
        conn = FakeConnection([build_response({"echo": "hello world"}, 3)])
        text = await dispatch_command(conn, "hello world")
        assert text == '{"echo": "hello world"}'
        assert conn.calls == [("request", METHOD_ECHO, {"message": "hello world"})]

    async def test_unknown_command(self) -> None:
        conn = FakeConnection([])
        text = await dispatch_command(conn, "/bogus")
        assert text == "未知命令: /bogus"
        assert conn.calls == []

    async def test_help(self) -> None:
        conn = FakeConnection([])
        text = await dispatch_command(conn, "/help")
        assert text == HELP_TEXT


class TestProcessLine:
    """输入行处理测试。"""

    async def test_quit_sends_shutdown(self) -> None:
        conn = FakeConnection([])
        cont = await process_line(conn, "/quit")
        assert cont is False
        assert conn.calls == [("notification", METHOD_SHUTDOWN, None)]

    async def test_exit_alias(self) -> None:
        conn = FakeConnection([])
        assert await process_line(conn, "/exit") is False

    async def test_empty_line_continues(self) -> None:
        conn = FakeConnection([])
        assert await process_line(conn, "") is True
        assert await process_line(conn, "   ") is True

    async def test_regular_line_prints_result(
        self, capsys: pytest.CaptureFixture
    ) -> None:
        conn = FakeConnection([build_response({"echo": "yo"}, 1)])
        cont = await process_line(conn, "yo")
        assert cont is True
        assert '{"echo": "yo"}' in capsys.readouterr().out

    async def test_disconnect_propagates_connection_error(self) -> None:
        class BrokenConnection:
            async def send_request(
                self, method: str, params: Any = None
            ) -> dict[str, Any]:
                raise ConnectionError("broken")

            async def send_notification(self, method: str, params: Any = None) -> None:
                raise ConnectionError("broken")

        with pytest.raises(ConnectionError):
            await process_line(BrokenConnection(), "hi")


class TestClientConnection:
    """与真实 CoreServer 的集成测试。"""

    async def test_ping(self, server: CoreServer) -> None:
        conn = ClientConnection()
        await conn.connect(*server.bound_addr)
        resp = parse_message(await conn.send_request(METHOD_PING))
        assert resp.result["status"] == "ok"
        assert "timestamp" in resp.result
        await conn.close()

    async def test_echo(self, server: CoreServer) -> None:
        conn = ClientConnection()
        await conn.connect(*server.bound_addr)
        resp = parse_message(await conn.send_request(METHOD_ECHO, {"message": "hi"}))
        assert resp.result == {"echo": "hi"}
        await conn.close()

    async def test_request_id_increments(self, server: CoreServer) -> None:
        conn = ClientConnection()
        await conn.connect(*server.bound_addr)
        r1 = parse_message(await conn.send_request(METHOD_PING))
        r2 = parse_message(await conn.send_request(METHOD_PING))
        assert r1.id == 1
        assert r2.id == 2
        await conn.close()

    async def test_notification_does_not_block(self, server: CoreServer) -> None:
        conn = ClientConnection()
        await conn.connect(*server.bound_addr)
        await conn.send_notification(METHOD_PING)
        resp = parse_message(await conn.send_request(METHOD_PING))
        assert resp.id == 1
        await conn.close()

    async def test_connected_property(self, server: CoreServer) -> None:
        conn = ClientConnection()
        assert not conn.connected
        await conn.connect(*server.bound_addr)
        assert conn.connected
        await conn.close()
        assert not conn.connected

    async def test_close_is_idempotent(self, server: CoreServer) -> None:
        conn = ClientConnection()
        await conn.connect(*server.bound_addr)
        await conn.close()
        await conn.close()

    async def test_send_without_connect_raises(self) -> None:
        conn = ClientConnection()
        with pytest.raises(ConnectionError):
            await conn.send_request(METHOD_PING)

    async def test_connect_refused(self) -> None:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        conn = ClientConnection()
        with pytest.raises(OSError):
            await conn.connect("127.0.0.1", port)

    async def test_connection_broken_after_server_stops(
        self, server: CoreServer
    ) -> None:
        conn = ClientConnection()
        await conn.connect(*server.bound_addr)
        await server.stop()
        with pytest.raises(ConnectionError):
            await conn.send_request(METHOD_PING)
        await conn.close()
