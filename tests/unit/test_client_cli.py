"""客户端 CLI 命令解析与渲染器测试。"""

import asyncio
import time

from awesome_claude.client.cli.app import CLIApp, _sanitize_input
from awesome_claude.client.cli.commands import parse_command
from awesome_claude.client.cli.renderer import StreamRenderer


class TestSanitizeInput:
    """_sanitize_input 代理字符清理测试。"""

    def test_valid_text_unchanged(self) -> None:
        text = "帮忙在本项目创建一个 Springboot 框架"
        assert _sanitize_input(text) == text

    def test_replaces_lone_surrogate(self) -> None:
        dirty = "帮忙\udce5测试"
        clean = _sanitize_input(dirty)
        assert clean == "帮忙\ufffd测试"
        clean.encode("utf-8")

    def test_replaces_all_surrogates(self) -> None:
        clean = _sanitize_input("\ud800\udc00\udce5")
        assert all(not (0xD800 <= ord(ch) <= 0xDFFF) for ch in clean)
        clean.encode("utf-8")


class TestParseCommand:
    """parse_command 测试。"""

    def test_plain_text_is_chat(self) -> None:
        assert parse_command("你好") is None
        assert parse_command("  hello  ") is None

    def test_ping(self) -> None:
        assert parse_command("/ping") == ("ping", {})

    def test_echo(self) -> None:
        assert parse_command("/echo hello world") == (
            "echo",
            {"message": "hello world"},
        )

    def test_echo_no_text_error(self) -> None:
        command, params = parse_command("/echo")
        assert command == "error"
        assert "用法" in params["message"]

    def test_quit_and_exit(self) -> None:
        assert parse_command("/quit") == ("quit", {})
        assert parse_command("/exit") == ("quit", {})

    def test_stats(self) -> None:
        assert parse_command("/stats") == ("stats", {})

    def test_help(self) -> None:
        assert parse_command("/help") == ("help", {})

    def test_unknown(self) -> None:
        command, params = parse_command("/bogus")
        assert command == "unknown"
        assert params["command"] == "/bogus"


class TestStreamRenderer:
    """StreamRenderer 渲染测试。"""

    def test_render_chunk_and_done(self, capsys) -> None:
        renderer = StreamRenderer()
        renderer.render_chunk("你好")
        renderer.render_chunk("世界")
        renderer.render_done()
        assert capsys.readouterr().out == "你好世界\n"
        assert renderer.chunk_count == 2

    def test_render_summary(self, capsys) -> None:
        renderer = StreamRenderer()
        renderer.render_summary(
            {
                "run_id": "abc12345",
                "text": "hi",
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "duration_ms": 123.4,
                "model": "m",
            }
        )
        out = capsys.readouterr().out
        assert "tokens: 10 in / 20 out" in out
        assert "123ms | model: m" in out
        assert "run: abc12345" in out

    def test_render_summary_missing_fields(self, capsys) -> None:
        renderer = StreamRenderer()
        renderer.render_summary({})
        out = capsys.readouterr().out
        assert "tokens: 0 in / 0 out" in out

    def test_render_error(self, capsys) -> None:
        renderer = StreamRenderer()
        renderer.render_error(
            {"code": -32002, "message": "auth failed", "data": {"k": 1}}
        )
        out = capsys.readouterr().out
        assert "32002" in out
        assert "auth failed" in out


class _FakeConnection:
    """仅暴露 last_activity 的连接替身。"""

    def __init__(self, last_activity: float) -> None:
        self.last_activity = last_activity


class TestWatchConnection:
    """心跳看门狗行为测试。"""

    async def test_stale_connection_triggers(self, capsys) -> None:
        app = CLIApp("127.0.0.1", 1)
        app._completion_event = asyncio.Event()
        app._heartbeat_interval_ms = 30

        await app._watch_connection(_FakeConnection(time.monotonic() - 100.0))

        assert app._completion_event.is_set()
        assert "连接疑似中断" in capsys.readouterr().err

    async def test_fresh_activity_does_not_trigger(self) -> None:
        app = CLIApp("127.0.0.1", 1)
        app._completion_event = asyncio.Event()
        app._heartbeat_interval_ms = 1000

        task = asyncio.create_task(
            app._watch_connection(_FakeConnection(time.monotonic()))
        )
        await asyncio.sleep(0.05)
        assert not app._completion_event.is_set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


class TestChatTerminalHandling:
    """终态通知处理与 ack 前到达的缓冲。"""

    async def test_completed_sets_event_and_stats(self) -> None:
        app = CLIApp("127.0.0.1", 1)
        app._completion_event = asyncio.Event()
        app._awaiting_run_id = "r1"

        await app._handle_completed(
            {"run_id": "r1", "usage": {"input_tokens": 3, "output_tokens": 4}}
        )

        assert app._completion_event.is_set()
        assert app._session_stats == {"input_tokens": 3, "output_tokens": 4}

    async def test_terminal_before_ack_is_buffered(self) -> None:
        app = CLIApp("127.0.0.1", 1)
        app._completion_event = asyncio.Event()
        app._awaiting_run_id = None

        await app._handle_failed(
            {"run_id": "r1", "error": {"code": -32001, "message": "boom"}}
        )

        assert app._pending_terminal is not None
        assert not app._completion_event.is_set()

    async def test_mismatched_run_ignored(self) -> None:
        app = CLIApp("127.0.0.1", 1)
        app._completion_event = asyncio.Event()
        app._awaiting_run_id = "r1"

        await app._handle_completed({"run_id": "other", "usage": {}})

        assert not app._completion_event.is_set()
