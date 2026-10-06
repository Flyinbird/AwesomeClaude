"""permission.respond handler 单元测试。"""

import asyncio

import pytest

from awesome_claude.core.handlers.permission import handle_permission_respond
from awesome_claude.core.permissions.broker import InteractiveBroker, _PendingItem
from awesome_claude.core.permissions.types import PermissionDecision
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.core.session.registry import SessionRegistry
from awesome_claude.shared.logging.trace_store import TraceStore


class TestHandlePermissionRespond:
    """handle_permission_respond 的单元测试。"""

    @pytest.fixture
    def broker(self) -> InteractiveBroker:
        registry = SessionRegistry()
        return InteractiveBroker(registry, timeout=60)

    @pytest.fixture
    def context(self, broker: InteractiveBroker) -> HandlerContext:
        return HandlerContext(
            trace_store=TraceStore("/tmp"),
            llm_client=None,  # type: ignore
            sessions=None,  # type: ignore
            config=None,  # type: ignore
            permission_broker=broker,
        )

    async def test_valid_allow(
        self, context: HandlerContext, broker: InteractiveBroker
    ) -> None:
        """有效 allow 响应返回 ok。"""
        future = broker._pending.setdefault(
            "req1",
            _PendingItem(
                future=asyncio.get_running_loop().create_future(),
                run_id="run1",
                request_id="req1",
            ),
        )
        result = await handle_permission_respond(
            {"request_id": "req1", "decision": "allow"}, context
        )
        assert result == {"ok": True}
        outcome = await future.future
        assert outcome.decision is PermissionDecision.ALLOW

    async def test_valid_deny(
        self, context: HandlerContext, broker: InteractiveBroker
    ) -> None:
        """有效 deny 响应返回 ok。"""
        future = broker._pending.setdefault(
            "req1",
            _PendingItem(
                future=asyncio.get_running_loop().create_future(),
                run_id="run1",
                request_id="req1",
            ),
        )
        result = await handle_permission_respond(
            {"request_id": "req1", "decision": "deny"}, context
        )
        assert result == {"ok": True}
        outcome = await future.future
        assert outcome.decision is PermissionDecision.DENY

    async def test_invalid_request_id(self, context: HandlerContext) -> None:
        """不存在的 request_id 返回错误。"""
        result = await handle_permission_respond(
            {"request_id": "nosuch", "decision": "allow"}, context
        )
        assert "error" in result
        assert "已过期或不存在" in result["error"]["message"]

    async def test_missing_params(self, context: HandlerContext) -> None:
        """缺少参数返回错误。"""
        result = await handle_permission_respond(None, context)
        assert "error" in result

    async def test_invalid_decision(self, context: HandlerContext) -> None:
        """decision 非法返回错误。"""
        result = await handle_permission_respond(
            {"request_id": "req1", "decision": "maybe"}, context
        )
        assert "error" in result

    async def test_no_broker(self) -> None:
        """未启用交互式审批通道返回错误。"""
        context = HandlerContext(
            trace_store=TraceStore("/tmp"),
            llm_client=None,  # type: ignore
            sessions=None,  # type: ignore
            config=None,  # type: ignore
            permission_broker=None,
        )
        result = await handle_permission_respond(
            {"request_id": "req1", "decision": "allow"}, context
        )
        assert "error" in result
        assert "未启用" in result["error"]["message"]
