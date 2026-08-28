"""会话管理 - 客户端连接会话状态跟踪。"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from awesome_claude.core.router.context import HandlerContext
from awesome_claude.core.router.dispatcher import Dispatcher
from awesome_claude.protocol.errors import INTERNAL_ERROR, build_error_response
from awesome_claude.protocol.jsonrpc import (
    INVALID_REQUEST,
    JsonRpcProtocolError,
    JsonRpcRequest,
    build_error,
    build_notification,
    build_response,
    decode_message,
    encode_message,
    parse_message,
)
from awesome_claude.protocol.methods import METHOD_SHUTDOWN
from awesome_claude.shared.logging.app_logger import get_app_logger

type ContextFactory = Callable[
    [Callable[[str, dict[str, Any]], Awaitable[None]]], HandlerContext
]


class ClientSession:
    """管理单个客户端连接的读写、消息分发与上下文。"""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        dispatcher: Dispatcher,
        context_factory: ContextFactory,
        on_shutdown: Callable[[], None] | None = None,
    ) -> None:
        """初始化会话。

        Args:
            reader: 连接读流。
            writer: 连接写流。
            dispatcher: 请求分发器。
            context_factory: 为每个连接创建 HandlerContext 的工厂（注入 send_notification）。
            on_shutdown: shutdown 通知触发时的回调（默认无）。
        """
        self._reader = reader
        self._writer = writer
        self._dispatcher = dispatcher
        self._context_factory = context_factory
        self._on_shutdown = on_shutdown
        self._addr = writer.get_extra_info("peername")
        self._logger = get_app_logger("core.session")

    async def handle_connection(self) -> None:
        """主循环：读取 JSON-RPC 消息并逐条处理。"""
        context = self._context_factory(self.send_notification)
        self._logger.info("client connected", addr=self._addr)
        try:
            while True:
                raw = await self._reader.readline()
                if not raw:
                    break
                line = raw.strip()
                if not line:
                    continue
                if not await self._process_line(line, context):
                    break
        except (ConnectionError, OSError):
            self._logger.info("connection dropped", addr=self._addr)
        except Exception:
            self._logger.exception("session crashed", addr=self._addr)
        finally:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                self._logger.debug(
                    "error closing writer", addr=self._addr, exc_info=True
                )
            self._logger.info("client disconnected", addr=self._addr)

    async def _process_line(self, line: bytes, context: HandlerContext) -> bool:
        """解析并处理一行消息；返回 False 表示应结束连接。"""
        try:
            data = decode_message(line)
            parsed = parse_message(data)
        except JsonRpcProtocolError as exc:
            await self._send(build_error(exc.code, str(exc), id=None))
            return True

        if not isinstance(parsed, JsonRpcRequest):
            await self._send(
                build_error(INVALID_REQUEST, "服务端只接收请求消息", id=None)
            )
            return True

        request_id = parsed.id
        try:
            result = await self._dispatcher.dispatch(
                parsed.method, parsed.params, context
            )
        except Exception:
            self._logger.exception("dispatch failed for method %s", parsed.method)
            result = build_error_response(None, INTERNAL_ERROR, "Internal error")

        if isinstance(result, dict) and "error" in result:
            response = dict(result)
            response["id"] = request_id
        else:
            response = build_response(result, id=request_id)

        if request_id is not None:
            await self._send(response)

        if parsed.method == METHOD_SHUTDOWN and self._on_shutdown is not None:
            self._on_shutdown()
            return False
        return True

    async def send_notification(self, method: str, params: dict[str, Any]) -> None:
        """构造 JSON-RPC notification 并写入 writer。"""
        await self._send(build_notification(method, params))

    async def _send(self, msg: dict[str, Any]) -> None:
        """编码并发送消息。"""
        self._writer.write(encode_message(msg))
        await self._writer.drain()
