"""工具抽象 - 工具定义与执行结果类型。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

type ToolHandler = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True, slots=True)
class Tool:
    """工具定义：名称、描述、输入 schema 与异步执行函数。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    handler: ToolHandler


@dataclass(frozen=True, slots=True)
class ToolResult:
    """工具执行结果，content 以字符串形式回填给 LLM。"""

    content: str
    is_error: bool = False
