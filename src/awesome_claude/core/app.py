"""应用装配 - 组装服务器各组件并启动。"""

import asyncio
import signal
from collections.abc import Callable
from pathlib import Path

from awesome_claude.core.agent.loop import AgentLoop
from awesome_claude.core.config import ServerConfig, load_server_config
from awesome_claude.core.llm.anthropic_client import AnthropicClient
from awesome_claude.core.llm.base import LLMProvider
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.core.router.dispatcher import create_dispatcher
from awesome_claude.core.server.tcp import TCPServer
from awesome_claude.core.session.channel import SessionChannel
from awesome_claude.core.session.registry import SessionRegistry
from awesome_claude.core.task.manager import TaskManager
from awesome_claude.core.tools.builtin import create_fs_tools, create_time_tool
from awesome_claude.core.tools.context import ToolContext
from awesome_claude.core.tools.registry import ToolRegistry
from awesome_claude.shared.logging.app_logger import get_app_logger, setup_app_logging
from awesome_claude.shared.logging.task_tracker import get_task_tracker


def build_context_factory(
    task_manager: TaskManager,
    llm_client: LLMProvider,
    config: ServerConfig,
    agent_loop: AgentLoop,
) -> Callable[[SessionChannel], HandlerContext]:
    """构造会话上下文工厂。

    Args:
        task_manager: 任务管理器。
        llm_client: LLM 客户端。
        config: 服务端配置。
        agent_loop: Agent 循环编排器。

    Returns:
        接收 SessionChannel 并返回 HandlerContext 的工厂函数。
    """

    def factory(channel: SessionChannel) -> HandlerContext:
        return HandlerContext(
            task_manager=task_manager,
            llm_client=llm_client,
            sessions=channel,
            config=config,
            agent_loop=agent_loop,
        )

    return factory


async def run_server(config: ServerConfig | None = None) -> None:
    """服务端启动流程。

    Args:
        config: 服务端配置，缺省时从环境变量加载。
    """
    config = config or load_server_config()
    setup_app_logging(Path(config.log_dir) / "server.log", level=config.log_level)
    logger = get_app_logger("core.app")

    task_tracker = get_task_tracker()
    task_manager = TaskManager(task_tracker)
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
    for tool in [create_time_tool(), *create_fs_tools()]:
        tool_registry.register(tool)
    agent_loop = AgentLoop(llm_client, tool_registry)
    dispatcher = create_dispatcher()
    session_registry = SessionRegistry()
    context_factory = build_context_factory(
        task_manager, llm_client, config, agent_loop
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
