"""任务领域模型：三态任务及其失败/让位元数据。"""

from dataclasses import dataclass, field
from enum import StrEnum


class TaskStatus(StrEnum):
    """任务状态：仅三态，失败通过回到 PENDING 表达。"""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass(slots=True)
class Task:
    """一次 Run 内的一个工作单元。

    `attempts` 与 `last_error` 是失败/重试的元数据，不是状态；任务因失败回到
    `PENDING` 时累计 `attempts` 并记录 `last_error`，因让位回到 `PENDING` 时不变。
    """

    id: str
    goal: str
    status: TaskStatus = TaskStatus.PENDING
    deps: list[str] = field(default_factory=list)
    attempts: int = 0
    last_error: str | None = None
    result: str | None = None
