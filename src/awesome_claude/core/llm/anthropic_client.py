"""Anthropic LLM Provider - 基于 Anthropic SDK 的 LLMProvider 实现。"""

import json
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import anthropic

from awesome_claude.core.llm.base import LLMProvider
from awesome_claude.core.llm.events import (
    DoneEvent,
    InputJsonDeltaEvent,
    LLMStreamEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolUseEndEvent,
    ToolUseStartEvent,
)
from awesome_claude.core.llm.exceptions import (
    LLMAuthError,
    LLMContentFilterError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from awesome_claude.shared.types import ChatResponse, StopReason, TokenUsage


class AnthropicClient(LLMProvider):
    """Anthropic 消息 API 客户端封装，支持流式与非流式调用。"""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int = 4096,
        base_url: str | None = None,
    ) -> None:
        """初始化 Anthropic 客户端。

        Args:
            api_key: Anthropic API key。
            model: 模型名。
            max_tokens: 默认最大输出 token 数。
            base_url: 自定义 API 基地址（可选，用于代理/兼容端点）。
        """
        client_kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": 2}
        if base_url is not None:
            client_kwargs["base_url"] = base_url
        self._client = anthropic.AsyncAnthropic(**client_kwargs)
        self._model = model
        self._default_max_tokens = max_tokens

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        """流式调用 Anthropic API。

        处理 Anthropic 流式事件：
        - content_block_delta(text_delta) → 产出 TextDeltaEvent
        - content_block_delta(thinking_delta) → 产出 ThinkingDeltaEvent
        - content_block_start(tool_use) → 产出 ToolUseStartEvent
        - content_block_delta(input_json_delta) → 产出 InputJsonDeltaEvent
        - content_block_stop(tool_use) → 产出 ToolUseEndEvent（参数已解析）
        - message_stop → 产出 DoneEvent（含 stop_reason、完整文本、token 用量、
          以及本轮完整的 assistant message）

        Token 统计：message_start 提取 input_tokens，message_delta 提取 output_tokens 与 stop_reason。

        Args:
            messages: 对话消息列表（Anthropic 格式）。
            system: 系统提示（可选）。
            max_tokens: 最大输出 token 数，覆盖默认值。
            tools: Anthropic 格式的工具 schema 列表（可选）。

        Yields:
            LLMStreamEvent: 文本/思考增量、工具调用事件或 DoneEvent。

        Raises:
            LLMAuthError: 认证失败。
            LLMRateLimitError: 速率限制。
            LLMTimeoutError: 请求超时。
            LLMContentFilterError: 内容被过滤。
            LLMError: 其他 LLM API 错误。
        """
        stream_kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens or self._default_max_tokens,
            "messages": messages,
        }
        if system is not None:
            stream_kwargs["system"] = system
        if tools:
            stream_kwargs["tools"] = tools

        input_tokens = 0
        output_tokens = 0
        stop_reason = StopReason.UNKNOWN
        full_text_parts: list[str] = []
        # index → 累积的 content block（仅 text 与 tool_use，thinking 不参与回填）
        blocks: dict[int, dict[str, Any]] = {}
        # index → 该 tool_use 块的参数 JSON 片段
        tool_json_parts: dict[int, list[str]] = {}
        try:
            async with self._client.messages.stream(**stream_kwargs) as stream:
                async for raw_event in stream:
                    event: Any = raw_event
                    event_type = getattr(event, "type", None)
                    if event_type == "message_start":
                        usage = getattr(getattr(event, "message", None), "usage", None)
                        if usage is not None:
                            input_tokens = getattr(usage, "input_tokens", input_tokens)
                    elif event_type == "content_block_start":
                        start_event = self._on_block_start(
                            event, blocks, tool_json_parts
                        )
                        if start_event is not None:
                            yield start_event
                    elif event_type == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        delta_type = getattr(delta, "type", None)
                        index = getattr(event, "index", None)
                        if delta_type == "text_delta":
                            text = getattr(delta, "text", "") or ""
                            full_text_parts.append(text)
                            if index is not None and index in blocks:
                                blocks[index]["text"] += text
                            yield TextDeltaEvent(text=text)
                        elif delta_type == "thinking_delta":
                            yield ThinkingDeltaEvent(
                                text=getattr(delta, "thinking", "") or ""
                            )
                        elif delta_type == "input_json_delta":
                            partial = getattr(delta, "partial_json", "") or ""
                            if index is not None and index in tool_json_parts:
                                tool_json_parts[index].append(partial)
                            block = blocks.get(index) if index is not None else None
                            yield InputJsonDeltaEvent(
                                block_id=block["id"] if block else "",
                                partial_json=partial,
                            )
                    elif event_type == "content_block_stop":
                        stop_event = self._on_block_stop(event, blocks, tool_json_parts)
                        if stop_event is not None:
                            yield stop_event
                    elif event_type == "message_delta":
                        usage = getattr(event, "usage", None)
                        if usage is not None:
                            output_tokens = getattr(
                                usage, "output_tokens", output_tokens
                            )
                        reason = getattr(
                            getattr(event, "delta", None), "stop_reason", None
                        )
                        if reason:
                            stop_reason = StopReason.from_raw(reason)
                    elif event_type == "message_stop":
                        ordered = [blocks[i] for i in sorted(blocks)]
                        yield DoneEvent(
                            stop_reason=stop_reason,
                            full_text="".join(full_text_parts),
                            usage=TokenUsage(
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                            ),
                            message={"role": "assistant", "content": ordered},
                        )
        except anthropic.AuthenticationError as exc:
            raise LLMAuthError(f"LLM 认证失败: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMRateLimitError(f"LLM 速率限制: {exc}") from exc
        except anthropic.APITimeoutError as exc:
            raise LLMTimeoutError(f"LLM 请求超时: {exc}") from exc
        except anthropic.BadRequestError as exc:
            raise LLMContentFilterError(f"LLM 内容被过滤: {exc}") from exc
        except anthropic.APIError as exc:
            raise LLMError(f"LLM 调用失败: {exc}") from exc

    def _on_block_start(
        self,
        event: Any,
        blocks: dict[int, dict[str, Any]],
        tool_json_parts: dict[int, list[str]],
    ) -> ToolUseStartEvent | None:
        """处理 content_block_start 事件。

        初始化 text / tool_use 块；tool_use 块返回起始事件，其余返回 None。

        Args:
            event: 原始流式事件。
            blocks: index → 累积 block 的映射（就地修改）。
            tool_json_parts: index → 参数 JSON 片段列表（就地修改）。

        Returns:
            若为新 tool_use 块则返回 ToolUseStartEvent，否则 None。
        """
        index = getattr(event, "index", None)
        block = getattr(event, "content_block", None)
        block_type = getattr(block, "type", None)
        if index is None:
            return None
        if block_type == "text":
            blocks[index] = {"type": "text", "text": ""}
        elif block_type == "tool_use":
            block_id = getattr(block, "id", "")
            name = getattr(block, "name", "")
            blocks[index] = {
                "type": "tool_use",
                "id": block_id,
                "name": name,
                "input": {},
            }
            tool_json_parts[index] = []
            return ToolUseStartEvent(block_id=block_id, name=name)
        return None

    def _on_block_stop(
        self,
        event: Any,
        blocks: dict[int, dict[str, Any]],
        tool_json_parts: dict[int, list[str]],
    ) -> ToolUseEndEvent | None:
        """处理 content_block_stop 事件。

        tool_use 块结束时拼接并解析参数 JSON，产出结束事件。

        Args:
            event: 原始流式事件。
            blocks: index → 累积 block 的映射（就地修改）。
            tool_json_parts: index → 参数 JSON 片段列表。

        Returns:
            若为 tool_use 块结束则返回 ToolUseEndEvent，否则 None。
        """
        index = getattr(event, "index", None)
        if index is None:
            return None
        block = blocks.get(index)
        if block is None or block["type"] != "tool_use":
            return None
        raw = "".join(tool_json_parts.get(index, []))
        try:
            parsed = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            parsed = {}
        block["input"] = parsed
        return ToolUseEndEvent(block_id=block["id"], name=block["name"], input=parsed)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        """非流式便捷方法：收集全部流式事件后返回完整响应。

        Args:
            messages: 对话消息列表。
            system: 系统提示（可选）。
            max_tokens: 最大输出 token 数（可选）。

        Returns:
            完整对话响应。

        Raises:
            同 chat_stream。
        """
        start = time.perf_counter()
        text_parts: list[str] = []
        usage = TokenUsage(input_tokens=0, output_tokens=0)
        stop_reason = StopReason.UNKNOWN
        async for event in self.chat_stream(messages, system, max_tokens):
            if isinstance(event, TextDeltaEvent):
                text_parts.append(event.text)
            elif isinstance(event, DoneEvent):
                stop_reason = event.stop_reason
                usage = event.usage
        duration_ms = (time.perf_counter() - start) * 1000.0
        return ChatResponse(
            task_id=str(uuid.uuid4()),
            text="".join(text_parts),
            stop_reason=stop_reason,
            usage=usage,
            duration_ms=duration_ms,
            model=self._model,
        )

    async def close(self) -> None:
        """关闭底层 HTTP 客户端。"""
        await self._client.close()
