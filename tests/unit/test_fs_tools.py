"""core/tools/builtin/fs 文件系统工具测试（含沙箱安全边界）。"""

import json
from pathlib import Path
from typing import Any

from awesome_claude.core.tools.builtin.fs import (
    FsToolError,
    PathOutsideRootError,
    create_fs_tools,
)
from awesome_claude.core.tools.context import ToolContext
from awesome_claude.core.tools.registry import ToolRegistry


def _registry(
    root: Path, *, max_read: int = 30000, max_write: int = 100000
) -> ToolRegistry:
    """构造以 root 为沙箱、注册了全部 fs 工具的注册表。"""
    ctx = ToolContext(workspace_root=root, fs_max_read=max_read, fs_max_write=max_write)
    registry = ToolRegistry(ctx)
    for tool in create_fs_tools():
        registry.register(tool)
    return registry


async def _exec(
    registry: ToolRegistry, name: str, args: dict[str, Any]
) -> dict[str, Any]:
    """执行工具并将 JSON 结果解析为 dict。"""
    result = await registry.execute(name, args)
    assert result.is_error is False, f"{name} 不应报错: {result.content}"
    return json.loads(result.content)


async def _exec_err(registry: ToolRegistry, name: str, args: dict[str, Any]) -> str:
    """执行工具并返回错误内容（断言 is_error=True）。"""
    result = await registry.execute(name, args)
    assert result.is_error is True, f"{name} 应报错: {result.content}"
    return result.content


def _tools() -> list[str]:
    return [t.name for t in create_fs_tools()]


class TestFsTools:
    """read_file / write_file / edit_file / list_dir 行为测试。"""

    async def test_create_fs_tools_names(self) -> None:
        assert _tools() == ["read_file", "write_file", "edit_file", "list_dir"]

    async def test_read_file_happy_path(self, tmp_path: Path) -> None:
        target = tmp_path / "ws" / "a.txt"
        target.parent.mkdir()
        target.write_text("l0\nl1\nl2")
        reg = _registry(tmp_path / "ws")

        data = await _exec(reg, "read_file", {"path": "a.txt"})
        assert data["content"] == "l0\nl1\nl2"
        assert data["total_lines"] == 3
        assert data["truncated"] is False
        assert Path(data["path"]) == target.resolve()

    async def test_read_file_offset_and_limit(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        (root / "a.txt").write_text("l0\nl1\nl2\nl3")
        reg = _registry(root)

        data = await _exec(reg, "read_file", {"path": "a.txt", "offset": 1, "limit": 2})
        assert data["content"] == "l1\nl2"
        assert data["start_line"] == 1
        assert data["end_line"] == 3

    async def test_read_file_truncates_over_limit(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        (root / "big.txt").write_text("hello world, this is long")
        reg = _registry(root, max_read=8)

        data = await _exec(reg, "read_file", {"path": "big.txt"})
        assert data["truncated"] is True
        assert "已省略" in data["content"]
        assert len(data["content"].encode()) <= 8 + len("已省略".encode()) + 64

    async def test_read_file_missing_is_error(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        reg = _registry(root)
        err = await _exec_err(reg, "read_file", {"path": "nope.txt"})
        assert "不存在" in err

    async def test_read_file_binary_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        (root / "bin.dat").write_bytes(b"\x00\x01\x02")
        reg = _registry(root)
        err = await _exec_err(reg, "read_file", {"path": "bin.dat"})
        assert "二进制" in err

    async def test_write_file_creates_and_overwrites(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        reg = _registry(root)

        data = await _exec(reg, "write_file", {"path": "new.txt", "content": "abc"})
        assert (root / "new.txt").read_text() == "abc"
        assert data["bytes"] == 3

        await _exec(reg, "write_file", {"path": "new.txt", "content": "xyz"})
        assert (root / "new.txt").read_text() == "xyz"

    async def test_write_file_no_auto_mkdir(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        reg = _registry(root)
        err = await _exec_err(
            reg, "write_file", {"path": "sub/new.txt", "content": "x"}
        )
        assert "父目录不存在" in err

    async def test_write_file_over_limit(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        reg = _registry(root, max_write=4)
        err = await _exec_err(reg, "write_file", {"path": "f.txt", "content": "12345"})
        assert "上限" in err

    async def test_edit_file_success(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        (root / "f.txt").write_text("hello world")
        reg = _registry(root)

        await _exec(
            reg,
            "edit_file",
            {"path": "f.txt", "old_string": "world", "new_string": "claude"},
        )
        assert (root / "f.txt").read_text() == "hello claude"

    async def test_edit_file_missing_old_string(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        (root / "f.txt").write_text("hello")
        reg = _registry(root)
        err = await _exec_err(
            reg,
            "edit_file",
            {"path": "f.txt", "old_string": "nope", "new_string": "x"},
        )
        assert "old_string" in err

    async def test_edit_file_ambiguous_match_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        (root / "f.txt").write_text("foo foo")
        reg = _registry(root)
        err = await _exec_err(
            reg,
            "edit_file",
            {"path": "f.txt", "old_string": "foo", "new_string": "bar"},
        )
        assert "2 次" in err
        assert (root / "f.txt").read_text() == "foo foo"

    async def test_list_dir(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        (root / "f.txt").write_text("x")
        (root / "sub").mkdir()
        reg = _registry(root)

        data = await _exec(reg, "list_dir", {"path": "."})
        assert data["count"] == 2
        entries = {e["name"]: e for e in data["entries"]}
        assert entries["f.txt"]["is_dir"] is False
        assert entries["sub"]["is_dir"] is True

    async def test_list_dir_missing_is_error(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        reg = _registry(root)
        err = await _exec_err(reg, "list_dir", {"path": "nope"})
        assert "不存在" in err

    async def test_list_dir_defaults_to_root_when_path_empty(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        (root / "f.txt").write_text("x")
        reg = _registry(root)

        for args in ({}, {"path": ""}):
            data = await _exec(reg, "list_dir", args)
            assert data["count"] == 1
            assert data["entries"][0]["name"] == "f.txt"
            assert Path(data["path"]) == root.resolve()


class TestFsSecurity:
    """沙箱 containment 安全边界测试。"""

    async def test_absolute_path_outside_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("secret")
        reg = _registry(root)

        err = await _exec_err(reg, "read_file", {"path": str(secret)})
        assert "超出工作区" in err

    async def test_dotdot_escape_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("secret")
        reg = _registry(root)

        err = await _exec_err(reg, "read_file", {"path": "sub/../../secret.txt"})
        assert "超出工作区" in err

    async def test_symlink_escape_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("secret")
        (root / "link").symlink_to(secret)
        reg = _registry(root)

        err = await _exec_err(reg, "read_file", {"path": "link"})
        assert "超出工作区" in err

    async def test_symlink_inside_root_allowed(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        (root / "real.txt").write_text("inside")
        (root / "link").symlink_to(root / "real.txt")
        reg = _registry(root)

        data = await _exec(reg, "read_file", {"path": "link"})
        assert data["content"] == "inside"

    async def test_write_outside_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "ws"
        root.mkdir()
        reg = _registry(root)
        outside = tmp_path / "out.txt"
        err = await _exec_err(reg, "write_file", {"path": str(outside), "content": "x"})
        assert "超出工作区" in err
        assert not outside.exists()

    async def test_custom_errors_raised(self, tmp_path: Path) -> None:
        assert issubclass(PathOutsideRootError, FsToolError)
