"""共享测试 fixtures 与测试辅助工具。"""

import asyncio
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest

from awesome_claude.protocol.jsonrpc import (
    build_notification,
    build_request,
    decode_message,
    encode_message,
)
from awesome_claude.protocol.methods import (
    METHOD_CHAT,
    NOTIFY_CHAT_COMPLETED,
    NOTIFY_CHAT_FAILED,
)

TERMINAL_CHAT_NOTIFICATIONS: frozenset[str] = frozenset(
    {NOTIFY_CHAT_COMPLETED, NOTIFY_CHAT_FAILED}
)


async def read_until(
    reader: asyncio.StreamReader,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    timeout: float = 2.0,
    collect: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """读取消息直到 predicate 命中；途经消息可收集到 collect。

    Args:
        reader: TCP 读流。
        predicate: 命中判断函数。
        timeout: 超时秒数。
        collect: 可选列表，按到达顺序收集所有读取到的消息。

    Returns:
        命中的消息；连接关闭或超时前未命中则返回 None。
    """

    async def _loop() -> dict[str, Any] | None:
        while True:
            raw = await reader.readline()
            if not raw:
                return None
            msg = decode_message(raw)
            if collect is not None:
                collect.append(msg)
            if predicate(msg):
                return msg

    return await asyncio.wait_for(_loop(), timeout=timeout)


def expect_chat_terminal(conn: Any) -> asyncio.Future[dict[str, Any]]:
    """在 ClientConnection 上注册对话终态监听，返回结果 Future。

    返回的 Future 解析为 ``{"method": <通知方法名>, "params": <通知参数>}``。

    Args:
        conn: 已连接的 ClientConnection。

    Returns:
        等待终态通知结果的 Future。
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()

    async def on_completed(params: dict[str, Any]) -> None:
        if not future.done():
            future.set_result({"method": NOTIFY_CHAT_COMPLETED, "params": params})

    async def on_failed(params: dict[str, Any]) -> None:
        if not future.done():
            future.set_result({"method": NOTIFY_CHAT_FAILED, "params": params})

    conn.on_notification(NOTIFY_CHAT_COMPLETED, on_completed)
    conn.on_notification(NOTIFY_CHAT_FAILED, on_failed)
    return future


class RpcTestClient:
    """测试用简易 JSON-RPC 客户端。"""

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None

    async def connect(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)

    async def call(
        self, method: str, params: Any = None, req_id: int = 1
    ) -> dict[str, Any]:
        assert self.writer is not None
        assert self.reader is not None
        self.writer.write(encode_message(build_request(method, params, req_id)))
        await self.writer.drain()
        return decode_message(await self.reader.readline())

    async def send_raw(self, raw: bytes) -> bytes:
        assert self.writer is not None
        assert self.reader is not None
        self.writer.write(raw)
        await self.writer.drain()
        return await self.reader.readline()

    async def notify(self, method: str, params: Any = None) -> None:
        assert self.writer is not None
        self.writer.write(encode_message(build_notification(method, params)))
        await self.writer.drain()

    async def chat_and_wait(
        self, params: Any, req_id: int = 1, *, timeout: float = 2.0
    ) -> dict[str, Any]:
        """发送 chat 请求，读受理 ack 后继续读到终态通知。

        Args:
            params: chat 参数。
            req_id: 请求 id。
            timeout: 等待终态通知的超时秒数。

        Returns:
            终态通知（`chat.completed` 或 `chat.failed`）完整消息。

        Raises:
            AssertionError: 未在超时前收到终态通知。
        """
        assert self.reader is not None
        ack = await self.call(METHOD_CHAT, params, req_id)
        assert ack.get("result", {}).get("accepted") is True
        run_id = ack["result"]["run_id"]
        terminal = await read_until(
            self.reader,
            lambda m: (
                m.get("method") in TERMINAL_CHAT_NOTIFICATIONS
                and m.get("params", {}).get("run_id") == run_id
            ),
            timeout=timeout,
        )
        assert terminal is not None, "未收到 chat 终态通知"
        return terminal

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()


@pytest.fixture
async def server(tmp_path: Any) -> Any:
    """启动一个使用随机端口（port=0）的新架构 TCPServer。"""
    from awesome_claude.core.config import ServerConfig
    from awesome_claude.core.router.context import HandlerContext
    from awesome_claude.core.router.dispatcher import create_dispatcher
    from awesome_claude.core.server.tcp import TCPServer
    from awesome_claude.core.session.channel import SessionChannel
    from awesome_claude.core.session.registry import SessionRegistry
    from awesome_claude.shared.logging.trace_store import TraceStore

    trace_store = TraceStore(str(tmp_path / "runs"))
    config = ServerConfig(api_key="test-key", host="127.0.0.1", port=0)
    registry = SessionRegistry()

    def context_factory(channel: SessionChannel) -> HandlerContext:
        return HandlerContext(
            trace_store=trace_store,
            llm_client=MagicMock(),
            sessions=channel,
            config=config,
        )

    srv = TCPServer(
        config.host, config.port, create_dispatcher(), context_factory, registry
    )
    await srv.start()
    yield srv
    await srv.stop()


@pytest.fixture
async def client(server: Any) -> RpcTestClient:
    """连接已启动 server 的测试客户端。"""
    c = RpcTestClient(*server.bound_addr)
    await c.connect()
    yield c
    await c.close()
