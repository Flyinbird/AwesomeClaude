"""请求上下文 - 处理器共享的上下文对象。"""

from dataclasses import dataclass

from awesome_claude.core.agent.loop import AgentLoop
from awesome_claude.core.agent.prompt import PromptContext
from awesome_claude.core.config import ServerConfig
from awesome_claude.core.llm.base import LLMProvider
from awesome_claude.core.session.channel import SessionChannel
from awesome_claude.core.session.run import Run
from awesome_claude.shared.logging.trace_store import TraceStore


@dataclass(slots=True)
class HandlerContext:
    """传递给 handler 的运行时上下文。"""

    trace_store: TraceStore
    llm_client: LLMProvider
    sessions: SessionChannel
    config: ServerConfig
    agent_loop: AgentLoop | None = None
    run: Run | None = None
    system_prompt: PromptContext | None = None
