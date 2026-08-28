"""Core 服务端测试：启动 → 连接 → 收发消息 → 验证。"""

import asyncio
from typing import Any

import pytest

from awesome_claude.core.handler import handle_echo
from awesome_claude.core.router import MethodRouter
from awesome_claude.core.server import CoreServer
from awesome_claude.protocol.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    JsonRpcError,
    JsonRpcResponse,
    decode_message,
    parse_message,
)
from awesome_claude.protocol.methods import METHOD_ECHO, METHOD_PING, METHOD_SHUTDOWN
from tests.conftest import RpcTestClient


class TestRouter:
    """MethodRouter 单元测试。"""

    async def test_register_overwrite(self) -> None:
        router = MethodRouter()

        async def first(params: Any) -> str:
            return "first"

        async def second(params: Any) -> str:
            return "second"

        router.register("m", first)
        router.register("m", second)
        req = parse_message({"jsonrpc": "2.0", "method": "m", "id": 1})
        resp = await router.route(req)
        assert resp["result"] == "second"

    async def test_unregister(self) -> None:
        router = MethodRouter()

        async def handler(params: Any) -> str:
            return "ok"

        router.register("m", handler)
        router.unregister("m")
        req = parse_message({"jsonrpc": "2.0", "method": "m", "id": 1})
        resp = await router.route(req)
        assert resp["error"]["code"] == METHOD_NOT_FOUND

    async def test_route_unknown_method(self) -> None:
        router = MethodRouter()
        req = parse_message({"jsonrpc": "2.0", "method": "nope", "id": 1})
        resp = await router.route(req)
        assert resp["error"]["code"] == METHOD_NOT_FOUND
        assert resp["id"] == 1

    async def test_route_echo(self) -> None:
        router = MethodRouter()
        router.register(METHOD_ECHO, handle_echo)
        req = parse_message(
            {
                "jsonrpc": "2.0",
                "method": METHOD_ECHO,
                "params": {"message": "hi"},
                "id": 1,
            }
        )
        resp = await router.route(req)
        assert resp["result"] == {"echo": "hi"}

    async def test_route_handler_exception_maps_to_internal_error(self) -> None:
        router = MethodRouter()

        async def boom(params: Any) -> None:
            raise RuntimeError("boom")

        router.register("boom", boom)
        req = parse_message({"jsonrpc": "2.0", "method": "boom", "id": 1})
        resp = await router.route(req)
        assert resp["error"]["code"] == INTERNAL_ERROR

    async def test_route_notification_returns_error_not_sent_by_server(self) -> None:
        router = MethodRouter()
        req = parse_message({"jsonrpc": "2.0", "method": "nope"})
        resp = await router.route(req)
        assert resp["error"]["code"] == METHOD_NOT_FOUND
        assert resp["id"] is None


class TestServer:
    """CoreServer 集成测试。"""

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

    async def test_method_not_found(self, client: RpcTestClient) -> None:
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

    async def test_multiple_clients(self, server: CoreServer) -> None:
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

    async def test_shutdown_stops_server(self, server: CoreServer) -> None:
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

    async def test_reconnect_after_stop_fails(self, server: CoreServer) -> None:
        await server.stop()
        with pytest.raises(OSError):
            _, writer = await asyncio.open_connection(*server.bound_addr)
            writer.close()
            await writer.wait_closed()
