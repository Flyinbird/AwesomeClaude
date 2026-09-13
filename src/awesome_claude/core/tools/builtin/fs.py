"""内置工具：受工作区沙箱约束的文件系统读写工具。"""

import asyncio
from pathlib import Path
from typing import Any

from awesome_claude.core.tools.base import Tool
from awesome_claude.core.tools.context import ToolContext

_MARK_TRUNCATED = "\n…[内容过长，已省略 {omitted} 字节以控制上下文]…"


class FsToolError(Exception):
    """文件系统工具执行错误（文件不存在、二进制、超限等）。"""


class PathOutsideRootError(FsToolError):
    """目标路径超出工作区沙箱根目录。"""


def _resolve_target(root: Path, raw: object) -> Path:
    """将 path 参数解析为沙箱内的规范绝对路径。

    相对路径以 root 为基准拼接；随后做 realpath 解析（含符号链接），
    解析结果必须仍落在 root 之内，否则视为越权访问。

    Args:
        root: 工作区沙箱根目录。
        raw: 用户提供的路径参数。

    Returns:
        沙箱内的规范绝对路径。

    Raises:
        ValueError: path 不是字符串或为空。
        PathOutsideRootError: 解析后的路径越出 root。
    """
    if not isinstance(raw, str):
        raise TypeError("path 必须是字符串")
    if not raw.strip():
        raise ValueError("path 不能为空")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    root_resolved = root.resolve()
    if not resolved.is_relative_to(root_resolved):
        raise PathOutsideRootError(
            f"path 超出工作区根目录（{root_resolved}）: {resolved}"
        )
    return resolved


def _read_text(target: Path) -> tuple[bytes, str]:
    """读取 UTF-8 文本文件字节与原样解码结果。

    Args:
        target: 已通过沙箱校验的目标文件。

    Returns:
        (原始字节, 解码后的文本)。

    Raises:
        FsToolError: 文件不存在/为目录、疑似二进制、非 UTF-8 编码。
    """
    if not target.is_file():
        raise FsToolError(f"文件不存在或不是普通文件: {target}")
    data = target.read_bytes()
    if b"\x00" in data[:8192]:
        raise FsToolError(f"文件疑似二进制（含空字节），拒绝读取: {target}")
    try:
        return data, data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FsToolError(f"文件不是 UTF-8 文本: {target}") from exc


def _cap_text(content: str, max_bytes: int) -> tuple[str, bool]:
    """将文本按 UTF-8 字节上限截断（保证不切断多字节字符）。

    Args:
        content: 原始文本。
        max_bytes: 允许返回的最大字节数。

    Returns:
        (截断后的文本, 是否发生了截断)。未超限时原样返回。
    """
    data = content.encode("utf-8")
    if len(data) <= max_bytes:
        return content, False
    head = data[:max_bytes]
    omitted = len(data) - len(head)
    head_str = head.decode("utf-8", errors="ignore")
    return head_str + _MARK_TRUNCATED.format(omitted=omitted), True


def _read_file(
    target: Path, offset: int, limit: int | None, max_read: int
) -> dict[str, Any]:
    """按行读取文件内容（供 asyncio.to_thread 调用的同步实现）。

    Args:
        target: 已校验的沙箱内文件路径。
        offset: 起始行号（0-based，含）。
        limit: 最多返回行数，None 表示到文件末尾。
        max_read: 返回内容的字节上限。

    Returns:
        读取结果 dict（path、size、total_lines、start_line、end_line、
        content、truncated）。
    """
    data, text = _read_text(target)
    lines = text.splitlines()
    total_lines = len(lines)
    if offset >= total_lines:
        content = ""
        end_line = offset
    else:
        end = None if limit is None else offset + limit
        selected = lines[offset:end]
        content = "\n".join(selected)
        end_line = offset + len(selected)
    content, truncated = _cap_text(content, max_read)
    return {
        "path": str(target),
        "size": len(data),
        "total_lines": total_lines,
        "start_line": offset,
        "end_line": end_line,
        "content": content,
        "truncated": truncated,
    }


async def _read_file_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """读取文本文件。参数 path 必填，offset/limit 可选。"""
    offset = args.get("offset", 0)
    limit = args.get("limit")
    if isinstance(offset, bool) or not isinstance(offset, int):
        raise TypeError("offset 必须是整数")
    if offset < 0:
        raise ValueError("offset 不能为负数")
    if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int)):
        raise TypeError("limit 必须是整数")
    if limit is not None and limit <= 0:
        raise ValueError("limit 必须大于 0")
    target = _resolve_target(ctx.workspace_root, args.get("path"))
    return await asyncio.to_thread(_read_file, target, offset, limit, ctx.fs_max_read)


def _write_bytes_guarded(target: Path, content: str, max_write: int) -> dict[str, Any]:
    """将文本写入文件（不自动创建父目录，超限拒绝）。

    Args:
        target: 已校验的沙箱内目标文件路径。
        content: 待写入文本。
        max_write: 写入字节上限。

    Returns:
        写入结果 dict（path、bytes）。

    Raises:
        FsToolError: 父目录不存在、目标是目录或超出写入上限。
    """
    if not target.parent.is_dir():
        raise FsToolError(f"父目录不存在（不自动创建）: {target.parent}")
    if target.is_dir():
        raise FsToolError(f"目标是目录: {target}")
    data = content.encode("utf-8")
    if len(data) > max_write:
        raise FsToolError(f"内容 {len(data)} 字节超出单次写入上限 {max_write} 字节")
    target.write_bytes(data)
    return {"path": str(target), "bytes": len(data)}


async def _write_file_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """写入文本文件（覆盖已存在文件）。参数 path、content 必填。"""
    content = args.get("content")
    if not isinstance(content, str):
        raise TypeError("content 必须是字符串")
    target = _resolve_target(ctx.workspace_root, args.get("path"))
    return await asyncio.to_thread(
        _write_bytes_guarded, target, content, ctx.fs_max_write
    )


async def _edit_file_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """精确字符串替换；old_string 必须在文件中唯一出现。"""
    old_string = args.get("old_string")
    new_string = args.get("new_string")
    if not isinstance(old_string, str):
        raise TypeError("old_string 必须是字符串")
    if old_string == "":
        raise ValueError("old_string 不能为空")
    if not isinstance(new_string, str):
        raise TypeError("new_string 必须是字符串")
    target = _resolve_target(ctx.workspace_root, args.get("path"))

    def _edit() -> dict[str, Any]:
        _, text = _read_text(target)
        occurrences = text.count(old_string)
        if occurrences == 0:
            raise FsToolError(f"未在 {target} 中找到 old_string")
        if occurrences > 1:
            raise FsToolError(
                f"old_string 在文件中出现 {occurrences} 次，无法唯一确定替换位置"
            )
        return _write_bytes_guarded(
            target, text.replace(old_string, new_string, 1), ctx.fs_max_write
        )

    return await asyncio.to_thread(_edit)


async def _list_dir_handler(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """列出目录条目；path 省略或为空时默认工作区根目录。"""
    raw = args.get("path")
    if raw is not None and not isinstance(raw, str):
        raise TypeError("path 必须是字符串")
    target = _resolve_target(ctx.workspace_root, raw if raw else ".")
    if not target.is_dir():
        raise FsToolError(f"目录不存在或不是目录: {target}")

    def _list() -> dict[str, Any]:
        entries = []
        for entry in sorted(target.iterdir(), key=lambda p: p.name):
            entries.append(
                {
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "is_link": entry.is_symlink(),
                }
            )
        return {"path": str(target), "count": len(entries), "entries": entries}

    return await asyncio.to_thread(_list)


def create_fs_tools() -> list[Tool]:
    """创建文件系统内置工具集合。

    Returns:
        read_file / write_file / edit_file / list_dir 工具定义列表。
    """
    return [
        Tool(
            name="read_file",
            description=(
                "读取工作区内的 UTF-8 文本文件（二进制文件会被拒绝）。"
                "path 为相对工作区根目录或绝对路径；content 超出限额时截断并标记 truncated。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件路径"},
                    "offset": {
                        "type": "integer",
                        "description": "起始行号（0-based），默认 0",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "最多返回行数，默认全部",
                    },
                },
                "required": ["path"],
            },
            handler=_read_file_handler,
        ),
        Tool(
            name="write_file",
            description=(
                "将文本写入工作区内的文件（覆盖已存在内容）。"
                "父目录必须已存在，不会自动创建；超出单次写入上限会被拒绝。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件路径"},
                    "content": {"type": "string", "description": "待写入的完整文本"},
                },
                "required": ["path", "content"],
            },
            handler=_write_file_handler,
        ),
        Tool(
            name="edit_file",
            description=(
                "在工作区内对文本文件做精确字符串替换。"
                "old_string 必须在文件中唯一出现，否则报错，不会误改多处。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目标文件路径"},
                    "old_string": {"type": "string", "description": "被替换的原文"},
                    "new_string": {"type": "string", "description": "替换后的新文本"},
                },
                "required": ["path", "old_string", "new_string"],
            },
            handler=_edit_file_handler,
        ),
        Tool(
            name="list_dir",
            description=(
                "列出工作区内某目录的直接条目（名称、是否目录/符号链接）。"
                "path 可省略或为空，此时列出工作区根目录。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径，省略/为空时默认为工作区根目录",
                    }
                },
                "required": [],
            },
            handler=_list_dir_handler,
        ),
    ]
