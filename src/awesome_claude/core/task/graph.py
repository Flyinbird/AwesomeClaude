"""任务图 - Run 内任务清单与依赖 DAG 的校验与状态转换。"""

from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from awesome_claude.core.task.task import Task, TaskStatus


class TaskGraphError(Exception):
    """任务图非法操作（悬空依赖、成环、状态不允许等）。"""


class TaskChangeKind(StrEnum):
    """任务图变更类型。"""

    ADDED = "added"
    STARTED = "started"
    COMPLETED = "completed"
    REOPENED = "reopened"
    SUSPENDED = "suspended"
    UPDATED = "updated"


@dataclass(frozen=True, slots=True)
class TaskChange:
    """一次任务变更，供观察者派发轨迹与进度通知。"""

    kind: TaskChangeKind
    task: Task


type TaskChangeHandler = Callable[[TaskChange], Awaitable[None]]


class TaskGraph:
    """一次 Run 内的任务清单与依赖图。

    变更通过注入的 `on_change` 观察者派发；所有变更在落地前校验图的有效性
    （依赖存在、无环、状态允许）。
    """

    def __init__(
        self,
        run_id: str,
        *,
        on_change: TaskChangeHandler | None = None,
    ) -> None:
        """初始化空任务图。

        Args:
            run_id: 所属 Run 标识。
            on_change: 变更观察者（可选）。
        """
        self._run_id = run_id
        self._tasks: dict[str, Task] = {}
        self._on_change = on_change

    @property
    def run_id(self) -> str:
        """所属 Run 标识。"""
        return self._run_id

    def get(self, task_id: str) -> Task | None:
        """按 id 查找任务。"""
        return self._tasks.get(task_id)

    def tasks(self) -> list[Task]:
        """返回全部任务（插入顺序）。"""
        return list(self._tasks.values())

    def current_task_id(self) -> str | None:
        """返回当前进行中的任务 id；无则为 None。"""
        for task in self._tasks.values():
            if task.status is TaskStatus.IN_PROGRESS:
                return task.id
        return None

    def snapshot(self) -> list[dict[str, Any]]:
        """返回任务清单快照（id / goal / status / deps / attempts）。"""
        return [
            {
                "id": task.id,
                "goal": task.goal,
                "status": task.status.value,
                "deps": list(task.deps),
                "attempts": task.attempts,
            }
            for task in self._tasks.values()
        ]

    def ready(self) -> list[str]:
        """返回依赖已全部完成的待启动任务 id。"""
        return [
            task.id
            for task in self._tasks.values()
            if task.status is TaskStatus.PENDING and self._deps_completed(task)
        ]

    async def add_tasks(self, specs: list[dict[str, Any]]) -> list[Task]:
        """批量追加任务（可引用已有任务或同批任务作为依赖）。

        Args:
            specs: 任务定义列表，每项含 id、goal 与可选 deps。

        Returns:
            新增的任务列表。

        Raises:
            TaskGraphError: id 缺失/重复、目标为空、依赖不存在或形成环。
        """
        new_tasks: dict[str, Task] = {}
        for spec in specs:
            task = self._parse_spec(spec)
            if task.id in self._tasks or task.id in new_tasks:
                raise TaskGraphError(f"任务 id 重复: {task.id}")
            new_tasks[task.id] = task

        combined = {**self._tasks, **new_tasks}
        self._assert_deps_exist(combined)
        self._assert_acyclic(combined)

        self._tasks.update(new_tasks)
        for task in new_tasks.values():
            await self._emit(TaskChangeKind.ADDED, task)
        return list(new_tasks.values())

    async def update_deps(self, task_id: str, deps: list[str]) -> Task:
        """更新一个待启动任务的依赖（补前置）。

        Args:
            task_id: 目标任务。
            deps: 新的依赖列表。

        Returns:
            更新后的任务。

        Raises:
            TaskGraphError: 任务不存在、非待启动、依赖不存在或形成环。
        """
        task = self._require(task_id)
        if task.status is not TaskStatus.PENDING:
            raise TaskGraphError(f"仅待启动任务可修改依赖: {task_id}")
        if not all(isinstance(d, str) for d in deps):
            raise TaskGraphError("依赖必须是字符串列表")
        if task_id in deps:
            raise TaskGraphError(f"任务不能依赖自身: {task_id}")

        combined = dict(self._tasks)
        updated = Task(
            id=task.id,
            goal=task.goal,
            status=task.status,
            deps=list(deps),
            attempts=task.attempts,
            last_error=task.last_error,
            result=task.result,
        )
        combined[task_id] = updated
        self._assert_deps_exist(combined)
        self._assert_acyclic(combined)

        task.deps = list(deps)
        await self._emit(TaskChangeKind.UPDATED, task)
        return task

    async def start_task(self, task_id: str) -> Task:
        """启动一个任务（依赖须全部完成，且无其他进行中任务）。

        Raises:
            TaskGraphError: 任务不在待启动状态、已有进行中任务或依赖未完成。
        """
        task = self._require(task_id)
        if task.status is not TaskStatus.PENDING:
            raise TaskGraphError(f"任务不在待启动状态: {task_id}")
        if self.current_task_id() is not None:
            raise TaskGraphError("已有进行中的任务，请先完成或让位")
        unmet = [d for d in task.deps if not self._is_completed(d)]
        if unmet:
            raise TaskGraphError(f"依赖未完成: {', '.join(unmet)}")
        task.status = TaskStatus.IN_PROGRESS
        await self._emit(TaskChangeKind.STARTED, task)
        return task

    async def complete_task(self, task_id: str, result: str | None = None) -> Task:
        """完成一个进行中任务。

        Raises:
            TaskGraphError: 任务不存在或不在进行中。
        """
        task = self._require(task_id)
        if task.status is not TaskStatus.IN_PROGRESS:
            raise TaskGraphError(f"仅进行中任务可完成: {task_id}")
        task.status = TaskStatus.COMPLETED
        task.result = result
        await self._emit(TaskChangeKind.COMPLETED, task)
        return task

    async def reopen_task(self, task_id: str, error: str) -> Task:
        """将进行中任务因失败退回待启动，累计尝试次数并记录错误。

        Raises:
            TaskGraphError: 任务不存在或不在进行中。
        """
        task = self._require(task_id)
        if task.status is not TaskStatus.IN_PROGRESS:
            raise TaskGraphError(f"仅进行中任务可重试: {task_id}")
        task.status = TaskStatus.PENDING
        task.attempts += 1
        task.last_error = error
        await self._emit(TaskChangeKind.REOPENED, task)
        return task

    async def suspend_task(self, task_id: str) -> Task:
        """将进行中任务让位退回待启动，不计尝试次数。

        Raises:
            TaskGraphError: 任务不存在或不在进行中。
        """
        task = self._require(task_id)
        if task.status is not TaskStatus.IN_PROGRESS:
            raise TaskGraphError(f"仅进行中任务可让位: {task_id}")
        task.status = TaskStatus.PENDING
        await self._emit(TaskChangeKind.SUSPENDED, task)
        return task

    def _parse_spec(self, spec: dict[str, Any]) -> Task:
        task_id = spec.get("id")
        goal = spec.get("goal")
        deps = spec.get("deps", [])
        if not isinstance(task_id, str) or not task_id:
            raise TaskGraphError("任务 id 必须是非空字符串")
        if not isinstance(goal, str) or not goal:
            raise TaskGraphError(f"任务 {task_id} 的 goal 必须是非空字符串")
        if not isinstance(deps, list) or not all(isinstance(d, str) for d in deps):
            raise TaskGraphError(f"任务 {task_id} 的 deps 必须是字符串列表")
        return Task(id=task_id, goal=goal, deps=list(deps))

    def _require(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            raise TaskGraphError(f"任务不存在: {task_id}")
        return task

    def _is_completed(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        return task is not None and task.status is TaskStatus.COMPLETED

    def _deps_completed(self, task: Task) -> bool:
        return all(self._is_completed(d) for d in task.deps)

    def _assert_deps_exist(self, tasks: dict[str, Task]) -> None:
        for task in tasks.values():
            for dep in task.deps:
                if dep not in tasks:
                    raise TaskGraphError(f"依赖不存在: {dep}")

    def _assert_acyclic(self, tasks: dict[str, Task]) -> None:
        indegree = {task_id: 0 for task_id in tasks}
        adjacency: dict[str, list[str]] = {task_id: [] for task_id in tasks}
        for task in tasks.values():
            for dep in task.deps:
                adjacency[dep].append(task.id)
                indegree[task.id] += 1
        queue: deque[str] = deque(tid for tid, deg in indegree.items() if deg == 0)
        visited = 0
        while queue:
            node = queue.popleft()
            visited += 1
            for successor in adjacency[node]:
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    queue.append(successor)
        if visited != len(tasks):
            raise TaskGraphError("任务依赖图存在环")

    async def _emit(self, kind: TaskChangeKind, task: Task) -> None:
        if self._on_change is not None:
            await self._on_change(TaskChange(kind=kind, task=task))
