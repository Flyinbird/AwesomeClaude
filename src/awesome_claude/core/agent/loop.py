"""Agent 循环 - 编排多轮 LLM 调用与工具执行。"""

from collections.abc import Awaitable, Callable
from typing import Any

from awesome_claude.core.agent.events import (
    AgentEvent,
    StepFinished,
    StepStarted,
    ToolFinished,
    ToolStarted,
)
from awesome_claude.core.agent.result import AgentResult
from awesome_claude.core.llm.client import LLMClient
from awesome_claude.core.llm.events import (
    DoneEvent,
    LLMStreamEvent,
    TextDeltaEvent,
    ToolUseEndEvent,
)
from awesome_claude.core.tools.registry import ToolRegistry
from awesome_claude.shared.types import TokenUsage

type EventHandler = Callable[[LLMStreamEvent], Awaitable[None]]
type StepHandler = Callable[[AgentEvent], Awaitable[None]]


class AgentLoop:
    """Agent 循环编排器。

    多轮调用 LLM：每轮收集文本与工具调用；若有工具调用则逐个执行并将
    tool_result 回填到消息列表，继续下一轮，直至模型不再调用工具或达到
    max_steps 上限。

    AgentLoop 不直接触碰网络/通知：LLM 流式事件通过 on_event 回调透出，
    结构事件（step/tool 生命周期）通过 on_step 回调透出，便于上层
    （handler）转换为通知、任务日志或测试替身。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tools: ToolRegistry,
        *,
        max_steps: int = 25,
    ) -> None:
        """初始化 AgentLoop。

        Args:
            llm_client: LLM 客户端。
            tools: 工具注册表。
            max_steps: 最大循环步数（每次 LLM 调用计一步）。
        """
        self._llm = llm_client
        self._tools = tools
        self._max_steps = max_steps

    async def run(
        self,
        user_input: str,
        *,
        system: str | None = None,
        history: list[dict[str, Any]] | None = None,
        on_event: EventHandler | None = None,
        on_step: StepHandler | None = None,
    ) -> AgentResult:
        """运行 agent 循环。

        Args:
            user_input: 用户输入。
            system: 系统提示（可选）。
            history: 前置对话历史（可选，不含本轮 user 消息）。
            on_event: LLM 流式事件回调（可选）。
            on_step: step/tool 结构事件回调（可选）。

        Returns:
            最终结果（文本、完整消息历史、累计用量、步数、停止原因）。
        """
        messages: list[dict[str, Any]] = list(history) if history else []
        messages.append({"role": "user", "content": user_input})

        final_text = ""
        stop_reason = ""
        total_input = 0
        total_output = 0
        steps = 0

        for _ in range(self._max_steps):
            steps += 1
            if on_step is not None:
                await on_step(StepStarted(step_index=steps))

            text_parts: list[str] = []
            tool_uses: list[dict[str, Any]] = []
            last_message: dict[str, Any] | None = None
            step_input = 0
            step_output = 0
            step_stop_reason = ""

            tools_param = self._tools.to_anthropic_tools() or None
            async for event in self._llm.chat_stream(
                messages, system=system, tools=tools_param
            ):
                if on_event is not None:
                    await on_event(event)
                if isinstance(event, TextDeltaEvent):
                    text_parts.append(event.text)
                elif isinstance(event, ToolUseEndEvent):
                    tool_uses.append(
                        {
                            "id": event.block_id,
                            "name": event.name,
                            "input": event.input,
                        }
                    )
                elif isinstance(event, DoneEvent):
                    last_message = event.message
                    step_stop_reason = event.stop_reason
                    step_input = event.usage.input_tokens
                    step_output = event.usage.output_tokens

            if last_message is not None:
                messages.append(last_message)
                stop_reason = step_stop_reason
                total_input += step_input
                total_output += step_output

            has_tool_calls = bool(tool_uses)
            if on_step is not None:
                await on_step(
                    StepFinished(
                        step_index=steps,
                        stop_reason=step_stop_reason,
                        input_tokens=step_input,
                        output_tokens=step_output,
                        has_tool_calls=has_tool_calls,
                    )
                )

            if not tool_uses:
                final_text = "".join(text_parts)
                break

            tool_results: list[dict[str, Any]] = []
            for tool_use in tool_uses:
                if on_step is not None:
                    await on_step(
                        ToolStarted(
                            step_index=steps,
                            tool_name=tool_use["name"],
                            args=tool_use["input"],
                        )
                    )
                result = await self._tools.execute(tool_use["name"], tool_use["input"])
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use["id"],
                        "content": result.content,
                        "is_error": result.is_error,
                    }
                )
                if on_step is not None:
                    await on_step(
                        ToolFinished(
                            step_index=steps,
                            tool_name=tool_use["name"],
                            is_error=result.is_error,
                            content=result.content,
                        )
                    )
            messages.append({"role": "user", "content": tool_results})

        return AgentResult(
            text=final_text,
            messages=messages,
            usage=TokenUsage(input_tokens=total_input, output_tokens=total_output),
            steps=steps,
            stop_reason=stop_reason,
        )
