"""CLI 命令行入口（python -m awesome_claude.client.cli）。"""

import argparse
import asyncio

from awesome_claude.client.cli.app import CLIApp


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


def main() -> None:
    """命令行入口。"""
    args = parse_args()
    asyncio.run(CLIApp(args.host, args.port).run())


if __name__ == "__main__":
    main()
