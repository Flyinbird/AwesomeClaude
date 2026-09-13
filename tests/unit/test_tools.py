"""core/tools 工具注册表测试。"""

from pathlib import Path
from typing import Any

from awesome_claude.core.tools.base import Tool, ToolResult
from awesome_claude.core.tools.context import ToolContext
from awesome_claude.core.tools.registry import ToolRegistry


async def _echo(args: dict[str, Any], ctx: ToolContext) -> str:
    """回显 text 参数的测试工具。"""
    return str(args.get("text", ""))


class TestToolRegistry:
    """ToolRegistry 注册、查询与执行测试。"""

    async def test_register_and_get(self) -> None:
        reg = ToolRegistry()
        tool = Tool(name="echo", description="d", input_schema={}, handler=_echo)
        reg.register(tool)
        assert reg.get("echo") is tool
        assert reg.get("nope") is None
        assert reg.names() == ["echo"]

    async def test_register_overwrite(self) -> None:
        reg = ToolRegistry()
        first = Tool(name="t", description="a", input_schema={}, handler=_echo)
        second = Tool(name="t", description="b", input_schema={}, handler=_echo)
        reg.register(first)
        reg.register(second)
        assert reg.get("t") is second
        assert reg.names() == ["t"]

    async def test_to_anthropic_tools(self) -> None:
        reg = ToolRegistry()
        reg.register(
            Tool(
                name="echo",
                description="回显",
                input_schema={"type": "object"},
                handler=_echo,
            )
        )
        assert reg.to_anthropic_tools() == [
            {"name": "echo", "description": "回显", "input_schema": {"type": "object"}}
        ]

    async def test_execute_returns_string_content(self) -> None:
        reg = ToolRegistry()
        reg.register(Tool(name="echo", description="d", input_schema={}, handler=_echo))
        result = await reg.execute("echo", {"text": "hi"})
        assert result == ToolResult(content="hi")

    async def test_execute_serializes_non_string_result(self) -> None:
        async def number(_: dict[str, Any], ctx: ToolContext) -> int:
            return 42

        reg = ToolRegistry()
        reg.register(Tool(name="num", description="d", input_schema={}, handler=number))
        result = await reg.execute("num", {})
        assert result.content == "42"
        assert result.is_error is False

    async def test_execute_unknown_tool_is_error(self) -> None:
        reg = ToolRegistry()
        result = await reg.execute("nope", {})
        assert result.is_error is True
        assert "nope" in result.content

    async def test_execute_handler_exception_is_error(self) -> None:
        async def boom(_: dict[str, Any], ctx: ToolContext) -> str:
            raise RuntimeError("boom")

        reg = ToolRegistry()
        reg.register(Tool(name="boom", description="d", input_schema={}, handler=boom))
        result = await reg.execute("boom", {})
        assert result.is_error is True
        assert "RuntimeError" in result.content

    async def test_execute_injects_registry_context(self, tmp_path: Path) -> None:
        seen: dict[str, object] = {}

        async def probe(args: dict[str, Any], ctx: ToolContext) -> str:
            seen["root"] = ctx.workspace_root
            return "ok"

        reg = ToolRegistry(ToolContext(workspace_root=tmp_path))
        reg.register(
            Tool(name="probe", description="d", input_schema={}, handler=probe)
        )
        result = await reg.execute("probe", {})
        assert result.is_error is False
        assert seen["root"] == tmp_path
