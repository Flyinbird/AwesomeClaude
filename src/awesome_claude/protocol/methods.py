"""JSON-RPC 方法名常量与参数/返回类型定义（Phase 1 + Phase 2 chat + Session）。"""

from typing import Literal, NotRequired, TypedDict

from awesome_claude.shared.types import ChatResponse

__all__ = [
    "METHOD_CHAT",
    "METHOD_ECHO",
    "METHOD_PING",
    "METHOD_SESSION_ATTACH",
    "METHOD_SESSION_DETACH",
    "METHOD_SHUTDOWN",
    "NOTIFY_CHAT_COMPLETED",
    "NOTIFY_CHAT_FAILED",
    "NOTIFY_CHAT_HEARTBEAT",
    "NOTIFY_CHAT_INTERRUPTED",
    "NOTIFY_CHAT_PLAN_UPDATED",
    "NOTIFY_CHAT_STREAM",
    "NOTIFY_CHAT_TOOL_FINISHED",
    "NOTIFY_CHAT_TOOL_STARTED",
    "NOTIFY_CHAT_USER_MESSAGE",
    "ChatAcceptedResult",
    "ChatCompletedNotificationParams",
    "ChatFailedNotificationParams",
    "ChatHeartbeatNotificationParams",
    "ChatParams",
    "ChatResponse",
    "EchoParams",
    "EchoResult",
    "InterruptedNotificationParams",
    "Method",
    "PingParams",
    "PingResult",
    "PlanTaskSnapshot",
    "PlanUpdatedNotificationParams",
    "SessionAttachParams",
    "SessionAttachResult",
    "ShutdownParams",
    "StreamNotificationParams",
    "ToolFinishedNotificationParams",
    "ToolStartedNotificationParams",
    "UserMessageNotificationParams",
]

type Method = Literal[
    "ping", "echo", "shutdown", "chat", "session.attach", "session.detach"
]

METHOD_PING: Method = "ping"
METHOD_ECHO: Method = "echo"
METHOD_SHUTDOWN: Method = "shutdown"
METHOD_CHAT: Method = "chat"
METHOD_SESSION_ATTACH: Method = "session.attach"
METHOD_SESSION_DETACH: Method = "session.detach"

NOTIFY_CHAT_STREAM: str = "chat.stream"
NOTIFY_CHAT_COMPLETED: str = "chat.completed"
NOTIFY_CHAT_FAILED: str = "chat.failed"
NOTIFY_CHAT_HEARTBEAT: str = "chat.heartbeat"
NOTIFY_CHAT_USER_MESSAGE: str = "chat.user_message"
NOTIFY_CHAT_TOOL_STARTED: str = "chat.tool_started"
NOTIFY_CHAT_TOOL_FINISHED: str = "chat.tool_finished"
NOTIFY_CHAT_INTERRUPTED: str = "chat.interrupted"
NOTIFY_CHAT_PLAN_UPDATED: str = "chat.plan_updated"


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
    session_id: NotRequired[str | None]
    max_tokens: NotRequired[int | None]


class ChatAcceptedResult(TypedDict):
    """chat 请求的受理应答（Run 创建成功后立即返回）。"""

    run_id: str
    accepted: bool
    heartbeat_interval_ms: int


class ChatCompletedNotificationParams(TypedDict):
    """chat.completed 对话完成通知参数（Server → Client 推送）。"""

    run_id: str
    text: str
    stop_reason: str
    usage: dict[str, int]
    duration_ms: float
    model: str
    session_id: NotRequired[str | None]


class ChatFailedNotificationParams(TypedDict):
    """chat.failed 对话失败通知参数（Server → Client 推送）。"""

    run_id: str
    error: dict[str, object]
    session_id: NotRequired[str | None]


class ChatHeartbeatNotificationParams(TypedDict):
    """chat.heartbeat 对话心跳通知参数（Server → Client 推送）。"""

    run_id: str
    timestamp: str
    session_id: NotRequired[str | None]


class SessionAttachParams(TypedDict):
    """session.attach 方法参数。"""

    session_id: str


class SessionAttachResult(TypedDict):
    """session.attach 方法返回值（含会话历史回放）。"""

    session_id: str
    history: list[dict[str, object]]
    active_runs: list[str]


class StreamNotificationParams(TypedDict):
    """chat.stream 流式通知参数（Server → Client 推送）。"""

    run_id: str
    chunk_index: int
    text: str
    is_final: bool
    session_id: NotRequired[str | None]


class UserMessageNotificationParams(TypedDict):
    """chat.user_message 通知参数（广播用户输入）。"""

    session_id: str
    message: str


class ToolStartedNotificationParams(TypedDict):
    """chat.tool_started 通知参数（工具调用开始）。"""

    session_id: NotRequired[str | None]
    run_id: str
    step_index: int
    tool_name: str
    args: dict[str, object]


class ToolFinishedNotificationParams(TypedDict):
    """chat.tool_finished 通知参数（工具调用结束）。"""

    session_id: NotRequired[str | None]
    run_id: str
    step_index: int
    tool_name: str
    is_error: bool


class InterruptedNotificationParams(TypedDict):
    """chat.interrupted 通知参数（达到最大步数，Run 未完整完成）。"""

    session_id: NotRequired[str | None]
    run_id: str
    stop_reason: str
    step_index: int


class PlanTaskSnapshot(TypedDict):
    """计划快照中的单个任务。"""

    id: str
    goal: str
    status: str
    deps: list[str]
    attempts: int


class PlanUpdatedNotificationParams(TypedDict):
    """chat.plan_updated 通知参数（任务清单与状态快照）。"""

    run_id: str
    session_id: NotRequired[str | None]
    tasks: list[PlanTaskSnapshot]
