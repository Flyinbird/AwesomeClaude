"""core/session/run.py Run 状态机测试。"""

import asyncio

from awesome_claude.core.session.run import Run, RunInitiator, RunState


async def _never() -> None:
    await asyncio.Event().wait()


class TestRunStateMachine:
    """Run 状态迁移测试。"""

    def test_initial_running(self) -> None:
        run = Run("r1", "s1", 1.0)
        assert run.run_id == "r1"
        assert run.session_id == "s1"
        assert run.state is RunState.RUNNING
        assert run.is_active
        assert not run.is_terminal
        assert run.task is None

    def test_finish_completed(self) -> None:
        run = Run("r1", "s1", 1.0)
        assert run.finish(RunState.COMPLETED) is True
        assert run.state is RunState.COMPLETED
        assert not run.is_active
        assert run.is_terminal

    def test_finish_failed(self) -> None:
        run = Run("r1", "s1", 1.0)
        assert run.finish(RunState.FAILED) is True
        assert run.state is RunState.FAILED

    def test_finish_interrupted(self) -> None:
        run = Run("r1", "s1", 1.0)
        assert run.finish(RunState.INTERRUPTED) is True
        assert run.state is RunState.INTERRUPTED

    def test_finish_terminal_is_noop(self) -> None:
        run = Run("r1", "s1", 1.0)
        assert run.finish(RunState.COMPLETED) is True
        assert run.finish(RunState.FAILED) is False
        assert run.finish(RunState.CANCELLED) is False
        assert run.state is RunState.COMPLETED

    def test_initiator_default_none(self) -> None:
        run = Run("r1", "s1", 1.0)
        assert run.initiator is None

    def test_initiator_recorded(self) -> None:
        run = Run("r1", "s1", 1.0, initiator=RunInitiator(request_id=5))
        assert run.initiator is not None
        assert run.initiator.request_id == 5


class TestRunCancel:
    """Run 取消语义测试。"""

    async def test_cancel_running_task(self) -> None:
        run = Run("r1", "s1", 1.0)
        task = asyncio.create_task(_never())
        run.set_task(task)
        assert run.cancel() is True
        assert run.state is RunState.CANCELLED
        assert run.is_terminal
        await asyncio.gather(task, return_exceptions=True)
        assert task.cancelled()

    async def test_cancel_terminal_is_noop(self) -> None:
        run = Run("r1", "s1", 1.0)
        task = asyncio.create_task(_never())
        run.set_task(task)
        run.finish(RunState.COMPLETED)
        assert run.cancel() is False
        assert run.state is RunState.COMPLETED
        assert not task.cancelled()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def test_cancel_without_task_is_noop(self) -> None:
        run = Run("r1", "s1", 1.0)
        assert run.cancel() is False
        assert run.is_active

    async def test_repeated_cancel_is_noop(self) -> None:
        run = Run("r1", "s1", 1.0)
        task = asyncio.create_task(_never())
        run.set_task(task)
        assert run.cancel() is True
        assert run.cancel() is False
        await asyncio.gather(task, return_exceptions=True)
