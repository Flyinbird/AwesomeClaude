"""CLI 渲染器 - 展示服务端响应与流式输出。"""

import sys
from typing import Any


class StreamRenderer:
    """处理流式输出与结果摘要的终端渲染。"""

    def __init__(self) -> None:
        """初始化渲染器。"""
        self._chunk_count = 0

    @property
    def chunk_count(self) -> int:
        """累计渲染的文本块数量。"""
        return self._chunk_count

    def render_chunk(self, text: str) -> None:
        """渲染一个流式文本块（不换行，直接拼接）。"""
        sys.stdout.write(text)
        sys.stdout.flush()
        self._chunk_count += 1

    def render_done(self) -> None:
        """流式结束，输出换行。"""
        sys.stdout.write("\n")
        sys.stdout.flush()

    def render_summary(self, response: dict[str, Any]) -> None:
        """渲染最终摘要。

        Args:
            response: ChatResponse 的 dict 形式。
        """
        usage = response.get("usage", {})
        print(
            f"📊 tokens: {usage.get('input_tokens', 0)} in / "
            f"{usage.get('output_tokens', 0)} out"
        )
        print(
            f"⏱ {response.get('duration_ms', 0):.0f}ms | model: {response.get('model', '')}"
        )
        print(f"🔑 task: {response.get('task_id', '')}")

    def render_error(self, error: dict[str, Any]) -> None:
        """渲染错误信息。

        Args:
            error: 错误详情 dict。
        """
        print(f"错误 {error.get('code')}: {error.get('message')}")
        if "data" in error:
            print(f"  data={error['data']}")

    def render_tool_started(self, tool_name: str) -> None:
        """渲染工具调用开始。

        Args:
            tool_name: 工具名。
        """
        print(f"🔧 调用工具 {tool_name} ...")

    def render_tool_finished(self, tool_name: str, is_error: bool) -> None:
        """渲染工具调用结束。

        Args:
            tool_name: 工具名。
            is_error: 是否执行失败。
        """
        status = "失败" if is_error else "完成"
        print(f"🔧 工具 {tool_name} {status}")

    def render_interrupted(self, stop_reason: str) -> None:
        """渲染"任务被中断"提示。

        Args:
            stop_reason: 中断原因（如 max_steps）。
        """
        print(f"⚠️ 任务未完整完成（{stop_reason}）")

    def render_user_message(self, message: str) -> None:
        """渲染其他客户端的用户输入。

        Args:
            message: 用户输入。
        """
        print(f"👤 其他客户端: {message}")

    def render_history(self, history: list[dict[str, Any]]) -> None:
        """渲染会话历史回放。

        Args:
            history: 会话历史记录列表。
        """
        for turn in history:
            role = turn.get("role")
            content = turn.get("content")
            prefix = "你" if role == "user" else "AI"
            print(f"  {prefix}: {content}")
