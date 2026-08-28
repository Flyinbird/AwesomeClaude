"""消息接收器 - 后台读取 TCP 数据，区分 response 与 notification 并分发。"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from awesome_claude.protocol.jsonrpc import JsonRpcDecodeError, decode_message
from awesome_claude.shared.logging.app_logger import get_app_logger

type NotificationHandler = Callable[[dict[str, Any]], Awaitable[None]]


class MessageReceiver:
    """后台读取 TCP 数据，将 response 与 notification 分发给对应消费者。"""

    def __init__(self, reader: asyncio.StreamReader) -> None:
        """初始化接收器。

        Args:
            reader: TCP 读流。
        """
        self._reader = reader
        self._response_futures: dict[int, asyncio.Future] = {}
        self._notification_handlers: dict[str, list[NotificationHandler]] = {}
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._logger = get_app_logger("client.receiver")

    async def start(self) -> None:
        """启动后台 reader task。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._read_loop())

    async def stop(self) -> None:
        """停止 reader task 并清理未完成的 response future。"""
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        for future in list(self._response_futures.values()):
            if not future.done():
                future.cancel()
        self._response_futures.clear()

    def on_notification(self, method: str, handler: NotificationHandler) -> None:
        """注册 notification 处理器（同一方法可注册多个）。

        Args:
            method: notification 方法名。
            handler: 异步处理器，接收 params。
        """
        self._notification_handlers.setdefault(method, []).append(handler)

    async def wait_for_response(
        self, request_id: int, timeout: float = 60.0
    ) -> dict[str, Any]:
        """等待指定 id 的 response。

        Args:
            request_id: 请求 id。
            timeout: 超时秒数。

        Returns:
            response 字典。

        Raises:
            TimeoutError: 超时未收到响应。
            ConnectionError: 连接在等待期间关闭。
        """
        future = asyncio.get_running_loop().create_future()
        self._response_futures[request_id] = future
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._response_futures.pop(request_id, None)

    async def _read_loop(self) -> None:
        """后台循环：读一行 → 解析 → 分发 response / notification。"""
        try:
            while self._running:
                raw = await self._reader.readline()
                if not raw:
                    break
                line = raw.strip()
                if not line:
                    continue
                try:
                    msg = decode_message(line)
                except JsonRpcDecodeError:
                    self._logger.warning("invalid message from server")
                    continue
                if msg.get("id") is not None:
                    await self._dispatch_response(msg)
                elif "method" in msg:
                    await self._dispatch_notification(msg)
                else:
                    self._logger.warning("unrecognized message", msg=msg)
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            self._logger.info("connection closed by server")
        finally:
            self._running = False
            for future in list(self._response_futures.values()):
                if not future.done():
                    future.set_exception(ConnectionError("服务端连接已关闭"))
            self._response_futures.clear()

    async def _dispatch_response(self, msg: dict[str, Any]) -> None:
        """将 response 交给对应 id 的等待者。"""
        request_id = msg["id"]
        if not isinstance(request_id, int) or isinstance(request_id, bool):
            self._logger.warning("unexpected response id type", id=request_id)
            return
        future = self._response_futures.get(request_id)
        if future is not None and not future.done():
            future.set_result(msg)
        else:
            self._logger.warning("unexpected response for id %s", request_id)

    async def _dispatch_notification(self, msg: dict[str, Any]) -> None:
        """将 notification 分发给注册的处理器。"""
        method = msg.get("method")
        if not isinstance(method, str):
            return
        params = msg.get("params", {})
        for handler in self._notification_handlers.get(method, []):
            try:
                await handler(params)
            except Exception:
                self._logger.exception("notification handler failed", method=method)
