"""core/agent/prompt System Prompt 构造测试。"""

from datetime import UTC, datetime
from pathlib import Path

from awesome_claude.core.agent.prompt import (
    PROMPT_VERSION,
    PromptContext,
    ToolBrief,
    build_system_prompt,
)


def _ctx(
    *,
    tools: tuple[ToolBrief, ...] = (
        ToolBrief(name="read_file", description="读取文本"),
    ),
    model: str = "test-model",
) -> PromptContext:
    return PromptContext(
        workspace_root=Path("/tmp/ws"),
        fs_max_read=30000,
        fs_max_write=100000,
        max_tokens=4096,
        tools=tools,
        model=model,
    )


class TestBuildSystemPrompt:
    """build_system_prompt 段落与动态注入测试。"""

    def test_includes_environment_and_limits(self) -> None:
        text = build_system_prompt(
            _ctx(), now=datetime(2026, 9, 20, 10, 30, tzinfo=UTC)
        )

        assert "/tmp/ws" in text
        assert "4096" in text
        assert "30000" in text
        assert "100000" in text
        assert "2026-09-20 10:30" in text
        assert "read_file: 读取文本" in text

    def test_includes_large_file_truncation_strategy(self) -> None:
        text = build_system_prompt(_ctx())

        assert "截断" in text
        assert "分段" in text
        assert "write_file" in text
        assert "edit_file" in text

    def test_version_constant(self) -> None:
        assert PROMPT_VERSION == "v1"

    def test_empty_tools_placeholder(self) -> None:
        text = build_system_prompt(_ctx(tools=()))

        assert "无可用工具" in text

    def test_model_fallback(self) -> None:
        text = build_system_prompt(_ctx(model=""))

        assert "未知" in text
