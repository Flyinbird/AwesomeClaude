"""共享数据类型 - 任务阶段、事件、流式块、用量与对话响应。"""

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
    stop_reason: str
    usage: TokenUsage
    duration_ms: float
    model: str
