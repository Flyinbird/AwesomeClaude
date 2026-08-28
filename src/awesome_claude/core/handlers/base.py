"""处理器基类与公共接口。"""

from collections.abc import Awaitable, Callable
from typing import Any

from awesome_claude.core.router.context import HandlerContext

type HandlerFunc = Callable[
    [dict[str, Any] | None, HandlerContext], Awaitable[dict[str, Any]]
]

_handler_registry: dict[str, HandlerFunc] = {}


def register_handler(method: str) -> Callable[[HandlerFunc], HandlerFunc]:
    """装饰器：将函数注册为指定 JSON-RPC 方法的处理器。

    Args:
        method: JSON-RPC 方法名。

    Returns:
        注册用装饰器，原样返回被装饰函数。
    """

    def decorator(func: HandlerFunc) -> HandlerFunc:
        _handler_registry[method] = func
        return func

    return decorator


def get_registered_handlers() -> dict[str, HandlerFunc]:
    """返回已注册的全部处理器。

    Returns:
        方法名 → 处理器的映射副本。
    """
    return dict(_handler_registry)
