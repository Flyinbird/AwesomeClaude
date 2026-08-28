"""JSON-RPC 方法名常量与参数/返回类型定义（Phase 1 + Phase 2 chat）。"""

from typing import Literal, NotRequired, TypedDict

from awesome_claude.shared.types import ChatResponse

__all__ = [
    "METHOD_CHAT",
    "METHOD_ECHO",
    "METHOD_PING",
    "METHOD_SHUTDOWN",
    "NOTIFY_CHAT_STREAM",
    "ChatParams",
    "ChatResponse",
    "EchoParams",
    "EchoResult",
    "Method",
    "PingParams",
    "PingResult",
    "ShutdownParams",
    "StreamNotificationParams",
]

type Method = Literal["ping", "echo", "shutdown", "chat"]

METHOD_PING: Method = "ping"
METHOD_ECHO: Method = "echo"
METHOD_SHUTDOWN: Method = "shutdown"
METHOD_CHAT: Method = "chat"

NOTIFY_CHAT_STREAM: str = "chat.stream"


class PingParams(TypedDict):
    """ping 方法参数（无）。"""


class PingResult(TypedDict):
    """ping 方法返回值。"""

    status: Literal["ok"]
    timestamp: str


class EchoParams(TypedDict):
    """echo 方法参数。"""

    message: str


class EchoResult(TypedDict):
    """echo 方法返回值。"""

    echo: str


class ShutdownParams(TypedDict):
    """shutdown 方法参数（无，notification 类型，无返回值）。"""


class ChatParams(TypedDict):
    """chat 方法参数。"""

    message: str
    conversation_id: NotRequired[str | None]
    max_tokens: NotRequired[int | None]


class StreamNotificationParams(TypedDict):
    """chat.stream 流式通知参数（Server → Client 推送）。"""

    task_id: str
    chunk_index: int
    text: str
    is_final: bool
