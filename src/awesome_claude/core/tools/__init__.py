"""工具抽象层。"""

from awesome_claude.core.tools.base import Tool, ToolResult
from awesome_claude.core.tools.registry import ToolRegistry

__all__ = ["Tool", "ToolRegistry", "ToolResult"]
