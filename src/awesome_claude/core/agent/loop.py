"""Agent 循环 - 编排多轮 LLM 调用与工具执行。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from awesome_claude.core.agent.events import (
    AgentEvent,
    StepFinished,
    StepStarted,
    ToolFinished,
    ToolStarted,
)
from awesome_claude.core.agent.result import AgentResult
from awesome_claude.core.llm.base import LLMProvider
from awesome_claude.core.llm.events import (
    DoneEvent,
    LLMStreamEvent,
    TextDeltaEvent,
    ToolUseEndEvent,
)
from awesome_claude.core.tools.registry import ToolRegistry
from awesome_claude.shared.types import StopReason, TokenUsage

type EventHandler = Callable[[LLMStreamEvent], Awaitable[None]]
type StepHandler = Callable[[AgentEvent], Awaitable[None]]

_FINALIZE_HINT = "已达最大步数，请基于以上内容直接给出最终回答，不要再调用工具。"


@dataclass(frozen=True, slots=True)
class _StepOutcome:
    """一次 LLM 调用的结构化结果。"""

    text: str
    tool_uses: list[dict[str, Any]]
    message: dict[str, Any] | None
    stop_reason: StopReason
    input_tokens: int
    output_tokens: int


class AgentLoop:
    """Agent 循环编排器。

    多轮调用 LLM：每轮收集文本与工具调用；若有工具调用则逐个执行并将
    tool_result 回填到消息列表，继续下一轮，直至模型不再调用工具或达到
    max_steps 上限。

    达到 max_steps 且模型仍请求工具时：默认追加一轮"收尾"调用（关闭
    tools、附加提示）以产出最终文本，最终 stop_reason 置为
    StopReason.MAX_STEPS；若关闭 finalize 则直接截断返回。

    AgentLoop 不直接触碰网络/通知：LLM 流式事件通过 on_event 回调透出，
    结构事件（step/tool 生命周期）通过 on_step 回调透出，便于上层
    （handler）转换为通知、任务日志或测试替身。
    """

    def __init__(
        self,
        llm_client: LLMProvider,
        tools: ToolRegistry,
        *,
        max_steps: int = 25,
        finalize: bool = True,
    ) -> None:
        """初始化 AgentLoop。

        Args:
            llm_client: LLM 客户端（任一 LLMProvider 实现）。
            tools: 工具注册表。
            max_steps: 最大循环步数（每次 LLM 调用计一步）。
            finalize: 撞上限时是否追加收尾轮以产出最终文本。
        """
        self._llm = llm_client
        self._tools = tools
        self._max_steps = max_steps
        self._finalize = finalize

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
            撞上限时 stop_reason 为 StopReason.MAX_STEPS，steps 为实际
            LLM 调用次数（含收尾轮）。
        """
        messages: list[dict[str, Any]] = list(history) if history else []
        messages.append({"role": "user", "content": user_input})

        final_text = ""
        stop_reason = StopReason.UNKNOWN
        total_input = 0
        total_output = 0
        steps = 0

        tools_param = self._tools.to_anthropic_tools() or None

        for step_index in range(1, self._max_steps + 1):
            steps = step_index
            if on_step is not None:
                await on_step(
                    StepStarted(
                        step_index=step_index,
                        messages=list(messages),
                        system=system,
                        tools=list(tools_param) if tools_param else None,
                    )
                )

            outcome = await self._chat_once(
                messages, system=system, tools=tools_param, on_event=on_event
            )

            if outcome.message is not None:
                messages.append(outcome.message)
                stop_reason = outcome.stop_reason
                total_input += outcome.input_tokens
                total_output += outcome.output_tokens

            has_tool_calls = bool(outcome.tool_uses)
            if on_step is not None:
                await on_step(
                    StepFinished(
                        step_index=step_index,
                        stop_reason=outcome.stop_reason,
                        input_tokens=outcome.input_tokens,
                        output_tokens=outcome.output_tokens,
                        has_tool_calls=has_tool_calls,
                        text=outcome.text,
                    )
                )

            if not outcome.tool_uses:
                final_text = outcome.text
                break

            tool_results: list[dict[str, Any]] = []
            for tool_use in outcome.tool_uses:
                if on_step is not None:
                    await on_step(
                        ToolStarted(
                            step_index=step_index,
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
                            step_index=step_index,
                            tool_name=tool_use["name"],
                            is_error=result.is_error,
                            content=result.content,
                        )
                    )
            messages.append({"role": "user", "content": tool_results})
        else:
            # 达到 max_steps 仍请求工具：进入收尾轮或直接截断
            if self._finalize:
                steps += 1
                if on_step is not None:
                    await on_step(
                        StepStarted(
                            step_index=steps,
                            messages=list(messages),
                            system=system,
                            tools=None,
                        )
                    )
                outcome = await self._chat_once(
                    messages,
                    system=self._finalize_system(system),
                    tools=None,
                    on_event=on_event,
                )
                if outcome.message is not None:
                    messages.append(outcome.message)
                    total_input += outcome.input_tokens
                    total_output += outcome.output_tokens
                if on_step is not None:
                    await on_step(
                        StepFinished(
                            step_index=steps,
                            stop_reason=outcome.stop_reason,
                            input_tokens=outcome.input_tokens,
                            output_tokens=outcome.output_tokens,
                            has_tool_calls=bool(outcome.tool_uses),
                            text=outcome.text,
                        )
                    )
                final_text = outcome.text
            stop_reason = StopReason.MAX_STEPS

        return AgentResult(
            text=final_text,
            messages=messages,
            usage=TokenUsage(input_tokens=total_input, output_tokens=total_output),
            steps=steps,
            stop_reason=stop_reason,
        )

    async def _chat_once(
        self,
        messages: list[dict[str, Any]],
        *,
        system: str | None,
        tools: list[dict[str, Any]] | None,
        on_event: EventHandler | None,
    ) -> _StepOutcome:
        """执行一次 LLM 调用并收集流式事件。

        Args:
            messages: 当前消息列表。
            system: 系统提示（可选）。
            tools: Anthropic 格式的工具 schema 列表（可选）。
            on_event: LLM 流式事件回调（可选）。

        Returns:
            本轮的结构化结果（文本、工具调用、assistant message、
            停止原因与 token 用量）。
        """
        text_parts: list[str] = []
        tool_uses: list[dict[str, Any]] = []
        last_message: dict[str, Any] | None = None
        input_tokens = 0
        output_tokens = 0
        stop_reason = StopReason.UNKNOWN

        async for event in self._llm.chat_stream(messages, system=system, tools=tools):
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
                stop_reason = event.stop_reason
                input_tokens = event.usage.input_tokens
                output_tokens = event.usage.output_tokens

        return _StepOutcome(
            text="".join(text_parts),
            tool_uses=tool_uses,
            message=last_message,
            stop_reason=stop_reason,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def _finalize_system(self, system: str | None) -> str | None:
        """构造收尾轮的系统提示，附加"不要继续调用工具"的约束。

        Args:
            system: 原始系统提示（可选）。

        Returns:
            追加收尾约束后的系统提示，原始为空时仅返回约束本身。
        """
        if system:
            return f"{system}\n{_FINALIZE_HINT}"
        return _FINALIZE_HINT
