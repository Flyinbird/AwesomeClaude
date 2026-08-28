"""客户端 CLI 命令解析与渲染器测试。"""

from awesome_claude.client.cli.commands import parse_command
from awesome_claude.client.cli.renderer import StreamRenderer


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
                "task_id": "abc12345",
                "text": "hi",
                "usage": {"input_tokens": 10, "output_tokens": 20},
                "duration_ms": 123.4,
                "model": "m",
            }
        )
        out = capsys.readouterr().out
        assert "tokens: 10 in / 20 out" in out
        assert "123ms | model: m" in out
        assert "task: abc12345" in out

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
