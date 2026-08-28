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
