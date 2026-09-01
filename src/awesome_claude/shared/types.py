"""共享数据类型 - 任务阶段、停止原因、事件、流式块、用量与对话响应。"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TaskStage(StrEnum):
    """任务生命周期阶段。"""

    TASK_CREATED = "task_created"
    CONTEXT_BUILT = "context_built"
    STEP_STARTED = "step_started"
    LLM_REQUEST_SENT = "llm_request_sent"
    LLM_STREAMING = "llm_streaming"
    LLM_RESPONSE_DONE = "llm_response_done"
    TOOL_STARTED = "tool_started"
    TOOL_COMPLETED = "tool_completed"
    TOOL_FAILED = "tool_failed"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    TASK_INTERRUPTED = "task_interrupted"


class StopReason(StrEnum):
    """对话/agent 循环的停止原因。

    同时容纳 LLM 原生停止原因（end_turn / max_tokens / stop_sequence /
    tool_use）与框架层强加的终止原因（max_steps）。未知的 SDK 值归入
    UNKNOWN，保证类型安全的前提下不丢失信息。
    """

    END_TURN = "end_turn"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    TOOL_USE = "tool_use"
    MAX_STEPS = "max_steps"
    UNKNOWN = "unknown"

    @classmethod
    def from_raw(cls, raw: str) -> "StopReason":
        """将 LLM 返回的原始停止原因字符串映射为 StopReason。

        Args:
            raw: SDK 返回的原始 stop_reason 字符串。

        Returns:
            对应的 StopReason 成员，未知值映射为 UNKNOWN。
        """
        try:
            return cls(raw)
        except ValueError:
            return cls.UNKNOWN


@dataclass(frozen=True, slots=True)
class TaskEvent:
    """任务生命周期事件。"""

    task_id: str
    stage: TaskStage
    timestamp: str
    duration_ms: float
    data: dict[str, Any]
    step_index: int | None = None


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """LLM 流式输出的单个片段。"""

    task_id: str
    chunk_index: int
    text: str
    is_final: bool


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """LLM 调用的 token 用量。"""

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class ChatResponse:
    """完整的 LLM 对话响应。"""

    task_id: str
    text: str
    stop_reason: StopReason
    usage: TokenUsage
    duration_ms: float
    model: str
