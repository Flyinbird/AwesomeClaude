"""CLI 应用入口 - 交互循环主流程。"""

import asyncio
import json
import uuid
from typing import Any

from awesome_claude.client.cli.commands import parse_command
from awesome_claude.client.cli.renderer import StreamRenderer
from awesome_claude.client.transport.connection import ClientConnection
from awesome_claude.protocol.methods import (
    METHOD_CHAT,
    METHOD_ECHO,
    METHOD_PING,
    METHOD_SESSION_ATTACH,
    NOTIFY_CHAT_INTERRUPTED,
    NOTIFY_CHAT_PLAN_UPDATED,
    NOTIFY_CHAT_STREAM,
    NOTIFY_CHAT_TOOL_FINISHED,
    NOTIFY_CHAT_TOOL_STARTED,
    NOTIFY_CHAT_USER_MESSAGE,
)
from awesome_claude.shared.logging.app_logger import get_app_logger

HELP_TEXT = """可用命令:
  /ping            健康检查
  /echo <text>     回显测试
  /session         显示当前会话 ID
  /stats           显示会话累计 token 用量
  /quit, /exit     退出
  /help            显示本帮助
其他输入将作为 chat 消息发送（流式输出）。"""

WELCOME_TEXT = "已连接。输入文本聊天，/help 查看命令，/quit 退出。"


def _sanitize_input(text: str) -> str:
    """将终端输入规范化为可安全编码的 UTF-8 文本。

    在 UTF-8 mode 下 stdin 以 surrogateescape 解码，中文等多字节字符
    在 IME / 粘贴边界被截断时会残留孤立代理字符（如 ``"\\udce5"``）。
    这类字符无法再次编码为合法 UTF-8，会导致 JSON 请求编码失败并让
    客户端崩溃。此处将所有代理码位替换为 Unicode 替换字符 U+FFFD。

    Args:
        text: ``input()`` 返回的原始行。

    Returns:
        不含代理码位、可安全编码为 UTF-8 的字符串。
    """
    return "".join("\ufffd" if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in text)


class CLIApp:
    """CLI 应用。"""

    def __init__(self, host: str, port: int, session_id: str | None = None) -> None:
        """初始化应用。

        Args:
            host: core server 地址。
            port: core server 端口。
            session_id: 会话 ID，缺省时自动生成新会话。
        """
        self._host = host
        self._port = port
        self._session_id = session_id or uuid.uuid4().hex[:8]
        self._connection: ClientConnection | None = None
        self._renderer = StreamRenderer()
        self._session_stats: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        self._active_run_id: str | None = None
        self._logger = get_app_logger("client.cli")

    async def run(self) -> None:
        """主循环：连接 → 注册通知 → attach 会话 → REPL。"""
        conn = ClientConnection(self._host, self._port)
        self._connection = conn
        try:
            await conn.connect()
        except (OSError, ConnectionError) as exc:
            print(f"无法连接到 core server {self._host}:{self._port}: {exc}")
            return
        self._register_notifications(conn)
        await self._attach(conn)
        print(WELCOME_TEXT)
        try:
            while True:
                try:
                    line = await asyncio.to_thread(input, "> ")
                except (EOFError, KeyboardInterrupt):
                    print()
                    break
                line = _sanitize_input(line)
                if not await self._process_line(line, conn):
                    break
        finally:
            await conn.disconnect()

    def _register_notifications(self, conn: ClientConnection) -> None:
        """注册各类通知处理器。"""
        conn.on_notification(NOTIFY_CHAT_STREAM, self._handle_stream_notification)
        conn.on_notification(NOTIFY_CHAT_USER_MESSAGE, self._handle_user_message)
        conn.on_notification(NOTIFY_CHAT_TOOL_STARTED, self._handle_tool_started)
        conn.on_notification(NOTIFY_CHAT_TOOL_FINISHED, self._handle_tool_finished)
        conn.on_notification(NOTIFY_CHAT_INTERRUPTED, self._handle_interrupted)
        conn.on_notification(NOTIFY_CHAT_PLAN_UPDATED, self._handle_plan_updated)

    async def _attach(self, conn: ClientConnection) -> None:
        """订阅到会话并回放历史。"""
        try:
            resp = await conn.send_request(
                METHOD_SESSION_ATTACH, {"session_id": self._session_id}
            )
        except ConnectionError:
            return
        if "error" in resp:
            print(f"attach 会话失败: {resp['error'].get('message')}")
            return
        history = resp.get("result", {}).get("history", [])
        if history:
            print(f"会话 {self._session_id} 历史:")
            self._renderer.render_history(history)

    async def _handle_stream_notification(self, params: dict[str, Any]) -> None:
        """处理 chat.stream 通知并渲染流式文本（按 run_id 区分 Run）。"""
        run_id = params.get("run_id")
        is_final = params.get("is_final")
        if not is_final:
            if run_id is not None and run_id != self._active_run_id:
                self._active_run_id = run_id
            self._renderer.render_chunk(str(params.get("text", "")))
        else:
            self._renderer.render_done()
            self._active_run_id = None

    async def _handle_user_message(self, params: dict[str, Any]) -> None:
        """渲染其他客户端广播的用户输入。"""
        self._renderer.render_user_message(str(params.get("message", "")))

    async def _handle_tool_started(self, params: dict[str, Any]) -> None:
        """渲染工具调用开始。"""
        self._renderer.render_tool_started(str(params.get("tool_name", "")))

    async def _handle_tool_finished(self, params: dict[str, Any]) -> None:
        """渲染工具调用结束。"""
        self._renderer.render_tool_finished(
            str(params.get("tool_name", "")), bool(params.get("is_error"))
        )

    async def _handle_interrupted(self, params: dict[str, Any]) -> None:
        """渲染任务被中断提示。"""
        self._renderer.render_interrupted(str(params.get("stop_reason", "")))

    async def _handle_plan_updated(self, params: dict[str, Any]) -> None:
        """渲染任务计划快照。"""
        tasks = params.get("tasks", [])
        if isinstance(tasks, list):
            self._renderer.render_plan(tasks)

    async def _process_line(self, line: str, conn: ClientConnection) -> bool:
        """处理一行输入；返回 False 表示退出。"""
        parsed = parse_command(line)
        if parsed is None:
            return await self._send_chat(line.strip(), conn)
        command, params = parsed
        try:
            if command == "quit":
                return False
            if command == "help":
                print(HELP_TEXT)
                return True
            if command == "stats":
                self._render_session_stats()
                return True
            if command == "session":
                print(f"会话 ID: {self._session_id}")
                return True
            if command == "unknown":
                print(f"未知命令: {params.get('command')}（输入 /help 查看帮助）")
                return True
            if command == "error":
                print(params.get("message"))
                return True
            if command == "ping":
                self._render_result(await conn.send_request(METHOD_PING))
                return True
            if command == "echo":
                self._render_result(await conn.send_request(METHOD_ECHO, params))
                return True
            return True
        except ConnectionError as exc:
            print(f"连接已断开: {exc}")
            return False

    async def _send_chat(self, message: str, conn: ClientConnection) -> bool:
        """发送 chat 请求；响应到达时已渲染完流式文本，随后展示摘要。"""
        if not message:
            return True
        try:
            resp = await conn.send_request(
                METHOD_CHAT, {"message": message, "session_id": self._session_id}
            )
        except ConnectionError as exc:
            print(f"连接已断开: {exc}")
            return False
        if "error" in resp:
            self._renderer.render_error(resp["error"])
        else:
            self._renderer.render_summary(resp["result"])
            self._accumulate_stats(resp["result"])
        return True

    def _render_result(self, resp: dict[str, Any]) -> None:
        """渲染普通 request-response 结果。"""
        if "error" in resp:
            self._renderer.render_error(resp["error"])
        else:
            print(json.dumps(resp["result"], ensure_ascii=False))

    def _accumulate_stats(self, result: dict[str, Any]) -> None:
        """累加会话 token 统计。"""
        usage = result.get("usage", {})
        self._session_stats["input_tokens"] += int(usage.get("input_tokens", 0))
        self._session_stats["output_tokens"] += int(usage.get("output_tokens", 0))

    def _render_session_stats(self) -> None:
        """显示会话累计 token 用量。"""
        print(
            f"📊 会话累计 tokens: {self._session_stats['input_tokens']} in / "
            f"{self._session_stats['output_tokens']} out"
        )
