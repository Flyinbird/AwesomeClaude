"""TCP 传输层 - 基于 asyncio 的 TCP 服务器主循环。"""

import asyncio

from awesome_claude.core.router.dispatcher import Dispatcher
from awesome_claude.core.server.session import ClientSession, ContextFactory
from awesome_claude.shared.logging.app_logger import get_app_logger


class TCPServer:
    """TCP 服务器，管理监听与并发连接。"""

    def __init__(
        self,
        host: str,
        port: int,
        dispatcher: Dispatcher,
        context_factory: ContextFactory,
    ) -> None:
        """初始化服务器。

        Args:
            host: 监听地址。
            port: 监听端口（0 表示由系统分配）。
            dispatcher: 请求分发器。
            context_factory: 会话上下文工厂。
        """
        self._host = host
        self._port = port
        self._dispatcher = dispatcher
        self._context_factory = context_factory
        self._logger = get_app_logger("core.tcp")
        self._server: asyncio.AbstractServer | None = None
        self._stop_event = asyncio.Event()
        self._connections: set[asyncio.StreamWriter] = set()
        self._client_tasks: set[asyncio.Task[None]] = set()
        self._bound_addr: tuple[str, int] | None = None

    @property
    def bound_addr(self) -> tuple[str, int]:
        """返回实际绑定的 (host, port)。"""
        if self._bound_addr is None:
            raise RuntimeError("server 尚未启动")
        return self._bound_addr

    async def start(self) -> None:
        """启动 TCP 监听并绑定地址。"""
        self._server = await asyncio.start_server(
            self._spawn_client_handler, self._host, self._port
        )
        sock = self._server.sockets[0]
        addr = sock.getsockname()
        self._bound_addr = (str(addr[0]), int(addr[1]))
        self._logger.info("listening", host=self._bound_addr[0], port=self._bound_addr[1])

    def _spawn_client_handler(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """为每个新连接创建独立的 handler 任务并跟踪。"""
        task = asyncio.create_task(self._handle_client(reader, writer))
        self._client_tasks.add(task)
        task.add_done_callback(self._client_tasks.discard)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """为单个连接创建 ClientSession 并运行。"""
        self._connections.add(writer)
        try:
            session = ClientSession(
                reader,
                writer,
                self._dispatcher,
                self._context_factory,
                on_shutdown=self.request_shutdown,
            )
            await session.handle_connection()
        finally:
            self._connections.discard(writer)

    def request_shutdown(self) -> None:
        """请求优雅退出（设置停止事件）。"""
        self._logger.info("shutdown requested")
        self._stop_event.set()

    async def wait_for_shutdown(self) -> None:
        """等待停止事件触发。"""
        await self._stop_event.wait()

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
