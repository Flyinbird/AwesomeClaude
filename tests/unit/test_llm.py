"""core/llm LLMClient 测试（mock Anthropic SDK，不真实调用 API）。"""

from types import SimpleNamespace
from typing import Any, Self
from unittest.mock import MagicMock, patch

import anthropic
import pytest

from awesome_claude.core.llm.client import LLMClient
from awesome_claude.core.llm.events import DoneEvent, TextDeltaEvent
from awesome_claude.core.llm.exceptions import (
    LLMAuthError,
    LLMContentFilterError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)


def _message_start(input_tokens: int = 12) -> SimpleNamespace:
    """构造 message_start 事件。"""
    return SimpleNamespace(
        type="message_start",
        message=SimpleNamespace(usage=SimpleNamespace(input_tokens=input_tokens)),
    )


def _text_delta(text: str) -> SimpleNamespace:
    """构造文本增量事件。"""
    return SimpleNamespace(
        type="content_block_delta",
        delta=SimpleNamespace(type="text_delta", text=text),
    )


def _message_delta(output_tokens: int, stop_reason: str) -> SimpleNamespace:
    """构造 message_delta 事件。"""
    return SimpleNamespace(
        type="message_delta",
        usage=SimpleNamespace(output_tokens=output_tokens),
        delta=SimpleNamespace(stop_reason=stop_reason),
    )


def _message_stop() -> SimpleNamespace:
    """构造 message_stop 事件。"""
    return SimpleNamespace(type="message_stop")


def _stream_events() -> list[SimpleNamespace]:
    """构造 3 个文本增量 + 结束的标准事件序列。"""
    return [
        _message_start(12),
        _text_delta("Hello "),
        _text_delta("world"),
        _text_delta("!"),
        _message_delta(7, "end_turn"),
        _message_stop(),
    ]


class FakeMessageStream:
    """可迭代的 fake 消息流。"""

    def __init__(self, events: list[Any], exc: Exception | None = None) -> None:
        self._events = list(events)
        self._exc = exc

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    def __aiter__(self) -> Any:
        async def _gen() -> Any:
            if self._exc is not None:
                raise self._exc
            for event in self._events:
                yield event

        return _gen()


class FakeStreamManager:
    """返回 FakeMessageStream 的上下文管理器。"""

    def __init__(self, events: list[Any], exc: Exception | None = None) -> None:
        self._events = list(events)
        self._exc = exc

    async def __aenter__(self) -> FakeMessageStream:
        return FakeMessageStream(self._events, self._exc)

    async def __aexit__(self, *args: object) -> bool:
        return False


class FakeMessages:
    """fake messages 端点。"""

    def __init__(self, events: list[Any], exc: Exception | None = None) -> None:
        self._events = list(events)
        self._exc = exc
        self.stream_calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> FakeStreamManager:
        self.stream_calls.append(kwargs)
        return FakeStreamManager(self._events, self._exc)


class FakeAnthropicClient:
    """fake AsyncAnthropic 客户端。"""

    def __init__(self, events: list[Any], exc: Exception | None = None) -> None:
        self.messages = FakeMessages(events, exc)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def make_client(
    events: list[Any] | None = None, exc: Exception | None = None
) -> tuple[LLMClient, FakeAnthropicClient]:
    """创建使用 fake 底层客户端的 LLMClient。"""
    fake = FakeAnthropicClient(events or [], exc)
    with patch("anthropic.AsyncAnthropic", return_value=fake):
        client = LLMClient(api_key="test-key", model="test-model", max_tokens=64)
    return client, fake


class TestChatStream:
    """流式输出测试。"""

    async def test_emits_text_deltas_then_done(self) -> None:
        client, _ = make_client(_stream_events())
        events = []
        async for event in client.chat_stream([{"role": "user", "content": "hi"}]):
            events.append(event)

        text_deltas = [e for e in events if isinstance(e, TextDeltaEvent)]
        assert [d.text for d in text_deltas] == ["Hello ", "world", "!"]

        done = events[-1]
        assert isinstance(done, DoneEvent)
        assert done.stop_reason == "end_turn"
        assert done.full_text == "Hello world!"
        assert done.usage.input_tokens == 12
        assert done.usage.output_tokens == 7
        await client.close()

    async def test_usage_statistics(self) -> None:
        client, _ = make_client(
            [
                _message_start(30),
                _text_delta("a"),
                _message_delta(5, "max_tokens"),
                _message_stop(),
            ]
        )
        done = None
        async for event in client.chat_stream([{"role": "user", "content": "q"}]):
            if isinstance(event, DoneEvent):
                done = event
        assert done is not None
        assert done.usage.input_tokens == 30
        assert done.usage.output_tokens == 5
        assert done.stop_reason == "max_tokens"
        await client.close()

    async def test_passes_model_messages_system_max_tokens(self) -> None:
        client, fake = make_client(_stream_events())
        messages = [{"role": "user", "content": "q"}]
        async for _ in client.chat_stream(messages, system="sys", max_tokens=123):
            pass
        kwargs = fake.messages.stream_calls[0]
        assert kwargs["model"] == "test-model"
        assert kwargs["max_tokens"] == 123
        assert kwargs["system"] == "sys"
        assert kwargs["messages"] == messages
        await client.close()

    async def test_default_max_tokens(self) -> None:
        client, fake = make_client(_stream_events())
        async for _ in client.chat_stream([{"role": "user", "content": "q"}]):
            pass
        assert fake.messages.stream_calls[0]["max_tokens"] == 64
        await client.close()

    async def test_system_omitted_when_none(self) -> None:
        client, fake = make_client(_stream_events())
        async for _ in client.chat_stream([{"role": "user", "content": "q"}]):
            pass
        assert "system" not in fake.messages.stream_calls[0]
        await client.close()


class TestExceptionMapping:
    """Anthropic 异常 → LLM 异常映射测试。"""

    @pytest.mark.parametrize(
        ("exc", "expected"),
        [
            (
                anthropic.AuthenticationError(
                    "bad key", response=MagicMock(), body=None
                ),
                LLMAuthError,
            ),
            (
                anthropic.RateLimitError("rate limit", response=MagicMock(), body=None),
                LLMRateLimitError,
            ),
            (anthropic.APITimeoutError(request=MagicMock()), LLMTimeoutError),
            (
                anthropic.BadRequestError("filtered", response=MagicMock(), body=None),
                LLMContentFilterError,
            ),
            (anthropic.APIError("boom", request=MagicMock(), body=None), LLMError),
        ],
    )
    async def test_mapping(self, exc: Exception, expected: type[Exception]) -> None:
        client, _ = make_client(exc=exc)
        with pytest.raises(expected):
            async for _ in client.chat_stream([{"role": "user", "content": "q"}]):
                pass
        await client.close()

    async def test_llm_exception_hierarchy(self) -> None:
        assert issubclass(LLMAuthError, LLMError)
        assert issubclass(LLMTimeoutError, LLMError)
        assert issubclass(LLMRateLimitError, LLMError)
        assert issubclass(LLMContentFilterError, LLMError)


class TestChat:
    """非流式 chat 方法测试。"""

    async def test_chat_collects_full_response(self) -> None:
        client, _ = make_client(_stream_events())
        resp = await client.chat([{"role": "user", "content": "hi"}])
        assert resp.text == "Hello world!"
        assert resp.stop_reason == "end_turn"
        assert resp.usage.input_tokens == 12
        assert resp.usage.output_tokens == 7
        assert resp.model == "test-model"
        assert resp.duration_ms >= 0
        assert resp.task_id
        await client.close()

    async def test_close_closes_underlying_client(self) -> None:
        client, fake = make_client([])
        assert not fake.closed
        await client.close()
        assert fake.closed
