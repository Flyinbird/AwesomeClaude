"""工具注册表 - 注册、查询与执行工具。"""

import json
from typing import Any

from awesome_claude.core.tools.base import Tool, ToolResult
from awesome_claude.core.tools.context import ToolContext
from awesome_claude.shared.logging.app_logger import get_app_logger


class ToolRegistry:
    """工具注册表，管理 Tool 的注册、查询与执行。

    Registry 持有全局 ToolContext（沙箱根、限额等），执行工具时随
    handler(args, ctx) 一并注入；构造时未显式提供则使用 ToolContext
    默认值（workspace_root = 进程 cwd）。
    """

    def __init__(self, ctx: ToolContext | None = None) -> None:
        """初始化注册表并绑定执行上下文。

        Args:
            ctx: 工具执行上下文，缺省时使用 ToolContext() 默认值。
        """
        self._tools: dict[str, Tool] = {}
        self._ctx = ctx if ctx is not None else ToolContext()
        self._logger = get_app_logger("core.tools")

    def register(self, tool: Tool) -> None:
        """注册一个工具（同名会覆盖）。

        Args:
            tool: 工具定义。
        """
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """按名称查找工具。

        Args:
            name: 工具名。

        Returns:
            对应工具，不存在时返回 None。
        """
        return self._tools.get(name)

    def names(self) -> list[str]:
        """返回全部已注册工具名。

        Returns:
            工具名列表。
        """
        return list(self._tools)

    def to_anthropic_tools(self) -> list[dict[str, Any]]:
        """转换为 Anthropic 工具 schema 列表。

        Returns:
            供 LLM 调用使用的 tools 参数。
        """
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            }
            for t in self._tools.values()
        ]

    async def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        """执行指定工具。

        工具未注册或执行抛异常时返回 is_error=True 的 ToolResult，
        不向上抛出，保证 agent loop 可将失败回填给 LLM。

        Args:
            name: 工具名。
            args: 工具参数。

        Returns:
            工具执行结果。
        """
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(content=f"未知工具: {name}", is_error=True)
        try:
            result = await tool.handler(args, self._ctx)
        except Exception as exc:
            self._logger.exception("tool execution failed", tool=name)
            return ToolResult(content=f"{type(exc).__name__}: {exc}", is_error=True)
        if isinstance(result, str):
            content = result
        else:
            content = json.dumps(result, ensure_ascii=False, default=str)
        return ToolResult(content=content)
