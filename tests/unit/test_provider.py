"""core/llm LLMProvider 协议测试。"""

from unittest.mock import AsyncMock, patch

from awesome_claude.core.llm.anthropic_client import AnthropicClient
from awesome_claude.core.llm.base import LLMProvider


class TestLLMProviderProtocol:
    """LLMProvider 协议与实现一致性测试。"""

    def test_anthropic_client_subclasses_protocol(self) -> None:
        assert issubclass(AnthropicClient, LLMProvider)

    async def test_anthropic_client_is_runtime_checkable_instance(self) -> None:
        fake = AsyncMock()
        with patch("anthropic.AsyncAnthropic", return_value=fake):
            client = AnthropicClient(api_key="k", model="m")
        assert isinstance(client, LLMProvider)
        await client.close()
        fake.close.assert_awaited_once()
