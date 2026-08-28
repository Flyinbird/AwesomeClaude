"""请求上下文 - 处理器共享的上下文对象。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from awesome_claude.core.config import ServerConfig
from awesome_claude.core.llm.client import LLMClient
from awesome_claude.core.task.manager import TaskManager

type SendNotification = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class HandlerContext:
    """传递给 handler 的运行时上下文。"""

    task_manager: TaskManager
    llm_client: LLMClient
    send_notification: SendNotification
    config: ServerConfig
