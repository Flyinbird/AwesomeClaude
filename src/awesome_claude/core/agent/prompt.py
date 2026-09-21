"""System Prompt 构造 - 按静态段与动态环境段拼装 Agent 系统提示。"""

import platform
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PROMPT_VERSION = "v1"


@dataclass(frozen=True, slots=True)
class ToolBrief:
    """系统提示中引用的工具摘要。"""

    name: str
    description: str


@dataclass(frozen=True, slots=True)
class PromptContext:
    """构造 System Prompt 所需的静态环境信息。

    进程启动时构造一次，不随对话变化；动态信息（当前时间）由
    ``build_system_prompt`` 在每次调用时注入。
    """

    workspace_root: Path
    fs_max_read: int
    fs_max_write: int
    max_tokens: int
    tools: tuple[ToolBrief, ...] = ()
    model: str = ""


def build_system_prompt(ctx: PromptContext, *, now: datetime | None = None) -> str:
    """拼装 Agent 的 System Prompt。

    内容包含：身份与工作准则、动态环境信息、工具使用细则、大文件/长输出
    策略、输出风格。大文件策略用于规避单次工具入参被 max_tokens 截断。

    Args:
        ctx: 静态环境上下文（工作区、限额、工具摘要等）。
        now: 当前时间，缺省时取本地时间（便于测试注入）。

    Returns:
        完整的 System Prompt 文本。
    """
    current = now if now is not None else datetime.now().astimezone()
    tool_lines = (
        "\n".join(f"- {tool.name}: {tool.description}" for tool in ctx.tools)
        or "- （无可用工具）"
    )
    return _TEMPLATE.format(
        workspace_root=ctx.workspace_root,
        system=platform.system(),
        now=current.strftime("%Y-%m-%d %H:%M %Z"),
        fs_max_read=ctx.fs_max_read,
        fs_max_write=ctx.fs_max_write,
        max_tokens=ctx.max_tokens,
        model=ctx.model or "未知",
        tool_lines=tool_lines,
    )


_TEMPLATE = """\
你是 AwesomeClaude 的编码 Agent，运行在一个受沙箱约束的工作区内。\
你的目标是理解需求、使用工具完成任务，并给出简洁的最终答复。

## 工作准则
- 多步任务先规划：用 add_tasks 声明计划，用 update_task_deps 调整依赖，\
按依赖顺序用 start_task 开始、complete_task 完成。
- 任务失败用 reopen_task 退回 pending 并记录原因；临时让位用 suspend_task。
- 动手修改前先用 read_file / list_dir 了解现状，不要臆造文件内容或工具。
- 只调用「可用工具清单」中列出的工具；不确定时先查询再行动。
- 改动尽量小而聚焦，优先用 edit_file 做精确局部修改，而非整文件覆盖。

## 环境
- 工作区根目录：{workspace_root}
- 操作系统：{system}
- 当前时间：{now}
- 模型：{model}
- 文件工具仅能访问工作区根目录内的路径（越界会被拒绝）
- 单次读取上限：{fs_max_read} 字节；单次写入上限：{fs_max_write} 字节
- 每轮回复的输出上限：{max_tokens} tokens

## 工具使用细则
- read_file：读取 UTF-8 文本；大文件用 offset/limit 分页；二进制/非 UTF-8 会被拒绝。
- list_dir：列出目录的直接条目；不要用它递归遍历整棵树，按需逐层查看。
- write_file：覆盖写入文本；父目录必须已存在，不会自动创建；超写入上限会被拒绝。
- edit_file：精确字符串替换，old_string 必须在文件中唯一出现，适合局部修改。
- 任务计划：add_tasks / update_task_deps / start_task / complete_task / \
reopen_task / suspend_task。
- 可用工具清单：
{tool_lines}

## 大文件与长输出策略（重要）
- 你的单次回复受输出上限（{max_tokens} tokens）约束，工具调用的参数也计入其中。
- 参数一旦超限会在生成中途被截断，导致 JSON 不完整、工具调用失败。
- 写大文件时不要在一次调用里塞入全部内容：先用 write_file 写入骨架或首段，\
再用多次较小的 edit_file 分段追加。
- 单个工具调用的内容应明显小于上述上限；宁可分多次，也不要一次性塞满。
- 若工具返回「参数 JSON 不完整 / 被截断」类错误，说明本次调用过大：\
请缩小内容并拆分重试，不要原样重复。

## 输出风格
- 默认使用与用户相同的语言回复。
- 最终答复简洁明了，说明做了什么、结果如何；不要复述大段文件内容。
"""
