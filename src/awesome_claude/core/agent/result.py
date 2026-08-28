"""Agent 运行结果类型。"""

from dataclasses import dataclass, field
from typing import Any

from awesome_claude.shared.types import TokenUsage


@dataclass(frozen=True, slots=True)
class AgentResult:
    """一次 agent 循环运行的最终结果。"""

    text: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=lambda: TokenUsage(0, 0))
    steps: int = 0
    stop_reason: str = ""
