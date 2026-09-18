"""工具执行上下文 - 进程级环境与 per-Run 运行态注入。"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from awesome_claude.core.task.graph import TaskGraph


@dataclass(frozen=True, slots=True)
class ToolContext:
    """工具 handler(args, ctx, scope) 的进程级执行环境。

    集中承载与对话无关的运行配置（沙箱根目录、读写限额等），启动时构造
    一次，不随对话变化；后续新增环境能力直接在此扩展字段。
    """

    workspace_root: Path = field(default_factory=Path.cwd)
    fs_max_read: int = 30000
    fs_max_write: int = 100000


@dataclass(frozen=True, slots=True)
class ToolScope:
    """一次 Run 的运行态作用域，作为参数随工具执行链传入。

    与进程级 `ToolContext` 解耦：`run_id` 标识所属 Run，`task_graph` 是
    本次 Run 的任务清单（可为 None，表示未启用任务计划）。
    """

    run_id: str
    task_graph: "TaskGraph | None" = None
