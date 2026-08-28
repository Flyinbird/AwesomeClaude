"""LLM 流式事件类型定义。"""

from dataclasses import dataclass

from awesome_claude.shared.types import TokenUsage


@dataclass(frozen=True, slots=True)
class TextDeltaEvent:
    """文本增量事件。"""

    text: str


@dataclass(frozen=True, slots=True)
class DoneEvent:
    """流式结束事件。"""

    stop_reason: str
    full_text: str
    usage: TokenUsage


type LLMStreamEvent = TextDeltaEvent | DoneEvent
