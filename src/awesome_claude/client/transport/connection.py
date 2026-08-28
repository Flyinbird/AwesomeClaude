"""客户端连接管理 - TCP 连接与 JSON-RPC 收发。"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from awesome_claude.client.transport.receiver import MessageReceiver
from awesome_claude.protocol.jsonrpc import build_request, encode_message
from awesome_claude.shared.logging.app_logger import get_app_logger

type NotificationHandler = Callable[[dict[str, Any]], Awaitable[None]]


class ClientConnection:
    """封装 TCP 连接，提供 request-response 与 notification 处理能力。"""

    def __init__(self, host: str, port: int) -> None:
        """初始化连接。

        Args:
            host: 服务端地址。
            port: 服务端端口。
        """
        self._host = host
        self._port = port
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._receiver: MessageReceiver | None = None
        self._request_id = 0
        self._logger = get_app_logger("client.connection")

    @property
    def connected(self) -> bool:
        """连接是否已建立。"""
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self) -> None:
        """建立 TCP 连接并启动 MessageReceiver。"""
        self._reader, self._writer = await asyncio.open_connection(
            self._host, self._port
        )
        self._receiver = MessageReceiver(self._reader)
        await self._receiver.start()
        self._logger.info("connected", host=self._host, port=self._port)

    async def disconnect(self) -> None:
        """断开连接并停止接收器。"""
        if self._receiver is not None:
            await self._receiver.stop()
            self._receiver = None
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            self._writer = None
            self._reader = None
            self._logger.info("disconnected")

    async def send_request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """发送 JSON-RPC request 并等待对应 response。

        Args:
            method: 方法名。
            params: 请求参数（可选）。

        Returns:
            服务端返回的响应字典（成功或错误）。

        Raises:
            ConnectionError: 未连接、超时或连接关闭。
        """
        if self._writer is None or self._receiver is None:
            raise ConnectionError("client 未连接")
        self._request_id += 1
        request_id = self._request_id
        self._writer.write(encode_message(build_request(method, params, request_id)))
        await self._writer.drain()
        try:
            return await self._receiver.wait_for_response(request_id)
        except TimeoutError:
            raise ConnectionError(f"请求 {method} 超时") from None

    def on_notification(self, method: str, handler: NotificationHandler) -> None:
        """注册 notification 处理器。

        Args:
            method: notification 方法名。
            handler: 异步处理器。

        Raises:
            ConnectionError: 未连接。
        """
        if self._receiver is None:
            raise ConnectionError("client 未连接")
        self._receiver.on_notification(method, handler)
