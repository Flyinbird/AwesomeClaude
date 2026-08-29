"""CLI 命令定义与解析。"""

from typing import Any


def parse_command(line: str) -> tuple[str, dict[str, Any]] | None:
    """解析一行输入。

    Args:
        line: 用户输入行。

    Returns:
        斜杠命令返回 (command, params)；普通文本（chat 输入）返回 None。
        command 取值：ping / echo / quit / stats / help / unknown / error。
    """
    text = line.strip()
    if not text.startswith("/"):
        return None
    parts = text.split(maxsplit=1)
    command = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""
    if command == "/ping":
        return ("ping", {})
    if command == "/echo":
        if not rest:
            return ("error", {"message": "用法: /echo <text>"})
        return ("echo", {"message": rest})
    if command in ("/quit", "/exit"):
        return ("quit", {})
    if command == "/stats":
        return ("stats", {})
    if command == "/session":
        return ("session", {})
    if command == "/help":
        return ("help", {})
    return ("unknown", {"command": text})
