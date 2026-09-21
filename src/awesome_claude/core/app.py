"""应用装配 - 组装服务器各组件并启动。"""

import asyncio
import signal
from collections.abc import Callable
from pathlib import Path

from awesome_claude.core.agent.loop import AgentLoop
from awesome_claude.core.agent.prompt import PromptContext, ToolBrief
from awesome_claude.core.config import ServerConfig, load_server_config
from awesome_claude.core.llm.anthropic_client import AnthropicClient
from awesome_claude.core.llm.base import LLMProvider
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.core.router.dispatcher import create_dispatcher
from awesome_claude.core.server.tcp import TCPServer
from awesome_claude.core.session.channel import SessionChannel
from awesome_claude.core.session.registry import SessionRegistry
from awesome_claude.core.tools.builtin import (
    create_fs_tools,
    create_plan_tools,
    create_time_tool,
)
from awesome_claude.core.tools.context import ToolContext
from awesome_claude.core.tools.registry import ToolRegistry
from awesome_claude.shared.logging.app_logger import get_app_logger, setup_app_logging
from awesome_claude.shared.logging.trace_store import TraceStore, get_trace_store


def build_context_factory(
    trace_store: TraceStore,
    llm_client: LLMProvider,
    config: ServerConfig,
    agent_loop: AgentLoop,
    system_prompt: PromptContext | None = None,
) -> Callable[[SessionChannel], HandlerContext]:
    """构造会话上下文工厂。

    Args:
        trace_store: 轨迹存储。
        llm_client: LLM 客户端。
        config: 服务端配置。
        agent_loop: Agent 循环编排器。
        system_prompt: System Prompt 静态上下文（可选）。

    Returns:
        接收 SessionChannel 并返回 HandlerContext 的工厂函数。
    """

    def factory(channel: SessionChannel) -> HandlerContext:
        return HandlerContext(
            trace_store=trace_store,
            llm_client=llm_client,
            sessions=channel,
            config=config,
            agent_loop=agent_loop,
            system_prompt=system_prompt,
        )

    return factory


def _build_prompt_context(
    config: ServerConfig, tool_context: ToolContext, registry: ToolRegistry
) -> PromptContext:
    """根据配置与已注册工具构造 System Prompt 上下文。

    Args:
        config: 服务端配置。
        tool_context: 工具执行环境（沙箱根与读写限额）。
        registry: 工具注册表。

    Returns:
        PromptContext 实例。
    """
    tools = tuple(
        ToolBrief(name=tool.name, description=tool.description)
        for tool in (registry.get(name) for name in registry.names())
        if tool is not None
    )
    return PromptContext(
        workspace_root=tool_context.workspace_root,
        fs_max_read=tool_context.fs_max_read,
        fs_max_write=tool_context.fs_max_write,
        max_tokens=config.max_tokens,
        tools=tools,
        model=config.model,
    )


async def run_server(config: ServerConfig | None = None) -> None:
    """服务端启动流程。

    Args:
        config: 服务端配置，缺省时从环境变量加载。
    """
    config = config or load_server_config()
    setup_app_logging(Path(config.log_dir) / "server.log", level=config.log_level)
    logger = get_app_logger("core.app")

    trace_store = get_trace_store()
    llm_client = AnthropicClient(
        api_key=config.api_key,
        model=config.model,
        max_tokens=config.max_tokens,
        base_url=config.base_url,
    )
    tool_context = ToolContext(
        workspace_root=config.workspace_dir,
        fs_max_read=config.fs_max_read,
        fs_max_write=config.fs_max_write,
    )
    tool_registry = ToolRegistry(tool_context)
    for tool in [create_time_tool(), *create_fs_tools(), *create_plan_tools()]:
        tool_registry.register(tool)
    agent_loop = AgentLoop(llm_client, tool_registry)
    system_prompt = _build_prompt_context(config, tool_context, tool_registry)
    dispatcher = create_dispatcher()
    session_registry = SessionRegistry()
    context_factory = build_context_factory(
        trace_store, llm_client, config, agent_loop, system_prompt
    )
    server = TCPServer(
        config.host, config.port, dispatcher, context_factory, session_registry
    )

    await server.start()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, server.request_shutdown)

    logger.info("core server running", host=config.host, port=config.port)
    try:
        await server.wait_for_shutdown()
    finally:
        await server.stop()
        await llm_client.close()
        logger.info("core server exited")


def main() -> None:
    """命令行入口。"""
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
