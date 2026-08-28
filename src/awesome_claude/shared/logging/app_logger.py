"""应用日志器 - structlog 配置（stdout 彩色文本 + 文件 JSON）。"""

import logging
import sys
from pathlib import Path

import structlog
from structlog.typing import Processor

_added_handlers: list[logging.Handler] = []


def setup_app_logging(
    log_path: Path, level: str = "INFO", *, colors: bool = True
) -> None:
    """配置应用日志：stdout 彩色文本 + 文件 JSON。

    重复调用会移除并关闭先前添加的 handler，以最后一次调用为准。

    Args:
        log_path: 日志文件路径（如 logs/server.log）。
        level: 日志级别（INFO / DEBUG / WARNING 等）。
        colors: stdout 是否使用 ANSI 彩色。
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(level.upper())

    for handler in _added_handlers:
        root.removeHandler(handler)
        handler.close()
    _added_handlers.clear()

    pre_chain: list[Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
    ]

    json_handler = logging.FileHandler(log_path, encoding="utf-8")
    json_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=pre_chain,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.JSONRenderer(ensure_ascii=False),
            ],
        )
    )
    root.addHandler(json_handler)
    _added_handlers.append(json_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=pre_chain,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=colors),
            ],
        )
    )
    root.addHandler(console_handler)
    _added_handlers.append(console_handler)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.stdlib.add_logger_name,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_app_logger(name: str) -> structlog.stdlib.BoundLogger:
    """获取应用 logger（使用前需先调用 setup_app_logging）。

    Args:
        name: logger 名称（如 "core.app"）。

    Returns:
        绑定名称的 structlog logger。
    """
    return structlog.get_logger(name)
