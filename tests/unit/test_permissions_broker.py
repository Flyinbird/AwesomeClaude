"""InteractiveBroker 单元测试。"""

import asyncio
from typing import Any

import pytest

from awesome_claude.core.permissions.broker import InteractiveBroker, _PendingItem
from awesome_claude.core.permissions.types import (
    PermissionDecision,
    PermissionRequest,
)
from awesome_claude.core.session.registry import SessionRegistry


class TestInteractiveBroker:
    """InteractiveBroker 的 ask / respond / timeout / cancel 场景。"""

    @pytest.fixture
    def registry(self) -> SessionRegistry:
        return SessionRegistry()

    @pytest.fixture
    def broker(self, registry: SessionRegistry) -> InteractiveBroker:
        return InteractiveBroker(registry, timeout=1)

    @pytest.fixture
    def request_sample(self) -> PermissionRequest:
        return PermissionRequest(
            tool="read_file",
            args={"path": "/tmp/test.txt"},
            run_id="run123",
            step_index=1,
            action="读取文件 /tmp/test.txt",
            resources=("path:/tmp/test.txt",),
        )

    async def test_ask_respond_allow(
        self,
        broker: InteractiveBroker,
        request_sample: PermissionRequest,
    ) -> None:
        """正常 ask + respond allow → 允许。"""

        async def responder() -> None:
            await asyncio.sleep(0.05)
            ok = broker.respond("req1", PermissionDecision.ALLOW)
            assert ok is True

        # 手动注册 pending 以便测试（绕过 ask 中的广播）
        future = asyncio.get_running_loop().create_future()
        broker._pending["req1"] = _PendingItem(
            future=future, run_id="run123", request_id="req1"
        )

        task = asyncio.create_task(responder())
        outcome = await future
        await task
        assert outcome.decision is PermissionDecision.ALLOW
        assert outcome.reason == "用户允许"

    async def test_ask_respond_deny(
        self,
        broker: InteractiveBroker,
        request_sample: PermissionRequest,
    ) -> None:
        """正常 ask + respond deny → 拒绝。"""
        future = asyncio.get_running_loop().create_future()
        broker._pending["req1"] = _PendingItem(
            future=future, run_id="run123", request_id="req1"
        )

        async def responder() -> None:
            await asyncio.sleep(0.05)
            broker.respond("req1", PermissionDecision.DENY)

        task = asyncio.create_task(responder())
        outcome = await future
        await task
        assert outcome.decision is PermissionDecision.DENY
        assert outcome.reason == "用户拒绝"

    async def test_ask_timeout(
        self,
        broker: InteractiveBroker,
        request_sample: PermissionRequest,
    ) -> None:
        """无人响应 → 超时按拒绝处理。"""
        outcome = await broker.ask(request_sample)
        assert outcome.decision is PermissionDecision.DENY
        assert "超时" in outcome.reason

    async def test_cancel_for_run(
        self,
        broker: InteractiveBroker,
        request_sample: PermissionRequest,
    ) -> None:
        """Run 取消时清理 pending 并按拒绝处理。"""
        future = asyncio.get_running_loop().create_future()
        broker._pending["req1"] = _PendingItem(
            future=future, run_id="run123", request_id="req1"
        )

        broker.cancel_for_run("run123")
        outcome = await future
        assert outcome.decision is PermissionDecision.DENY
        assert "Run 已取消" in outcome.reason

    async def test_respond_unknown_request_id(self, broker: InteractiveBroker) -> None:
        """响应不存在的 request_id → 返回 False。"""
        ok = broker.respond("nosuch", PermissionDecision.ALLOW)
        assert ok is False

    async def test_respond_duplicate_ignored(
        self,
        broker: InteractiveBroker,
        request_sample: PermissionRequest,
    ) -> None:
        """重复响应 → 首个有效，后续被忽略。"""
        future = asyncio.get_running_loop().create_future()
        broker._pending["req1"] = _PendingItem(
            future=future, run_id="run123", request_id="req1"
        )

        ok1 = broker.respond("req1", PermissionDecision.ALLOW)
        ok2 = broker.respond("req1", PermissionDecision.DENY)
        assert ok1 is True
        assert ok2 is False
        outcome = await future
        assert outcome.decision is PermissionDecision.ALLOW

    async def test_ask_broadcasts_to_session(
        self,
        registry: SessionRegistry,
        request_sample: PermissionRequest,
    ) -> None:
        """ask 时向会话订阅者广播通知。"""
        received: list[dict[str, Any]] = []

        async def fake_send(method: str, params: dict[str, Any]) -> None:
            if method == "chat.permission_requested":
                received.append(params)

        session = registry.get_or_create("sess1")
        from awesome_claude.core.session.registry import ConnectionSink

        sink = ConnectionSink(fake_send)
        session.sinks.add(sink)
        session.active_run = type("Run", (), {"run_id": "run123", "is_active": True})()  # type: ignore

        broker = InteractiveBroker(registry, timeout=1)
        # 不等待超时，直接验证广播已发出
        task = asyncio.create_task(broker.ask(request_sample))
        await asyncio.sleep(0.05)
        assert len(received) == 1
        assert received[0]["request_id"]
        assert received[0]["tool_name"] == "read_file"
        broker.cancel_for_run("run123")
        outcome = await task
        assert outcome.decision is PermissionDecision.DENY
