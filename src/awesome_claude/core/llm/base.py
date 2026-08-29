"""LLM Provider 抽象 - 定义供应商无关的 LLM 客户端接口。"""

from collections.abc import AsyncGenerator
from typing import Any, Protocol, cast, runtime_checkable

from awesome_claude.core.llm.events import LLMStreamEvent
from awesome_claude.shared.types import ChatResponse


@runtime_checkable
class LLMProvider(Protocol):
    """LLM 供应商客户端接口。

    上游（AgentLoop / handler）只依赖本协议，不感知具体供应商 SDK；
    各供应商（如 AnthropicClient）实现本协议并统一产出 LLMStreamEvent。

    实现方约定：
    - ``chat_stream`` 产出的事件序列以 ``DoneEvent`` 结尾；
    - 异常统一映射为 ``core.llm.exceptions`` 中的 ``LLMError`` 子类。
    """

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        system: str | None = None,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncGenerator[LLMStreamEvent, None]:
        """流式调用 LLM，产出统一事件流。

        Args:
            messages: 对话消息列表（供应商原生格式）。
            system: 系统提示（可选）。
            max_tokens: 最大输出 token 数，覆盖默认值。
            tools: 工具 schema 列表（供应商原生格式，可选）。

        Yields:
            LLMStreamEvent: 文本/思考增量、工具调用事件或 DoneEvent。
        """
        if False:
            yield cast(LLMStreamEvent, None)

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
        """
        ...

    async def close(self) -> None:
        """关闭底层客户端资源。"""
        ...
