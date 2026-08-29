"""请求上下文 - 处理器共享的上下文对象。"""

from dataclasses import dataclass

from awesome_claude.core.agent.loop import AgentLoop
from awesome_claude.core.config import ServerConfig
from awesome_claude.core.llm.base import LLMProvider
from awesome_claude.core.session.channel import SessionChannel
from awesome_claude.core.task.manager import TaskManager


@dataclass(slots=True)
class HandlerContext:
    """传递给 handler 的运行时上下文。"""

    task_manager: TaskManager
    llm_client: LLMProvider
    sessions: SessionChannel
    config: ServerConfig
    agent_loop: AgentLoop | None = None
