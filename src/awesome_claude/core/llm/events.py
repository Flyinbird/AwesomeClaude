"""LLM 流式事件类型定义。"""

from dataclasses import dataclass
from typing import Any

from awesome_claude.shared.types import TokenUsage


@dataclass(frozen=True, slots=True)
class TextDeltaEvent:
    """文本增量事件。"""

    text: str


@dataclass(frozen=True, slots=True)
class ThinkingDeltaEvent:
    """thinking 块增量事件。"""

    text: str


@dataclass(frozen=True, slots=True)
class ToolUseStartEvent:
    """tool_use 块开始事件。"""

    block_id: str
    name: str


@dataclass(frozen=True, slots=True)
class InputJsonDeltaEvent:
    """工具参数 JSON 增量事件。"""

    block_id: str
    partial_json: str


@dataclass(frozen=True, slots=True)
class ToolUseEndEvent:
    """tool_use 块结束事件，参数已拼接并解析为 dict。"""

    block_id: str
    name: str
    input: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DoneEvent:
    """流式结束事件，携带本轮完整的 assistant message。"""

    stop_reason: str
    full_text: str
    usage: TokenUsage
    message: dict[str, Any]


type LLMStreamEvent = (
    TextDeltaEvent
    | ThinkingDeltaEvent
    | ToolUseStartEvent
    | InputJsonDeltaEvent
    | ToolUseEndEvent
    | DoneEvent
)
