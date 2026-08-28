"""方法分发器 - 将请求路由到对应处理器。"""

from typing import Any

from awesome_claude.core.handlers.base import HandlerFunc
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.protocol.errors import METHOD_NOT_FOUND, build_error_response
from awesome_claude.protocol.methods import (
    METHOD_CHAT,
    METHOD_ECHO,
    METHOD_PING,
    METHOD_SHUTDOWN,
)
from awesome_claude.shared.logging.app_logger import get_app_logger


class Dispatcher:
    """JSON-RPC 方法分发器。"""

    def __init__(self) -> None:
        """初始化空路由表。"""
        self._handlers: dict[str, HandlerFunc] = {}
        self._logger = get_app_logger("core.dispatcher")

    def register(self, method: str, handler: HandlerFunc) -> None:
        """注册一个方法处理器（重复注册会覆盖）。

        Args:
            method: 方法名。
            handler: 异步处理器。
        """
        self._handlers[method] = handler

    async def dispatch(
        self,
        method: str,
        params: dict[str, Any] | None,
        context: HandlerContext,
    ) -> dict[str, Any]:
        """分发请求到对应 handler。

        方法不存在时返回 METHOD_NOT_FOUND 错误响应（id 为 None，由调用方补充）。

        Args:
            method: 方法名。
            params: 请求参数。
            context: 运行时上下文。

        Returns:
            handler 的结果 dict（或错误响应 dict，含 "error" 键）。
        """
        handler = self._handlers.get(method)
        if handler is None:
            self._logger.warning("method not found", method=method)
            return build_error_response(
                None, METHOD_NOT_FOUND, f"Method not found: {method}"
            )
        return await handler(params, context)


def create_dispatcher() -> Dispatcher:
    """创建 Dispatcher 并注册全部默认 handler（ping/echo/shutdown/chat）。

    Returns:
        已注册默认方法的 Dispatcher。
    """
    from awesome_claude.core.handlers.chat import handle_chat
    from awesome_claude.core.handlers.echo import handle_echo
    from awesome_claude.core.handlers.ping import handle_ping
    from awesome_claude.core.handlers.shutdown import handle_shutdown

    dispatcher = Dispatcher()
    dispatcher.register(METHOD_PING, handle_ping)
    dispatcher.register(METHOD_ECHO, handle_echo)
    dispatcher.register(METHOD_SHUTDOWN, handle_shutdown)
    dispatcher.register(METHOD_CHAT, handle_chat)
    return dispatcher
