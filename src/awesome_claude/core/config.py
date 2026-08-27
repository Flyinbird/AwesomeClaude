"""服务端配置（host, port, 日志等）。"""

import os
from dataclasses import dataclass

ENV_PREFIX = "AWESOME_CLAUDE_"


@dataclass(frozen=True, slots=True)
class ServerConfig:
    """服务端配置。"""

    host: str = "127.0.0.1"
    port: int = 9527
    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "ServerConfig":
        """从环境变量读取配置（前缀 AWESOME_CLAUDE_）。

        支持的环境变量：AWESOME_CLAUDE_HOST / AWESOME_CLAUDE_PORT / AWESOME_CLAUDE_LOG_LEVEL。
        未设置时使用默认值；PORT 非法时抛出 ValueError。

        Returns:
            从环境变量构建的 ServerConfig。
        """
        defaults = cls()
        host = os.getenv(f"{ENV_PREFIX}HOST", defaults.host)
        port_raw = os.getenv(f"{ENV_PREFIX}PORT")
        log_level = os.getenv(f"{ENV_PREFIX}LOG_LEVEL", defaults.log_level)
        port = int(port_raw) if port_raw is not None else defaults.port
        return cls(host=host, port=port, log_level=log_level)
