"""统一日志配置。"""

import logging
import sys

_ROOT_NAME = "awesome_claude"
_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """配置统一日志：向根 logger 挂载 stdout handler（幂等，可重复调用）。

    Args:
        level: 日志级别，如 "DEBUG" / "INFO" / "WARNING"。
    """
    root = logging.getLogger(_ROOT_NAME)
    root.setLevel(level.upper())
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT))
        root.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """获取统一前缀下的模块 logger。

    Args:
        name: 模块名（如 "core.server"）。

    Returns:
        配置好的 Logger 实例。
    """
    return logging.getLogger(f"{_ROOT_NAME}.{name}")
