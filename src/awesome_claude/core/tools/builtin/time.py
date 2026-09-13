"""内置工具：获取当前服务器本地时间。"""

import time
from typing import Any

from awesome_claude.core.tools.base import Tool
from awesome_claude.core.tools.context import ToolContext


async def _get_time(args: dict[str, Any], ctx: ToolContext) -> str:
    """返回当前服务器本地时间字符串。

    Args:
        args: 工具参数（未使用）。
        ctx: 工具执行上下文（未使用）。

    Returns:
        本地时间字符串，格式 YYYY-MM-DD HH:MM:SS。
    """
    return time.strftime("%Y-%m-%d %H:%M:%S %Z")


def create_time_tool() -> Tool:
    """创建 get_time 内置工具。

    Returns:
        get_time 工具定义。
    """
    return Tool(
        name="get_time",
        description="返回当前服务器本地时间（格式 YYYY-MM-DD HH:MM:SS）。",
        input_schema={"type": "object", "properties": {}, "required": []},
        handler=_get_time,
    )
