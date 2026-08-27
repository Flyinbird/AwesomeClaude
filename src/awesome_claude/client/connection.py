"""TCP 客户端连接管理。"""

import asyncio
from typing import Any

from awesome_claude.protocol.jsonrpc import (
    JsonRpcDecodeError,
    build_notification,
    build_request,
    decode_message,
    encode_message,
)
from awesome_claude.shared.logger import get_logger


class ClientConnection:
    """JSON-RPC over TCP 客户端连接（请求 id 自增、串行收发）。"""

    def __init__(self) -> None:
        """初始化未连接状态。"""
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._request_id = 0
        self._lock = asyncio.Lock()
        self._logger = get_logger("client.connection")

    @property
    def connected(self) -> bool:
        """连接是否已建立。"""
        return self._writer is not None and not self._writer.is_closing()

    async def connect(self, host: str, port: int) -> None:
        """建立 TCP 连接。

        Args:
            host: 服务端地址。
            port: 服务端端口。

        Raises:
            OSError: 连接失败。
        """
        self._reader, self._writer = await asyncio.open_connection(host, port)
        self._logger.info("connected to %s:%s", host, port)

    async def send_request(self, method: str, params: Any = None) -> dict[str, Any]:
        """发送请求并等待对应响应。

        Args:
            method: 方法名。
            params: 参数（可选）。

        Returns:
            服务端返回的响应字典（成功或错误）。

        Raises:
            ConnectionError: 未连接或连接已断开。
        """
        async with self._lock:
            req_id = self._next_id()
            self._logger.debug("send request #%s %s", req_id, method)
            await self._send(build_request(method, params, req_id))
            raw = await self._read_line()
        try:
            return decode_message(raw)
        except JsonRpcDecodeError as exc:
            raise ConnectionError(f"收到非法响应: {exc}") from exc

    async def send_notification(self, method: str, params: Any = None) -> None:
        """发送通知（不等待响应）。

        Args:
            method: 方法名。
            params: 参数（可选）。

        Raises:
            ConnectionError: 未连接或连接已断开。
        """
        async with self._lock:
            self._logger.debug("send notification %s", method)
            await self._send(build_notification(method, params))

    async def close(self) -> None:
        """关闭连接（幂等）。"""
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (ConnectionError, OSError):
                pass
            self._writer = None
            self._reader = None
            self._logger.info("connection closed")

    def _next_id(self) -> int:
        """自增并返回下一个请求 id。"""
        self._request_id += 1
        return self._request_id

    async def _send(self, msg: dict[str, Any]) -> None:
        """编码并发送消息。"""
        if self._writer is None:
            raise ConnectionError("client 未连接")
        self._writer.write(encode_message(msg))
        await self._writer.drain()

    async def _read_line(self) -> bytes:
        """读取一行响应，处理断开异常。"""
        if self._reader is None:
            raise ConnectionError("client 未连接")
        try:
            raw = await self._reader.readline()
        except (ConnectionError, OSError, asyncio.IncompleteReadError) as exc:
            raise ConnectionError(f"连接已断开: {exc}") from exc
        if not raw:
            raise ConnectionError("连接已被服务端关闭")
        return raw.strip()
