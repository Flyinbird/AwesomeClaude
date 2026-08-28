"""Core 服务端测试（新架构）：Dispatcher 单元测试 + TCPServer 集成测试。"""

import asyncio
from typing import Any

import pytest

from awesome_claude.core.config import ServerConfig
from awesome_claude.core.handlers.echo import handle_echo
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.core.router.dispatcher import Dispatcher, create_dispatcher
from awesome_claude.protocol.errors import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
)
from awesome_claude.protocol.jsonrpc import (
    JsonRpcError,
    JsonRpcResponse,
    decode_message,
    parse_message,
)
from awesome_claude.protocol.methods import METHOD_ECHO, METHOD_PING, METHOD_SHUTDOWN
from tests.conftest import RpcTestClient


def make_context() -> HandlerContext:
    """构造测试用 HandlerContext。"""
    from unittest.mock import MagicMock

    return HandlerContext(
        task_manager=MagicMock(),
        llm_client=MagicMock(),
        send_notification=MagicMock(),
        config=ServerConfig(api_key="k", host="127.0.0.1", port=0),
    )


class TestDispatcher:
    """Dispatcher 单元测试。"""

    async def test_register_and_dispatch(self) -> None:
        dispatcher = Dispatcher()
        dispatcher.register(METHOD_ECHO, handle_echo)
        result = await dispatcher.dispatch(
            METHOD_ECHO, {"message": "hi"}, make_context()
        )
        assert result == {"echo": "hi"}

    async def test_register_overwrite(self) -> None:
        dispatcher = Dispatcher()

        async def first(params: dict[str, Any] | None, context: HandlerContext) -> dict:
            return {"v": "first"}

        async def second(
            params: dict[str, Any] | None, context: HandlerContext
        ) -> dict:
            return {"v": "second"}

        dispatcher.register("m", first)
        dispatcher.register("m", second)
        assert await dispatcher.dispatch("m", None, make_context()) == {"v": "second"}

    async def test_unknown_method_returns_method_not_found(self) -> None:
        dispatcher = Dispatcher()
        result = await dispatcher.dispatch("nope", None, make_context())
        assert result["error"]["code"] == METHOD_NOT_FOUND

    async def test_create_dispatcher_registers_defaults(self) -> None:
        dispatcher = create_dispatcher()
        for method in (METHOD_PING, METHOD_ECHO, METHOD_SHUTDOWN, "chat"):
            assert method in dispatcher._handlers


class TestServer:
    """TCPServer 集成测试（通过真实 TCP 连接）。"""

    async def test_ping(self, client: RpcTestClient) -> None:
        resp = await client.call(METHOD_PING, req_id=1)
        msg = parse_message(resp)
        assert isinstance(msg, JsonRpcResponse)
        assert msg.id == 1
        assert msg.result["status"] == "ok"
        assert "timestamp" in msg.result

    async def test_echo(self, client: RpcTestClient) -> None:
        resp = await client.call(METHOD_ECHO, {"message": "你好"}, req_id=2)
        msg = parse_message(resp)
        assert isinstance(msg, JsonRpcResponse)
        assert msg.result == {"echo": "你好"}
        assert msg.id == 2

    async def test_echo_missing_params_is_internal_error(
        self, client: RpcTestClient
    ) -> None:
        resp = await client.call(METHOD_ECHO, req_id=1)
        msg = parse_message(resp)
        assert isinstance(msg, JsonRpcError)
        assert msg.error.code == INTERNAL_ERROR

    async def test_method_not_found_echoes_id(self, client: RpcTestClient) -> None:
        resp = await client.call("unknown_method", req_id=42)
        msg = parse_message(resp)
        assert isinstance(msg, JsonRpcError)
        assert msg.error.code == METHOD_NOT_FOUND
        assert msg.id == 42

    async def test_invalid_request(self, client: RpcTestClient) -> None:
        raw = await client.send_raw(b'{"jsonrpc": "2.0", "id": 1}\n')
        msg = parse_message(decode_message(raw))
        assert isinstance(msg, JsonRpcError)
        assert msg.error.code == INVALID_REQUEST
        assert msg.id is None

    async def test_parse_error(self, client: RpcTestClient) -> None:
        raw = await client.send_raw(b"{not json}\n")
        msg = parse_message(decode_message(raw))
        assert isinstance(msg, JsonRpcError)
        assert msg.error.code == -32700
        assert msg.id is None

    async def test_notification_no_response(self, client: RpcTestClient) -> None:
        await client.notify(METHOD_PING)
        resp = await client.call(METHOD_PING, req_id=5)
        msg = parse_message(resp)
        assert isinstance(msg, JsonRpcResponse)
        assert msg.id == 5

    async def test_multiple_clients(self, server: Any) -> None:
        clients = [RpcTestClient(*server.bound_addr) for _ in range(3)]
        await asyncio.gather(*(c.connect() for c in clients))
        results = await asyncio.gather(
            *(c.call(METHOD_PING, req_id=i) for i, c in enumerate(clients))
        )
        for i, resp in enumerate(results):
            msg = parse_message(resp)
            assert isinstance(msg, JsonRpcResponse)
            assert msg.id == i
            assert msg.result["status"] == "ok"
        await asyncio.gather(*(c.close() for c in clients))

    async def test_shutdown_stops_server(self, server: Any) -> None:
        c = RpcTestClient(*server.bound_addr)
        await c.connect()
        await c.notify(METHOD_SHUTDOWN)
        for _ in range(100):
            if server._stop_event.is_set():
                break
            await asyncio.sleep(0.01)
        assert server._stop_event.is_set()
        assert await c.reader.readline() == b""
        await c.close()

    async def test_reconnect_after_stop_fails(self, server: Any) -> None:
        await server.stop()
        with pytest.raises(OSError):
            _, writer = await asyncio.open_connection(*server.bound_addr)
            writer.close()
            await writer.wait_closed()
