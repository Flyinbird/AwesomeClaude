"""共享测试 fixtures 与测试辅助工具。"""

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from awesome_claude.protocol.jsonrpc import (
    build_notification,
    build_request,
    decode_message,
    encode_message,
)


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
    from awesome_claude.core.task.manager import TaskManager
    from awesome_claude.shared.logging.task_tracker import TaskTracker

    tracker = TaskTracker(str(tmp_path / "tasks"))
    task_manager = TaskManager(tracker)
    config = ServerConfig(api_key="test-key", host="127.0.0.1", port=0)
    registry = SessionRegistry()

    def context_factory(channel: SessionChannel) -> HandlerContext:
        return HandlerContext(
            task_manager=task_manager,
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
