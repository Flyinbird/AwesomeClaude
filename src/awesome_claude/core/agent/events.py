"""Agent 循环结构事件类型（区别于 LLM 流式事件）。"""

from dataclasses import dataclass, field
from typing import Any

from awesome_claude.shared.types import StopReason


@dataclass(frozen=True, slots=True)
class StepStarted:
    """一轮 agent step 开始（一次 LLM 调用）。

    携带本轮实际发出的请求快照（messages / system / tools），供上层
    记录 CONTEXT_BUILT / LLM_REQUEST_SENT 等日志与任务阶段。
    """

    step_index: int
    messages: list[dict[str, Any]] = field(default_factory=list)
    system: str | None = None
    tools: list[dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class StepFinished:
    """一轮 agent step 结束，含本轮 LLM 用量、是否产生工具调用与生成文本。"""

    step_index: int
    stop_reason: StopReason
    input_tokens: int
    output_tokens: int
    has_tool_calls: bool
    text: str = ""


@dataclass(frozen=True, slots=True)
class ToolStarted:
    """一次工具调用开始。"""

    step_index: int
    tool_name: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolFinished:
    """一次工具调用结束。"""

    step_index: int
    tool_name: str
    is_error: bool
    content: str


type AgentEvent = StepStarted | StepFinished | ToolStarted | ToolFinished
