"""CLI 应用入口 - 交互循环主流程。"""

import asyncio
import json
import sys
import time
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
    NOTIFY_CHAT_COMPLETED,
    NOTIFY_CHAT_FAILED,
    NOTIFY_CHAT_HEARTBEAT,
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
        self._awaiting_run_id: str | None = None
        self._pending_terminal: tuple[str, dict[str, Any]] | None = None
        self._completion_event: asyncio.Event | None = None
        self._heartbeat_interval_ms = 15000
        self._logger = get_app_logger("client.cli")

    async def run(self) -> None:
        """主循环：连接 → 注册通知 → attach 会话 → REPL。"""
        self._completion_event = asyncio.Event()
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
        """注册各类通知处理器与连接关闭回调。"""
        conn.on_notification(NOTIFY_CHAT_STREAM, self._handle_stream_notification)
        conn.on_notification(NOTIFY_CHAT_COMPLETED, self._handle_completed)
        conn.on_notification(NOTIFY_CHAT_FAILED, self._handle_failed)
        conn.on_notification(NOTIFY_CHAT_HEARTBEAT, self._handle_heartbeat)
        conn.on_notification(NOTIFY_CHAT_USER_MESSAGE, self._handle_user_message)
        conn.on_notification(NOTIFY_CHAT_TOOL_STARTED, self._handle_tool_started)
        conn.on_notification(NOTIFY_CHAT_TOOL_FINISHED, self._handle_tool_finished)
        conn.on_notification(NOTIFY_CHAT_INTERRUPTED, self._handle_interrupted)
        conn.on_notification(NOTIFY_CHAT_PLAN_UPDATED, self._handle_plan_updated)
        conn.on_disconnect(self._handle_disconnect)

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
        """处理 chat.stream 通知并渲染流式文本（终态由完成通知决定）。"""
        if not params.get("is_final"):
            self._renderer.render_chunk(str(params.get("text", "")))
        else:
            self._renderer.render_done()

    async def _handle_completed(self, params: dict[str, Any]) -> None:
        """处理 chat.completed：渲染摘要并结束本轮等待。"""
        run_id = params.get("run_id")
        if self._awaiting_run_id is None:
            self._pending_terminal = (NOTIFY_CHAT_COMPLETED, params)
            return
        if run_id != self._awaiting_run_id:
            return
        self._renderer.render_summary(params)
        self._accumulate_stats(params)
        self._finish_run()

    async def _handle_failed(self, params: dict[str, Any]) -> None:
        """处理 chat.failed：渲染错误并结束本轮等待。"""
        run_id = params.get("run_id")
        if self._awaiting_run_id is None:
            self._pending_terminal = (NOTIFY_CHAT_FAILED, params)
            return
        if run_id != self._awaiting_run_id:
            return
        error = params.get("error")
        if isinstance(error, dict):
            self._renderer.render_error(error)
        else:
            print(f"对话失败: {error}")
        self._finish_run()

    async def _handle_heartbeat(self, params: dict[str, Any]) -> None:
        """处理 chat.heartbeat：入站活动已由接收器记录，无需额外渲染。"""
        self._logger.debug("chat heartbeat", run_id=params.get("run_id"))

    async def _handle_disconnect(self) -> None:
        """服务端关闭连接：结束在途等待并提示。"""
        if self._awaiting_run_id is not None:
            print("\n⚠️ 与服务端的连接已关闭", file=sys.stderr)
            self._finish_run()

    def _finish_run(self) -> None:
        """标记当前对话等待结束。"""
        if self._completion_event is not None:
            self._completion_event.set()

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
        """发起 chat 并等待终态通知；受理后不再对对话施加总时长限制。"""
        if not message:
            return True
        assert self._completion_event is not None
        self._completion_event.clear()
        self._pending_terminal = None
        self._awaiting_run_id = None
        try:
            resp = await conn.send_request(
                METHOD_CHAT, {"message": message, "session_id": self._session_id}
            )
        except ConnectionError as exc:
            print(f"连接已断开: {exc}")
            return False
        if "error" in resp:
            self._renderer.render_error(resp["error"])
            return True

        result = resp.get("result", {})
        run_id = str(result.get("run_id", ""))
        self._heartbeat_interval_ms = max(
            int(result.get("heartbeat_interval_ms", 15000)), 1
        )
        self._awaiting_run_id = run_id

        if self._pending_terminal is not None:
            method, params = self._pending_terminal
            self._pending_terminal = None
            if method == NOTIFY_CHAT_COMPLETED:
                await self._handle_completed(params)
            else:
                await self._handle_failed(params)

        await self._wait_for_completion(conn)
        self._awaiting_run_id = None
        self._pending_terminal = None
        return True

    async def _wait_for_completion(self, conn: ClientConnection) -> None:
        """等待终态通知，并以心跳看门狗监测连接存活。"""
        assert self._completion_event is not None
        watchdog = asyncio.create_task(self._watch_connection(conn))
        try:
            await self._completion_event.wait()
        finally:
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)

    async def _watch_connection(self, conn: ClientConnection) -> None:
        """超过 3× 心跳间隔未收到任何入站消息时提示连接疑似中断。"""
        interval = max(self._heartbeat_interval_ms / 1000.0, 0.1)
        threshold = interval * 3
        while True:
            await asyncio.sleep(interval)
            last = conn.last_activity
            if last is None or (time.monotonic() - last) >= threshold:
                print("\n⚠️ 连接疑似中断（长时间未收到服务端数据）", file=sys.stderr)
                self._finish_run()
                return

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
