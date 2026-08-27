"""命令行入口与交互循环（REPL）。"""

import argparse
import asyncio
import json
from typing import Any

from awesome_claude.client.connection import ClientConnection
from awesome_claude.protocol.methods import METHOD_ECHO, METHOD_PING, METHOD_SHUTDOWN
from awesome_claude.shared.logger import setup_logging

WELCOME_TEXT = "已连接到 AwesomeClaude core server。输入 /help 查看命令，/quit 退出。"

HELP_TEXT = """可用命令:
  /ping            健康检查
  /echo <message>  回显测试
  /help            显示本帮助
  /quit, /exit     发送 shutdown 通知并退出
其他任意文本将作为 echo 请求发送（Phase 2 将改为对话）。"""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。

    Args:
        argv: 参数列表，缺省时使用 sys.argv[1:]。

    Returns:
        解析后的参数命名空间。
    """
    parser = argparse.ArgumentParser(
        prog="awesome-claude-client",
        description="AwesomeClaude 命令行客户端",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="core server 地址（默认 127.0.0.1）"
    )
    parser.add_argument(
        "--port", type=int, default=9527, help="core server 端口（默认 9527）"
    )
    return parser.parse_args(argv)


def format_response(resp: dict[str, Any]) -> str:
    """将响应字典格式化为可读文本。

    Args:
        resp: 服务端返回的响应字典。

    Returns:
        格式化后的文本。
    """
    if "error" in resp:
        error = resp["error"]
        text = f"错误 {error['code']}: {error['message']}"
        if "data" in error:
            text += f" data={error['data']}"
        return text
    return json.dumps(resp.get("result"), ensure_ascii=False)


async def dispatch_command(conn: ClientConnection, line: str) -> str:
    """执行单条命令并返回要显示的内容。

    Args:
        conn: 客户端连接。
        line: 去除首尾空白后的命令文本。

    Returns:
        要显示的内容。

    Raises:
        ConnectionError: 连接断开。
    """
    if line == "/ping":
        return format_response(await conn.send_request(METHOD_PING))
    if line.startswith("/echo"):
        message = line[len("/echo") :].strip()
        if not message:
            return "用法: /echo <message>"
        return format_response(
            await conn.send_request(METHOD_ECHO, {"message": message})
        )
    if line == "/help":
        return HELP_TEXT
    if line.startswith("/"):
        return f"未知命令: {line}"
    return format_response(await conn.send_request(METHOD_ECHO, {"message": line}))


async def process_line(conn: ClientConnection, line: str) -> bool:
    """处理一行输入。

    Args:
        conn: 客户端连接。
        line: 原始输入行。

    Returns:
        True 表示继续循环，False 表示退出。
    """
    text = line.strip()
    if not text:
        return True
    if text in ("/quit", "/exit"):
        await conn.send_notification(METHOD_SHUTDOWN)
        return False
    print(await dispatch_command(conn, text))
    return True


async def run_repl(conn: ClientConnection) -> None:
    """运行交互式 REPL 循环。

    Args:
        conn: 已连接的客户端连接。
    """
    print(WELCOME_TEXT)
    while True:
        try:
            line = await asyncio.to_thread(input, "> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        try:
            if not await process_line(conn, line):
                break
        except (ConnectionError, OSError) as exc:
            print(f"连接已断开: {exc}")
            break


async def async_main() -> None:
    """异步主流程：解析参数 → 连接 → 运行 REPL。"""
    args = parse_args()
    setup_logging("WARNING")
    conn = ClientConnection()
    try:
        await conn.connect(args.host, args.port)
    except (OSError, ConnectionError) as exc:
        print(f"无法连接到 core server {args.host}:{args.port}: {exc}")
        return
    try:
        await run_repl(conn)
    finally:
        await conn.close()


def main() -> None:
    """命令行入口。"""
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
