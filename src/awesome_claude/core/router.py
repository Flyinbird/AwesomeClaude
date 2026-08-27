"""JSON-RPC 请求路由与方法注册。"""

from collections.abc import Awaitable, Callable
from typing import Any

from awesome_claude.protocol.jsonrpc import (
    INTERNAL_ERROR,
    METHOD_NOT_FOUND,
    JsonRpcRequest,
    build_error,
    build_response,
)
from awesome_claude.shared.logger import get_logger

type Handler = Callable[[Any], Awaitable[Any]]


class MethodRouter:
    """JSON-RPC 方法注册与分发。"""

    def __init__(self) -> None:
        """初始化空路由表。"""
        self._handlers: dict[str, Handler] = {}
        self._logger = get_logger("core.router")

    def register(self, method_name: str, handler: Handler) -> None:
        """注册方法处理器（重复注册会覆盖）。

        Args:
            method_name: 方法名。
            handler: 异步处理器，接收 params 并返回结果。
        """
        self._handlers[method_name] = handler

    def unregister(self, method_name: str) -> None:
        """注销方法处理器。

        Args:
            method_name: 方法名。
        """
        self._handlers.pop(method_name, None)

    async def route(self, request: JsonRpcRequest) -> dict[str, Any]:
        """根据 method 分发请求并返回响应消息。

        未注册的方法返回 METHOD_NOT_FOUND 错误；handler 抛异常返回 INTERNAL_ERROR 错误。
        注意：notification（id 为 None）也返回响应，由调用方决定是否发送。

        Args:
            request: 已解析的 JSON-RPC 请求。

        Returns:
            JSON-RPC 响应字典。
        """
        handler = self._handlers.get(request.method)
        if handler is None:
            return build_error(
                METHOD_NOT_FOUND, f"Method not found: {request.method}", id=request.id
            )
        try:
            result = await handler(request.params)
        except Exception as exc:
            self._logger.exception("handler '%s' failed", request.method)
            return build_error(INTERNAL_ERROR, f"Internal error: {exc}", id=request.id)
        return build_response(result, id=request.id)
