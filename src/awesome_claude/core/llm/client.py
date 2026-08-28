"""LLM Client - Anthropic SDK 封装，支持流式输出。"""

import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import anthropic

from awesome_claude.core.llm.events import DoneEvent, LLMStreamEvent, TextDeltaEvent
from awesome_claude.core.llm.exceptions import (
    LLMAuthError,
    LLMContentFilterError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from awesome_claude.shared.types import ChatResponse, TokenUsage


class LLMClient:
    """Anthropic 消息 API 客户端封装，支持流式与非流式调用。"""

    def __init__(
        self,
        api_key: str,
        model: str,
        max_tokens: int = 4096,
        base_url: str | None = None,
    ) -> None:
        """初始化 LLM 客户端。

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
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        """流式调用 Anthropic API。

        处理 Anthropic 流式事件：
        - content_block_delta(text_delta) → 产出 TextDeltaEvent
        - message_stop → 产出 DoneEvent（含 stop_reason、完整文本、token 用量）

        Token 统计：message_start 提取 input_tokens，message_delta 提取 output_tokens 与 stop_reason。

        Args:
            messages: 对话消息列表（Anthropic 格式）。
            system: 系统提示（可选）。
            max_tokens: 最大输出 token 数，覆盖默认值。

        Yields:
            LLMStreamEvent: TextDeltaEvent（增量文本）或 DoneEvent（结束事件）。

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

        input_tokens = 0
        output_tokens = 0
        stop_reason = ""
        full_text_parts: list[str] = []
        try:
            async with self._client.messages.stream(**stream_kwargs) as stream:
                async for raw_event in stream:
                    event: Any = raw_event
                    event_type = getattr(event, "type", None)
                    if event_type == "message_start":
                        usage = getattr(getattr(event, "message", None), "usage", None)
                        if usage is not None:
                            input_tokens = getattr(usage, "input_tokens", input_tokens)
                    elif event_type == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if getattr(delta, "type", None) == "text_delta":
                            text = getattr(delta, "text", "") or ""
                            full_text_parts.append(text)
                            yield TextDeltaEvent(text=text)
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
                            stop_reason = reason
                    elif event_type == "message_stop":
                        yield DoneEvent(
                            stop_reason=stop_reason,
                            full_text="".join(full_text_parts),
                            usage=TokenUsage(
                                input_tokens=input_tokens,
                                output_tokens=output_tokens,
                            ),
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
        stop_reason = ""
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
