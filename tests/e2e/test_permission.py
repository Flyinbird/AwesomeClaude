"""端到端测试：工具权限拒绝不终止 Run，并记入轨迹。"""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from awesome_claude.client.transport.connection import ClientConnection
from awesome_claude.core.agent.loop import AgentLoop
from awesome_claude.core.config import ServerConfig
from awesome_claude.core.llm.events import DoneEvent, TextDeltaEvent, ToolUseEndEvent
from awesome_claude.core.permissions.policy import PermissionPolicy
from awesome_claude.core.permissions.types import PermissionDecision
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.core.router.dispatcher import create_dispatcher
from awesome_claude.core.server.tcp import TCPServer
from awesome_claude.core.session.channel import SessionChannel
from awesome_claude.core.session.registry import SessionRegistry
from awesome_claude.core.tools.base import Tool
from awesome_claude.core.tools.context import ToolContext, ToolScope
from awesome_claude.core.tools.registry import ToolRegistry
from awesome_claude.protocol.methods import METHOD_CHAT, NOTIFY_CHAT_COMPLETED
from awesome_claude.shared.logging.trace_store import TraceStore
from awesome_claude.shared.types import TokenUsage
from tests.conftest import expect_chat_terminal


class RoundsLLM:
    """按预设轮次返回事件的 fake LLM。"""

    def __init__(self, rounds: list[list[Any]]) -> None:
        self._rounds = rounds
        self._index = 0

    async def chat_stream(
        self, messages: list[dict], **kwargs: Any
    ) -> AsyncIterator[Any]:
        events = self._rounds[self._index]
        self._index += 1
        for event in events:
            yield event


def _tool_round(tool_id: str, name: str, args: dict[str, Any]) -> list[Any]:
    """构造一轮「请求工具」的事件序列。"""
    return [
        ToolUseEndEvent(tool_id, name, args),
        DoneEvent(
            stop_reason="tool_use",
            full_text="",
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            message={
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": tool_id, "name": name, "input": args}
                ],
            },
        ),
    ]


def _text_round(text: str) -> list[Any]:
    """构造一轮「纯文本结尾」的事件序列。"""
    return [
        TextDeltaEvent(text),
        DoneEvent(
            stop_reason="end_turn",
            full_text=text,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
            message={
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
        ),
    ]


async def _secret(
    args: dict[str, Any], ctx: ToolContext, scope: ToolScope | None
) -> str:
    return "classified"


async def _build_server(
    tmp_path: Path, llm: Any, policy: PermissionPolicy
) -> tuple[TCPServer, Path]:
    """构建带权限策略的 TCPServer。"""
    runs_dir = tmp_path / "logs" / "runs"
    trace_store = TraceStore(str(runs_dir))
    registry = ToolRegistry()
    registry.register(
        Tool(name="secret", description="d", input_schema={}, handler=_secret)
    )
    config = ServerConfig(api_key="k", model="m", host="127.0.0.1", port=0)

    def context_factory(channel: SessionChannel) -> HandlerContext:
        return HandlerContext(
            trace_store=trace_store,
            llm_client=llm,
            sessions=channel,
            config=config,
            agent_loop=AgentLoop(llm, registry),
            permission_policy=policy,
        )

    server = TCPServer(
        config.host,
        config.port,
        create_dispatcher(),
        context_factory,
        SessionRegistry(),
    )
    await server.start()
    return server, runs_dir


async def test_denied_tool_does_not_stop_run(tmp_path: Path) -> None:
    """被拒工具回填错误结果，Run 继续并正常完成，轨迹含 permission_denied。"""
    llm = RoundsLLM(
        [
            _tool_round("t1", "secret", {}),
            _text_round("finished"),
        ]
    )
    policy = PermissionPolicy(global_default=PermissionDecision.DENY)
    server, runs_dir = await _build_server(tmp_path, llm, policy)
    run_id = ""
    try:
        conn = ClientConnection(*server.bound_addr)
        await conn.connect()
        try:
            terminal_future = expect_chat_terminal(conn)
            ack = await conn.send_request(METHOD_CHAT, {"message": "reveal"})
            run_id = ack["result"]["run_id"]
            terminal = await asyncio.wait_for(terminal_future, timeout=2.0)
            assert terminal["method"] == NOTIFY_CHAT_COMPLETED
            assert terminal["params"]["text"] == "finished"
        finally:
            await conn.disconnect()
    finally:
        await server.stop()

    events: list[dict[str, Any]] = []
    for path in runs_dir.glob("*/*.jsonl"):
        events.extend(
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        )
    stages = [event["stage"] for event in events]
    assert "permission_requested" in stages
    assert "permission_denied" in stages
    denied = [e for e in events if e["stage"] == "permission_denied"]
    assert denied[0]["data"]["tool"] == "secret"
    assert denied[0]["run_id"] == run_id

    failed = [e for e in events if e["stage"] == "tool_failed"]
    assert failed and failed[0]["data"]["is_error"] is True
