"""服务端配置（host, port, model, api_key, 日志等）。"""

import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """服务端配置。"""

    api_key: str
    host: str = "127.0.0.1"
    port: int = 9527
    model: str = "claude-sonnet-4-20250514"
    base_url: str | None = None
    max_tokens: int = 4096
    log_level: str = "INFO"
    log_dir: str = "logs"

    @classmethod
    def from_env(cls) -> "ServerConfig":
        """从环境变量读取配置（兼容接口，等价于 load_server_config）。"""
        return load_server_config()


def load_server_config() -> ServerConfig:
    """加载 .env 并从环境变量构建服务端配置。

    支持的环境变量：
        ANTHROPIC_API_KEY（必需）、AWESOME_CLAUDE_HOST、AWESOME_CLAUDE_PORT、
        AWESOME_CLAUDE_MODEL、AWESOME_CLAUDE_BASE_URL（可选，如 DeepSeek 的
        https://api.deepseek.com/anthropic）、AWESOME_CLAUDE_MAX_TOKENS、
        AWESOME_CLAUDE_LOG_LEVEL、AWESOME_CLAUDE_LOG_DIR。

    Returns:
        服务端配置。

    Raises:
        ValueError: 缺少必需的 ANTHROPIC_API_KEY，或 PORT/MAX_TOKENS 不是整数。
    """
    load_dotenv()

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("缺少必需的环境变量 ANTHROPIC_API_KEY")

    return ServerConfig(
        api_key=api_key,
        host=os.getenv("AWESOME_CLAUDE_HOST", "127.0.0.1"),
        port=_env_int("AWESOME_CLAUDE_PORT", 9527),
        model=os.getenv("AWESOME_CLAUDE_MODEL", "claude-sonnet-4-20250514"),
        base_url=os.getenv("AWESOME_CLAUDE_BASE_URL") or None,
        max_tokens=_env_int("AWESOME_CLAUDE_MAX_TOKENS", 4096),
        log_level=os.getenv("AWESOME_CLAUDE_LOG_LEVEL", "INFO"),
        log_dir=os.getenv("AWESOME_CLAUDE_LOG_DIR", "logs"),
    )


def _env_int(name: str, default: int) -> int:
    """读取整数环境变量，非法时抛出 ValueError。"""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"环境变量 {name} 必须是整数: {raw!r}") from None
