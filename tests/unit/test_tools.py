"""core/tools 工具注册表测试。"""

from pathlib import Path
from typing import Any

from awesome_claude.core.permissions.broker import NonInteractiveBroker
from awesome_claude.core.permissions.manager import PermissionManager
from awesome_claude.core.permissions.policy import PermissionPolicy
from awesome_claude.core.permissions.types import PermissionDecision
from awesome_claude.core.tools.base import Tool, ToolResult
from awesome_claude.core.tools.context import ToolContext, ToolScope
from awesome_claude.core.tools.registry import ToolRegistry


async def _echo(
    args: dict[str, Any], ctx: ToolContext, scope: ToolScope | None = None
) -> str:
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
        async def number(
            _: dict[str, Any], ctx: ToolContext, scope: ToolScope | None = None
        ) -> int:
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
        async def boom(
            _: dict[str, Any], ctx: ToolContext, scope: ToolScope | None = None
        ) -> str:
            raise RuntimeError("boom")

        reg = ToolRegistry()
        reg.register(Tool(name="boom", description="d", input_schema={}, handler=boom))
        result = await reg.execute("boom", {})
        assert result.is_error is True
        assert "RuntimeError" in result.content

    async def test_execute_injects_registry_context(self, tmp_path: Path) -> None:
        seen: dict[str, object] = {}

        async def probe(
            args: dict[str, Any], ctx: ToolContext, scope: ToolScope | None = None
        ) -> str:
            seen["root"] = ctx.workspace_root
            return "ok"

        reg = ToolRegistry(ToolContext(workspace_root=tmp_path))
        reg.register(
            Tool(name="probe", description="d", input_schema={}, handler=probe)
        )
        result = await reg.execute("probe", {})
        assert result.is_error is False
        assert seen["root"] == tmp_path

    async def test_execute_permission_denied_blocks_handler(self) -> None:
        calls: list[int] = []

        async def handler(
            args: dict[str, Any], ctx: ToolContext, scope: ToolScope | None = None
        ) -> str:
            calls.append(1)
            return "ran"

        reg = ToolRegistry()
        reg.register(Tool(name="t", description="d", input_schema={}, handler=handler))
        manager = PermissionManager(
            PermissionPolicy(global_default=PermissionDecision.DENY),
            NonInteractiveBroker(),
        )
        scope = ToolScope(run_id="r1", permissions=manager)

        result = await reg.execute("t", {}, scope)

        assert result.is_error is True
        assert "权限被拒绝" in result.content
        assert calls == []

    async def test_execute_permission_allowed_runs_handler(self) -> None:
        calls: list[int] = []

        async def handler(
            args: dict[str, Any], ctx: ToolContext, scope: ToolScope | None = None
        ) -> str:
            calls.append(1)
            return "ran"

        reg = ToolRegistry()
        reg.register(Tool(name="t", description="d", input_schema={}, handler=handler))
        manager = PermissionManager(
            PermissionPolicy(global_default=PermissionDecision.ALLOW),
            NonInteractiveBroker(),
        )
        scope = ToolScope(run_id="r1", permissions=manager)

        result = await reg.execute("t", {}, scope)

        assert result.content == "ran"
        assert result.is_error is False
        assert calls == [1]

    async def test_execute_passthrough_without_permission_manager(self) -> None:
        reg = ToolRegistry()
        reg.register(Tool(name="echo", description="d", input_schema={}, handler=_echo))
        scope = ToolScope(run_id="r1")
        result = await reg.execute("echo", {"text": "hi"}, scope)
        assert result == ToolResult(content="hi")
