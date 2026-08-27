"""Phase 1 具体业务处理器（ping / echo / shutdown）。"""

from datetime import UTC, datetime
from typing import Any, Protocol

from awesome_claude.protocol.methods import (
    EchoParams,
    EchoResult,
    PingParams,
    PingResult,
)


class ShutdownTarget(Protocol):
    """支持优雅关闭的服务端对象接口。"""

    async def shutdown(self) -> None: ...


async def handle_ping(params: PingParams | None) -> PingResult:
    """健康检查：返回状态与当前 UTC ISO 时间戳。

    Args:
        params: ping 参数（无）。

    Returns:
        包含 status 与 timestamp 的结果。
    """
    return {"status": "ok", "timestamp": datetime.now(UTC).isoformat()}


async def handle_echo(params: EchoParams | None) -> EchoResult:
    """回显测试：原样返回 message。

    Args:
        params: 必须包含 message 字段。

    Returns:
        包含回显内容的结果。

    Raises:
        ValueError: params 缺失或缺少 message 字段。
    """
    if params is None or "message" not in params:
        raise ValueError("echo 需要参数 message")
    return {"echo": params["message"]}


async def handle_shutdown(params: Any, server_ref: ShutdownTarget) -> None:
    """通知服务端关闭。

    Args:
        params: shutdown 参数（无）。
        server_ref: 触发关闭的服务端对象。
    """
    await server_ref.shutdown()
