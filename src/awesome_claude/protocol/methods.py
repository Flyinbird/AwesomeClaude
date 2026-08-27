"""Phase 1 JSON-RPC 方法名常量与参数/返回类型定义。"""

from typing import Literal, TypedDict

type Method = Literal["ping", "echo", "shutdown"]

METHOD_PING: Method = "ping"
METHOD_ECHO: Method = "echo"
METHOD_SHUTDOWN: Method = "shutdown"


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
