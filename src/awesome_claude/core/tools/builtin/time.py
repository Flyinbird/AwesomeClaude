"""内置工具：获取当前服务器本地时间。"""

import time

from awesome_claude.core.tools.base import Tool


async def _get_time(args: dict[str, object]) -> str:
    """返回当前服务器本地时间字符串。"""
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
