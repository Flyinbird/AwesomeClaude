"""工具执行上下文 - 每次工具调用注入运行所需的配置。"""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ToolContext:
    """工具 handler(args, ctx) 的执行上下文。

    集中承载工具所需的运行配置（沙箱根目录、读写限额等），
    后续新增工具能力（命令执行策略、会话上下文等）直接在此扩展字段，
    不破坏既有工具签名。
    """

    workspace_root: Path = field(default_factory=Path.cwd)
    fs_max_read: int = 30000
    fs_max_write: int = 100000
