"""内置工具：Run 内任务计划（Task DAG）的声明与推进。"""

from typing import Any

from awesome_claude.core.task.graph import TaskGraph
from awesome_claude.core.tools.base import Tool
from awesome_claude.core.tools.context import ToolContext, ToolScope


def _require_graph(scope: ToolScope | None) -> TaskGraph:
    """从运行态作用域取出任务图。

    Args:
        scope: 本次 Run 的运行态作用域。

    Returns:
        当前 Run 的任务图。

    Raises:
        ValueError: 未提供作用域或该 Run 未启用任务计划。
    """
    if scope is None or scope.task_graph is None:
        raise ValueError("当前对话未启用任务计划")
    return scope.task_graph


def _plan_result(graph: TaskGraph, **extra: Any) -> dict[str, Any]:
    """构造统一的工具返回：最新任务快照 + 附加信息。"""
    return {"tasks": graph.snapshot(), **extra}


async def _add_tasks(
    args: dict[str, Any], ctx: ToolContext, scope: ToolScope | None = None
) -> dict[str, Any]:
    """追加任务（可声明依赖，构成 DAG）。"""
    graph = _require_graph(scope)
    specs = args.get("tasks")
    if not isinstance(specs, list):
        raise TypeError("tasks 必须是任务数组")
    added = await graph.add_tasks(specs)
    return _plan_result(graph, added=[t.id for t in added])


async def _update_task_deps(
    args: dict[str, Any], ctx: ToolContext, scope: ToolScope | None = None
) -> dict[str, Any]:
    """为待启动任务补充前置依赖。"""
    graph = _require_graph(scope)
    task_id = args.get("task_id")
    deps = args.get("deps")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id 必须是非空字符串")
    if not isinstance(deps, list):
        raise TypeError("deps 必须是字符串数组")
    task = await graph.update_deps(task_id, deps)
    return _plan_result(graph, updated=task.id)


async def _start_task(
    args: dict[str, Any], ctx: ToolContext, scope: ToolScope | None = None
) -> dict[str, Any]:
    """启动一个依赖已满足的任务。"""
    graph = _require_graph(scope)
    task_id = _require_task_id(args)
    task = await graph.start_task(task_id)
    return _plan_result(graph, started=task.id)


async def _complete_task(
    args: dict[str, Any], ctx: ToolContext, scope: ToolScope | None = None
) -> dict[str, Any]:
    """完成一个进行中任务，可附结果。"""
    graph = _require_graph(scope)
    task_id = _require_task_id(args)
    result = args.get("result")
    if result is not None and not isinstance(result, str):
        raise ValueError("result 必须是字符串")
    task = await graph.complete_task(task_id, result)
    return _plan_result(graph, completed=task.id)


async def _reopen_task(
    args: dict[str, Any], ctx: ToolContext, scope: ToolScope | None = None
) -> dict[str, Any]:
    """因失败将任务退回待启动，累计尝试次数并记录错误。"""
    graph = _require_graph(scope)
    task_id = _require_task_id(args)
    error = args.get("error")
    if not isinstance(error, str) or not error:
        raise ValueError("error 必须是非空字符串")
    task = await graph.reopen_task(task_id, error)
    return _plan_result(graph, reopened=task.id)


async def _suspend_task(
    args: dict[str, Any], ctx: ToolContext, scope: ToolScope | None = None
) -> dict[str, Any]:
    """将进行中任务让位退回待启动（不计尝试次数）。"""
    graph = _require_graph(scope)
    task_id = _require_task_id(args)
    task = await graph.suspend_task(task_id)
    return _plan_result(graph, suspended=task.id)


def _require_task_id(args: dict[str, Any]) -> str:
    task_id = args.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("task_id 必须是非空字符串")
    return task_id


_TASK_ID_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"task_id": {"type": "string", "description": "目标任务 id"}},
    "required": ["task_id"],
}


def create_plan_tools() -> list[Tool]:
    """创建任务计划内置工具集合。

    Returns:
        add_tasks / update_task_deps / start_task / complete_task /
        reopen_task / suspend_task 工具定义列表。
    """
    return [
        Tool(
            name="add_tasks",
            description=(
                "声明本对话需要完成的任务及其依赖（构成 DAG）。"
                "每项含 id、goal 与可选 deps（依赖的任务 id）。"
                "执行中可多次调用以追加任务。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "goal": {"type": "string"},
                                "deps": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["id", "goal"],
                        },
                    }
                },
                "required": ["tasks"],
            },
            handler=_add_tasks,
        ),
        Tool(
            name="update_task_deps",
            description="为尚未启动的任务补充前置依赖（依赖完成前不可启动）。",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "deps": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["task_id", "deps"],
            },
            handler=_update_task_deps,
        ),
        Tool(
            name="start_task",
            description=(
                "开始执行某个任务。要求其依赖全部完成，且当前没有其他进行中的任务。"
            ),
            input_schema=_TASK_ID_SCHEMA,
            handler=_start_task,
        ),
        Tool(
            name="complete_task",
            description="标记任务完成，可用 result 简述产出。",
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "result": {"type": "string"},
                },
                "required": ["task_id"],
            },
            handler=_complete_task,
        ),
        Tool(
            name="reopen_task",
            description=(
                "任务遇到失败时将其退回待启动以重试，需给出 error 说明，"
                "并应思考是否更换策略。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "error": {"type": "string"},
                },
                "required": ["task_id", "error"],
            },
            handler=_reopen_task,
        ),
        Tool(
            name="suspend_task",
            description="为插入前置任务而暂时让出进行中的任务（不计为失败重试）。",
            input_schema=_TASK_ID_SCHEMA,
            handler=_suspend_task,
        ),
    ]
