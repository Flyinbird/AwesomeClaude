"""core/agent AgentLoop 多轮编排测试。"""

from typing import Any

from awesome_claude.core.agent.events import (
    StepFinished,
    StepStarted,
    ToolFinished,
    ToolStarted,
)
from awesome_claude.core.agent.loop import AgentLoop
from awesome_claude.core.llm.events import (
    DoneEvent,
    LLMStreamEvent,
    TextDeltaEvent,
    ToolUseEndEvent,
)
from awesome_claude.core.tools.base import Tool
from awesome_claude.core.tools.registry import ToolRegistry
from awesome_claude.shared.types import StopReason, TokenUsage


def _done(
    text: str,
    *,
    stop_reason: str = "end_turn",
    content: list[dict[str, Any]] | None = None,
    input_tokens: int = 1,
    output_tokens: int = 1,
) -> DoneEvent:
    """构造 DoneEvent，content 缺省为单个文本块。"""
    return DoneEvent(
        stop_reason=StopReason.from_raw(stop_reason),
        full_text=text,
        usage=TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens),
        message={
            "role": "assistant",
            "content": content or [{"type": "text", "text": text}],
        },
    )


class FakeLLM:
    """按脚本依次返回每轮事件的 fake LLM。"""

    def __init__(self, rounds: list[list[LLMStreamEvent]]) -> None:
        self._rounds = list(rounds)
        self._idx = 0

    async def chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        events = self._rounds[self._idx]
        self._idx += 1
        for event in events:
            yield event


class AlwaysToolLLM:
    """每轮都请求工具；当 tools=None（收尾轮）时返回最终文本。"""

    def __init__(self, tool_name: str = "noop") -> None:
        self._tool_name = tool_name
        self.tool_calls = 0

    async def chat_stream(self, messages: list[dict[str, Any]], **kwargs: Any) -> Any:
        if kwargs.get("tools") is None:
            yield TextDeltaEvent("wrapped up")
            yield _done("wrapped up", stop_reason="end_turn")
            return
        self.tool_calls += 1
        yield ToolUseEndEvent(f"t{self.tool_calls}", self._tool_name, {})
        yield _done(
            "",
            stop_reason="tool_use",
            content=[
                {
                    "type": "tool_use",
                    "id": f"t{self.tool_calls}",
                    "name": self._tool_name,
                    "input": {},
                }
            ],
        )


async def _noop(args: dict[str, Any], ctx: Any) -> str:
    return "ok"


class TestAgentLoop:
    """AgentLoop 编排测试。"""

    async def test_single_turn_no_tools(self) -> None:
        llm = FakeLLM(
            [[TextDeltaEvent("hi"), _done("hi", input_tokens=3, output_tokens=2)]]
        )
        loop = AgentLoop(llm, ToolRegistry())
        result = await loop.run("hello")

        assert result.text == "hi"
        assert result.steps == 1
        assert result.stop_reason == "end_turn"
        assert result.usage.input_tokens == 3
        assert result.usage.output_tokens == 2
        assert result.messages[0] == {"role": "user", "content": "hello"}
        assert result.messages[1]["role"] == "assistant"

    async def test_multi_turn_with_tool(self) -> None:
        calls: list[dict[str, Any]] = []

        async def add(args: dict[str, Any], ctx: Any) -> str:
            calls.append(args)
            return str(args["a"] + args["b"])

        registry = ToolRegistry()
        registry.register(
            Tool(
                name="add",
                description="两数相加",
                input_schema={"type": "object"},
                handler=add,
            )
        )

        llm = FakeLLM(
            [
                [
                    ToolUseEndEvent("t1", "add", {"a": 1, "b": 2}),
                    _done(
                        "",
                        stop_reason="tool_use",
                        content=[
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "add",
                                "input": {"a": 1, "b": 2},
                            }
                        ],
                    ),
                ],
                [
                    TextDeltaEvent("3"),
                    _done("3", input_tokens=5, output_tokens=4),
                ],
            ]
        )
        loop = AgentLoop(llm, registry)
        result = await loop.run("1+2")

        assert result.text == "3"
        assert result.steps == 2
        assert calls == [{"a": 1, "b": 2}]
        assert result.usage.input_tokens == 6
        assert result.usage.output_tokens == 5

        tool_result_msg = result.messages[-2]
        assert tool_result_msg["role"] == "user"
        assert tool_result_msg["content"] == [
            {
                "type": "tool_result",
                "tool_use_id": "t1",
                "content": "3",
                "is_error": False,
            }
        ]

    async def test_tool_error_is_fed_back_to_llm(self) -> None:
        async def boom(args: dict[str, Any], ctx: Any) -> str:
            raise RuntimeError("boom")

        registry = ToolRegistry()
        registry.register(
            Tool(name="boom", description="d", input_schema={}, handler=boom)
        )

        llm = FakeLLM(
            [
                [
                    ToolUseEndEvent("t1", "boom", {}),
                    _done(
                        "",
                        stop_reason="tool_use",
                        content=[
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "boom",
                                "input": {},
                            }
                        ],
                    ),
                ],
                [TextDeltaEvent("ok"), _done("ok")],
            ]
        )
        loop = AgentLoop(llm, registry)
        result = await loop.run("x")

        assert result.text == "ok"
        tool_result = result.messages[-2]["content"][0]
        assert tool_result["is_error"] is True
        assert "RuntimeError" in tool_result["content"]

    async def test_on_event_receives_all_events(self) -> None:
        llm = FakeLLM([[TextDeltaEvent("a"), _done("a")]])
        loop = AgentLoop(llm, ToolRegistry())
        received: list[str] = []

        async def on_event(event: LLMStreamEvent) -> None:
            received.append(type(event).__name__)

        await loop.run("x", on_event=on_event)
        assert received == ["TextDeltaEvent", "DoneEvent"]

    async def test_history_is_prepended(self) -> None:
        llm = FakeLLM([[TextDeltaEvent("b"), _done("b")]])
        loop = AgentLoop(llm, ToolRegistry())
        history = [{"role": "user", "content": "previous"}]
        result = await loop.run("now", history=history)

        assert result.messages[0] == {"role": "user", "content": "previous"}
        assert result.messages[1] == {"role": "user", "content": "now"}

    async def test_on_step_reports_step_and_tool_events(self) -> None:
        async def add(args: dict[str, Any], ctx: Any) -> str:
            return str(args["a"] + args["b"])

        registry = ToolRegistry()
        registry.register(
            Tool(
                name="add",
                description="两数相加",
                input_schema={"type": "object"},
                handler=add,
            )
        )

        llm = FakeLLM(
            [
                [
                    ToolUseEndEvent("t1", "add", {"a": 1, "b": 2}),
                    _done(
                        "",
                        stop_reason="tool_use",
                        content=[
                            {
                                "type": "tool_use",
                                "id": "t1",
                                "name": "add",
                                "input": {"a": 1, "b": 2},
                            }
                        ],
                    ),
                ],
                [TextDeltaEvent("3"), _done("3")],
            ]
        )
        loop = AgentLoop(llm, registry)
        events: list[Any] = []

        async def on_step(event: Any) -> None:
            events.append(event)

        await loop.run("1+2", on_step=on_step)

        assert [type(e).__name__ for e in events] == [
            "StepStarted",
            "StepFinished",
            "ToolStarted",
            "ToolFinished",
            "StepStarted",
            "StepFinished",
        ]
        assert isinstance(events[0], StepStarted)
        assert events[0].step_index == 1
        assert isinstance(events[1], StepFinished)
        assert events[1].step_index == 1
        assert events[1].has_tool_calls is True
        assert events[1].stop_reason == "tool_use"
        assert isinstance(events[2], ToolStarted)
        assert events[2].tool_name == "add"
        assert events[2].args == {"a": 1, "b": 2}
        assert isinstance(events[3], ToolFinished)
        assert events[3].is_error is False
        assert events[3].content == "3"
        assert isinstance(events[4], StepStarted)
        assert events[4].step_index == 2
        assert isinstance(events[5], StepFinished)
        assert events[5].has_tool_calls is False

    async def test_max_steps_truncated_with_finalize(self) -> None:
        registry = ToolRegistry()
        registry.register(
            Tool(name="noop", description="d", input_schema={}, handler=_noop)
        )
        llm = AlwaysToolLLM()
        loop = AgentLoop(llm, registry, max_steps=3)

        result = await loop.run("x")

        assert result.steps == 4
        assert result.stop_reason == StopReason.MAX_STEPS
        assert result.text == "wrapped up"
        assert llm.tool_calls == 3

    async def test_max_steps_truncated_without_finalize(self) -> None:
        registry = ToolRegistry()
        registry.register(
            Tool(name="noop", description="d", input_schema={}, handler=_noop)
        )
        llm = AlwaysToolLLM()
        loop = AgentLoop(llm, registry, max_steps=3, finalize=False)

        result = await loop.run("x")

        assert result.steps == 3
        assert result.stop_reason == StopReason.MAX_STEPS
        assert result.text == ""
        assert llm.tool_calls == 3
