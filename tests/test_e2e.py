"""端到端集成测试：启动 CoreServer → ClientConnection 连接 → ping/echo/shutdown 完整链路。"""

import asyncio

from awesome_claude.client.connection import ClientConnection
from awesome_claude.core.config import ServerConfig
from awesome_claude.core.server import CoreServer
from awesome_claude.protocol.jsonrpc import parse_message
from awesome_claude.protocol.methods import METHOD_ECHO, METHOD_PING, METHOD_SHUTDOWN


async def _wait_for_addr(server: CoreServer, timeout: float = 5.0) -> tuple[str, int]:
    """轮询等待 server 完成监听并返回绑定地址。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        try:
            return server.bound_addr
        except RuntimeError:
            await asyncio.sleep(0.01)
    raise TimeoutError("server 未在超时时间内完成监听")


async def test_e2e_full_chain() -> None:
    """以后台任务启动 CoreServer，验证 ping → echo → shutdown 完整链路。"""
    server = CoreServer(ServerConfig(host="127.0.0.1", port=0))
    run_task = asyncio.create_task(server.run())
    conn = ClientConnection()
    try:
        host, port = await _wait_for_addr(server)

        await conn.connect(host, port)
        assert conn.connected

        ping = parse_message(await conn.send_request(METHOD_PING))
        assert ping.result["status"] == "ok"
        assert "timestamp" in ping.result

        echo = parse_message(await conn.send_request(METHOD_ECHO, {"message": "e2e"}))
        assert echo.result == {"echo": "e2e"}

        await conn.send_notification(METHOD_SHUTDOWN)

        await asyncio.wait_for(run_task, timeout=5.0)
        assert run_task.done()
        assert server._stop_event.is_set()

        assert await conn._reader.readline() == b""
    finally:
        await conn.close()
        if not run_task.done():
            run_task.cancel()
            await asyncio.gather(run_task, return_exceptions=True)


async def test_e2e_echo_and_unknown_method() -> None:
    """额外验证：多请求、未知方法错误响应、connection 生命周期。"""
    server = CoreServer(ServerConfig(host="127.0.0.1", port=0))
    await server.start()
    conn = ClientConnection()
    try:
        await conn.connect(*server.bound_addr)

        echo1 = parse_message(await conn.send_request(METHOD_ECHO, {"message": "a"}))
        echo2 = parse_message(await conn.send_request(METHOD_ECHO, {"message": "b"}))
        assert echo1.result == {"echo": "a"}
        assert echo2.result == {"echo": "b"}
        assert echo1.id == 1
        assert echo2.id == 2

        err = parse_message(await conn.send_request("no_such_method"))
        assert err.error.code == -32601
        assert err.id == 3
    finally:
        await conn.close()
        await server.stop()
