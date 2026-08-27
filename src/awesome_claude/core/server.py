"""Core 守护进程：TCP Server 主循环（JSON-RPC 2.0）。"""

import asyncio
import signal
from typing import Any

from awesome_claude.core.config import ServerConfig
from awesome_claude.core.handler import handle_echo, handle_ping, handle_shutdown
from awesome_claude.core.router import MethodRouter
from awesome_claude.protocol.jsonrpc import (
    INVALID_REQUEST,
    JsonRpcProtocolError,
    JsonRpcRequest,
    build_error,
    decode_message,
    encode_message,
    parse_message,
)
from awesome_claude.protocol.methods import METHOD_ECHO, METHOD_PING, METHOD_SHUTDOWN
from awesome_claude.shared.logger import get_logger, setup_logging


class CoreServer:
    """JSON-RPC over TCP 守护进程，支持多客户端并发与优雅退出。"""

    def __init__(self, config: ServerConfig | None = None) -> None:
        """初始化服务器。

        Args:
            config: 服务端配置，缺省时从环境变量读取。
        """
        self.config = config or ServerConfig.from_env()
        self.router = MethodRouter()
        self._logger = get_logger("core.server")
        self._server: asyncio.AbstractServer | None = None
        self._stop_event = asyncio.Event()
        self._connections: set[asyncio.StreamWriter] = set()
        self._client_tasks: set[asyncio.Task[None]] = set()
        self._bound_addr: tuple[str, int] | None = None
        self._register_default_handlers()

    @property
    def bound_addr(self) -> tuple[str, int]:
        """返回实际绑定的 (host, port)。"""
        if self._bound_addr is None:
            raise RuntimeError("server 尚未启动")
        return self._bound_addr

    def _register_default_handlers(self) -> None:
        """注册 Phase 1 默认方法处理器。"""
        self.router.register(METHOD_PING, handle_ping)
        self.router.register(METHOD_ECHO, handle_echo)
        self.router.register(METHOD_SHUTDOWN, self._handle_shutdown_request)

    async def _handle_shutdown_request(self, params: Any) -> None:
        """shutdown 处理器包装：将 self 作为 server_ref 传入。"""
        await handle_shutdown(params, self)

    async def start(self) -> None:
        """启动 TCP 监听并绑定地址。"""
        self._server = await asyncio.start_server(
            self._spawn_client_handler, self.config.host, self.config.port
        )
        sock = self._server.sockets[0]
        addr = sock.getsockname()
        self._bound_addr = (str(addr[0]), int(addr[1]))
        self._logger.info("listening on %s:%s", *self._bound_addr)

    def _spawn_client_handler(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """为每个新连接创建独立的 handler 任务并跟踪。"""
        task = asyncio.create_task(self._handle_client(reader, writer))
        self._client_tasks.add(task)
        task.add_done_callback(self._client_tasks.discard)

    async def shutdown(self) -> None:
        """请求服务器优雅退出（由 shutdown 通知触发）。"""
        self._logger.info("shutdown requested")
        self._stop_event.set()

    async def run(self) -> None:
        """启动服务器并阻塞，直到收到 shutdown 通知或 SIGTERM/SIGINT。"""
        await self.start()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._stop_event.set)
        self._logger.info("core server running (SIGTERM/SIGINT to stop)")
        try:
            await self._stop_event.wait()
        finally:
            await self.stop()

    async def stop(self) -> None:
        """停止监听并关闭所有连接。"""
        self._stop_event.set()
        tasks = list(self._client_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        writers = list(self._connections)
        for writer in writers:
            writer.close()
        if writers:
            await asyncio.gather(
                *(w.wait_closed() for w in writers), return_exceptions=True
            )
        self._connections.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._logger.info("server stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """单个客户端连接的处理协程。"""
        addr = writer.get_extra_info("peername")
        self._logger.info("client connected: %s", addr)
        self._connections.add(writer)
        try:
            await self._read_loop(reader, writer)
        except Exception:
            self._logger.exception("client handler crashed: %s", addr)
        finally:
            self._connections.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                self._logger.debug("error closing writer for %s", addr, exc_info=True)
            self._logger.info("client disconnected: %s", addr)

    async def _read_loop(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """按 \\n 分隔读取消息并逐条处理。"""
        while not self._stop_event.is_set():
            raw = await reader.readline()
            if not raw:
                break
            line = raw.strip()
            if not line:
                continue
            await self._process_line(line, writer)
            if self._stop_event.is_set():
                break

    async def _process_line(self, line: bytes, writer: asyncio.StreamWriter) -> None:
        """解析单条消息并路由执行。"""
        try:
            data = decode_message(line)
            parsed = parse_message(data)
        except JsonRpcProtocolError as exc:
            await self._send(writer, build_error(exc.code, str(exc), id=None))
            return
        if not isinstance(parsed, JsonRpcRequest):
            await self._send(
                writer, build_error(INVALID_REQUEST, "服务端只接收请求消息", id=None)
            )
            return
        response = await self.router.route(parsed)
        if parsed.id is not None:
            await self._send(writer, response)

    async def _send(self, writer: asyncio.StreamWriter, msg: dict[str, Any]) -> None:
        """编码并发送响应消息。"""
        writer.write(encode_message(msg))
        await writer.drain()


def main() -> None:
    """守护进程入口。"""
    config = ServerConfig.from_env()
    setup_logging(config.log_level)
    server = CoreServer(config)
    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
