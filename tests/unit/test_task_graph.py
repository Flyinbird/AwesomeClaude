"""core/task TaskGraph 领域测试。"""

import pytest

from awesome_claude.core.task.graph import (
    TaskChange,
    TaskChangeKind,
    TaskGraph,
    TaskGraphError,
)
from awesome_claude.core.task.task import TaskStatus


def _observer_sink() -> tuple[list[TaskChange], object]:
    changes: list[TaskChange] = []

    async def on_change(change: TaskChange) -> None:
        changes.append(change)

    return changes, on_change


class TestTaskGraphMutations:
    """任务图变更与校验测试。"""

    async def test_add_tasks_creates_pending(self) -> None:
        graph = TaskGraph("run1")
        tasks = await graph.add_tasks(
            [
                {"id": "a", "goal": "读文件"},
                {"id": "b", "goal": "写文件", "deps": ["a"]},
            ]
        )
        assert [t.id for t in tasks] == ["a", "b"]
        assert graph.get("a").status is TaskStatus.PENDING
        assert graph.get("b").deps == ["a"]
        assert graph.ready() == ["a"]

    async def test_duplicate_id_rejected(self) -> None:
        graph = TaskGraph("run1")
        await graph.add_tasks([{"id": "a", "goal": "x"}])
        with pytest.raises(TaskGraphError):
            await graph.add_tasks([{"id": "a", "goal": "y"}])

    async def test_missing_dependency_rejected(self) -> None:
        graph = TaskGraph("run1")
        with pytest.raises(TaskGraphError):
            await graph.add_tasks([{"id": "a", "goal": "x", "deps": ["ghost"]}])

    async def test_cycle_rejected(self) -> None:
        graph = TaskGraph("run1")
        with pytest.raises(TaskGraphError):
            await graph.add_tasks(
                [
                    {"id": "a", "goal": "x", "deps": ["b"]},
                    {"id": "b", "goal": "y", "deps": ["a"]},
                ]
            )

    async def test_update_deps_on_pending(self) -> None:
        graph = TaskGraph("run1")
        await graph.add_tasks([{"id": "a", "goal": "x"}, {"id": "b", "goal": "y"}])
        await graph.update_deps("b", ["a"])
        assert graph.get("b").deps == ["a"]
        assert graph.ready() == ["a"]

    async def test_update_deps_cycle_rejected(self) -> None:
        graph = TaskGraph("run1")
        await graph.add_tasks([{"id": "a", "goal": "x"}, {"id": "b", "goal": "y"}])
        with pytest.raises(TaskGraphError):
            await graph.update_deps("a", ["b"])
            await graph.update_deps("b", ["a"])

    async def test_update_deps_self_rejected(self) -> None:
        graph = TaskGraph("run1")
        await graph.add_tasks([{"id": "a", "goal": "x"}])
        with pytest.raises(TaskGraphError):
            await graph.update_deps("a", ["a"])

    async def test_update_deps_on_completed_rejected(self) -> None:
        graph = TaskGraph("run1")
        await graph.add_tasks([{"id": "a", "goal": "x"}])
        await graph.start_task("a")
        await graph.complete_task("a")
        with pytest.raises(TaskGraphError):
            await graph.update_deps("a", [])

    async def test_observer_receives_kinds(self) -> None:
        changes, on_change = _observer_sink()
        graph = TaskGraph("run1", on_change=on_change)
        await graph.add_tasks([{"id": "a", "goal": "x"}])
        await graph.start_task("a")
        await graph.reopen_task("a", "boom")
        await graph.start_task("a")
        await graph.suspend_task("a")
        await graph.start_task("a")
        await graph.complete_task("a")
        assert [c.kind for c in changes] == [
            TaskChangeKind.ADDED,
            TaskChangeKind.STARTED,
            TaskChangeKind.REOPENED,
            TaskChangeKind.STARTED,
            TaskChangeKind.SUSPENDED,
            TaskChangeKind.STARTED,
            TaskChangeKind.COMPLETED,
        ]

    async def test_invalid_change_does_not_notify(self) -> None:
        changes, on_change = _observer_sink()
        graph = TaskGraph("run1", on_change=on_change)
        await graph.add_tasks([{"id": "a", "goal": "x"}, {"id": "b", "goal": "y"}])
        await graph.start_task("a")
        with pytest.raises(TaskGraphError):
            await graph.start_task("b")
        assert [c.kind for c in changes] == [
            TaskChangeKind.ADDED,
            TaskChangeKind.ADDED,
            TaskChangeKind.STARTED,
        ]


class TestTaskStateMachine:
    """任务状态机与失败/让位语义测试。"""

    async def test_start_requires_deps_completed(self) -> None:
        graph = TaskGraph("run1")
        await graph.add_tasks(
            [{"id": "a", "goal": "x"}, {"id": "b", "goal": "y", "deps": ["a"]}]
        )
        with pytest.raises(TaskGraphError):
            await graph.start_task("b")

    async def test_at_most_one_in_progress(self) -> None:
        graph = TaskGraph("run1")
        await graph.add_tasks([{"id": "a", "goal": "x"}, {"id": "b", "goal": "y"}])
        await graph.start_task("a")
        assert graph.current_task_id() == "a"
        with pytest.raises(TaskGraphError):
            await graph.start_task("b")

    async def test_reopen_increments_attempts(self) -> None:
        graph = TaskGraph("run1")
        await graph.add_tasks([{"id": "a", "goal": "x"}])
        await graph.start_task("a")
        task = await graph.reopen_task("a", "boom")
        assert task.status is TaskStatus.PENDING
        assert task.attempts == 1
        assert task.last_error == "boom"

    async def test_suspend_does_not_increment_attempts(self) -> None:
        graph = TaskGraph("run1")
        await graph.add_tasks([{"id": "a", "goal": "x"}])
        await graph.start_task("a")
        task = await graph.suspend_task("a")
        assert task.status is TaskStatus.PENDING
        assert task.attempts == 0
        assert task.last_error is None

    async def test_complete_requires_in_progress(self) -> None:
        graph = TaskGraph("run1")
        await graph.add_tasks([{"id": "a", "goal": "x"}])
        with pytest.raises(TaskGraphError):
            await graph.complete_task("a")

    async def test_suspend_then_start_other(self) -> None:
        graph = TaskGraph("run1")
        await graph.add_tasks([{"id": "a", "goal": "x"}, {"id": "b", "goal": "y"}])
        await graph.start_task("a")
        await graph.suspend_task("a")
        await graph.start_task("b")
        assert graph.current_task_id() == "b"

    async def test_snapshot_shape(self) -> None:
        graph = TaskGraph("run1")
        await graph.add_tasks([{"id": "a", "goal": "x"}])
        [snap] = graph.snapshot()
        assert snap == {
            "id": "a",
            "goal": "x",
            "status": "pending",
            "deps": [],
            "attempts": 0,
        }
