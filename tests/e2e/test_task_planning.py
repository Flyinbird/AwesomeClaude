"""端到端测试：Run 内任务计划的拆分、推进、轨迹与进度通知。"""

import asyncio
import json
from pathlib import Path
from typing import Any

from awesome_claude.client.transport.connection import ClientConnection
from awesome_claude.core.agent.loop import AgentLoop
from awesome_claude.core.config import ServerConfig
from awesome_claude.core.llm.events import DoneEvent, TextDeltaEvent, ToolUseEndEvent
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.core.router.dispatcher import create_dispatcher
from awesome_claude.core.server.tcp import TCPServer
from awesome_claude.core.session.channel import SessionChannel
from awesome_claude.core.session.registry import SessionRegistry
from awesome_claude.core.tools.builtin.plan import create_plan_tools
from awesome_claude.core.tools.registry import ToolRegistry
from awesome_claude.protocol.methods import (
    METHOD_CHAT,
    METHOD_SESSION_ATTACH,
    NOTIFY_CHAT_COMPLETED,
    NOTIFY_CHAT_PLAN_UPDATED,
)
from awesome_claude.shared.logging.trace_store import TraceStore
from awesome_claude.shared.types import TokenUsage
from tests.conftest import expect_chat_terminal


class QueuedLLM:
    """按调用次序返回事件序列的 fake LLM。"""

    def __init__(self, steps: list[list[Any]]) -> None:
        self._steps = [list(step) for step in steps]

    async def chat_stream(self, messages: list[dict], **kwargs: Any) -> Any:
        events = self._steps.pop(0) if self._steps else []
        for event in events:
            yield event


def _tool_use(block_id: str, name: str, tool_input: dict[str, Any]) -> DoneEvent:
    return DoneEvent(
        stop_reason="tool_use",
        full_text="",
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        message={
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": block_id,
                    "name": name,
                    "input": tool_input,
                }
            ],
        },
    )


def _final(text: str) -> DoneEvent:
    return DoneEvent(
        stop_reason="end_turn",
        full_text=text,
        usage=TokenUsage(input_tokens=1, output_tokens=1),
        message={"role": "assistant", "content": [{"type": "text", "text": text}]},
    )


async def _build_server(tmp_path: Path, llm: Any) -> tuple[TCPServer, Path]:
    runs_dir = tmp_path / "runs"
    trace_store = TraceStore(str(runs_dir))
    registry = SessionRegistry()
    tool_registry = ToolRegistry()
    for tool in create_plan_tools():
        tool_registry.register(tool)
    config = ServerConfig(api_key="k", model="m", host="127.0.0.1", port=0)

    def context_factory(channel: SessionChannel) -> HandlerContext:
        return HandlerContext(
            trace_store=trace_store,
            llm_client=llm,
            sessions=channel,
            config=config,
            agent_loop=AgentLoop(llm, tool_registry),
        )

    server = TCPServer(
        config.host, config.port, create_dispatcher(), context_factory, registry
    )
    await server.start()
    return server, runs_dir


def _collector(store: list[dict[str, Any]]) -> Any:
    async def handler(params: dict[str, Any]) -> None:
        store.append(params)

    return handler


def _read_trace(runs_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in runs_dir.glob("*/*.jsonl"):
        events.extend(
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        )
    return events


async def test_plan_declared_advanced_and_notified(tmp_path: Path) -> None:
    """模型拆分任务 → 推进状态 → 轨迹记录 + 进度通知。"""
    steps = [
        [
            ToolUseEndEvent(
                "t1", "add_tasks", {"tasks": [{"id": "a", "goal": "读取文件"}]}
            ),
            _tool_use("t1", "add_tasks", {"tasks": [{"id": "a", "goal": "读取文件"}]}),
        ],
        [
            ToolUseEndEvent("t2", "start_task", {"task_id": "a"}),
            _tool_use("t2", "start_task", {"task_id": "a"}),
        ],
        [
            ToolUseEndEvent(
                "t3", "complete_task", {"task_id": "a", "result": "已读取"}
            ),
            _tool_use("t3", "complete_task", {"task_id": "a", "result": "已读取"}),
        ],
        [TextDeltaEvent("完成"), _final("完成")],
    ]
    server, runs_dir = await _build_server(tmp_path, QueuedLLM(steps))
    try:
        conn = ClientConnection(*server.bound_addr)
        await conn.connect()
        try:
            await conn.send_request(METHOD_SESSION_ATTACH, {"session_id": "shared"})
            plans: list[dict[str, Any]] = []
            conn.on_notification(NOTIFY_CHAT_PLAN_UPDATED, _collector(plans))

            terminal_future = expect_chat_terminal(conn)
            ack = await conn.send_request(
                METHOD_CHAT, {"message": "请处理文件", "session_id": "shared"}
            )
            assert ack["result"]["accepted"] is True
            terminal = await asyncio.wait_for(terminal_future, timeout=2.0)
            assert terminal["method"] == NOTIFY_CHAT_COMPLETED
            assert terminal["params"]["text"] == "完成"

            # 每次任务变更各推送一次计划快照：added / started / completed
            assert len(plans) == 3
            assert [t["status"] for t in plans[0]["tasks"]] == ["pending"]
            assert [t["status"] for t in plans[1]["tasks"]] == ["in_progress"]
            assert [t["status"] for t in plans[2]["tasks"]] == ["completed"]
        finally:
            await conn.disconnect()
    finally:
        await server.stop()

    events = _read_trace(runs_dir)
    stages = [e["stage"] for e in events]
    assert "task_added" in stages
    assert "task_started" in stages
    assert "task_completed" in stages

    # 完成任务所在 step 的轮次事件应携带所属任务分组
    grouped = [
        e
        for e in events
        if e["stage"] == "step_started" and e["data"].get("task_id") == "a"
    ]
    assert grouped, "step 事件应携带进行中任务的 task_id"
