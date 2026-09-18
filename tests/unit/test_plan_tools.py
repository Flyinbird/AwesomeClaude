"""core/tools/builtin/plan 任务计划工具测试。"""

import json

from awesome_claude.core.task.graph import TaskGraph
from awesome_claude.core.tools.builtin.plan import create_plan_tools
from awesome_claude.core.tools.context import ToolScope
from awesome_claude.core.tools.registry import ToolRegistry


def _registry_with_scope() -> tuple[ToolRegistry, ToolScope, TaskGraph]:
    registry = ToolRegistry()
    for tool in create_plan_tools():
        registry.register(tool)
    graph = TaskGraph("run1")
    scope = ToolScope(run_id="run1", task_graph=graph)
    return registry, scope, graph


def _payload(result_content: str) -> dict:
    return json.loads(result_content)


class TestPlanTools:
    """计划工具的成功路径与错误路径测试。"""

    async def test_add_tasks(self) -> None:
        registry, scope, _ = _registry_with_scope()
        result = await registry.execute(
            "add_tasks",
            {
                "tasks": [
                    {"id": "a", "goal": "读文件"},
                    {"id": "b", "goal": "写", "deps": ["a"]},
                ]
            },
            scope,
        )
        assert not result.is_error
        payload = _payload(result.content)
        assert payload["added"] == ["a", "b"]
        assert [t["id"] for t in payload["tasks"]] == ["a", "b"]

    async def test_start_and_complete(self) -> None:
        registry, scope, graph = _registry_with_scope()
        await registry.execute(
            "add_tasks", {"tasks": [{"id": "a", "goal": "x"}]}, scope
        )
        started = await registry.execute("start_task", {"task_id": "a"}, scope)
        assert not started.is_error
        assert graph.current_task_id() == "a"
        completed = await registry.execute(
            "complete_task", {"task_id": "a", "result": "done"}, scope
        )
        assert not completed.is_error
        assert graph.get("a").status.value == "completed"

    async def test_start_with_unmet_deps_is_tool_error(self) -> None:
        registry, scope, _ = _registry_with_scope()
        await registry.execute(
            "add_tasks",
            {
                "tasks": [
                    {"id": "a", "goal": "x"},
                    {"id": "b", "goal": "y", "deps": ["a"]},
                ]
            },
            scope,
        )
        result = await registry.execute("start_task", {"task_id": "b"}, scope)
        assert result.is_error
        assert "依赖未完成" in result.content

    async def test_second_start_is_tool_error(self) -> None:
        registry, scope, _ = _registry_with_scope()
        await registry.execute(
            "add_tasks",
            {"tasks": [{"id": "a", "goal": "x"}, {"id": "b", "goal": "y"}]},
            scope,
        )
        await registry.execute("start_task", {"task_id": "a"}, scope)
        result = await registry.execute("start_task", {"task_id": "b"}, scope)
        assert result.is_error

    async def test_reopen_records_attempt(self) -> None:
        registry, scope, _ = _registry_with_scope()
        await registry.execute(
            "add_tasks", {"tasks": [{"id": "a", "goal": "x"}]}, scope
        )
        await registry.execute("start_task", {"task_id": "a"}, scope)
        result = await registry.execute(
            "reopen_task", {"task_id": "a", "error": "boom"}, scope
        )
        assert not result.is_error
        payload = _payload(result.content)
        task = next(t for t in payload["tasks"] if t["id"] == "a")
        assert task["status"] == "pending"
        assert task["attempts"] == 1

    async def test_suspend(self) -> None:
        registry, scope, graph = _registry_with_scope()
        await registry.execute(
            "add_tasks", {"tasks": [{"id": "a", "goal": "x"}]}, scope
        )
        await registry.execute("start_task", {"task_id": "a"}, scope)
        result = await registry.execute("suspend_task", {"task_id": "a"}, scope)
        assert not result.is_error
        assert graph.current_task_id() is None
        assert graph.get("a").attempts == 0

    async def test_update_task_deps(self) -> None:
        registry, scope, graph = _registry_with_scope()
        await registry.execute(
            "add_tasks",
            {"tasks": [{"id": "a", "goal": "x"}, {"id": "b", "goal": "y"}]},
            scope,
        )
        result = await registry.execute(
            "update_task_deps", {"task_id": "b", "deps": ["a"]}, scope
        )
        assert not result.is_error
        assert graph.get("b").deps == ["a"]

    async def test_no_scope_is_tool_error(self) -> None:
        registry, _, _ = _registry_with_scope()
        result = await registry.execute(
            "add_tasks", {"tasks": [{"id": "a", "goal": "x"}]}
        )
        assert result.is_error

    async def test_invalid_args_is_tool_error(self) -> None:
        registry, scope, _ = _registry_with_scope()
        result = await registry.execute("add_tasks", {"tasks": "nope"}, scope)
        assert result.is_error
