from __future__ import annotations

import logging
import logging.handlers
import os
import sys

import structlog


def configure_logging(
    level: str | None = None,
    log_file: str | None = None,
    console: bool | None = None,
) -> None:
    level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    log_file = log_file or os.environ.get("LOG_FILE", "data/redlib-api.log")
    if console is None:
        console = os.environ.get("LOG_CONSOLE", "false").lower() == "true"

    int_level = getattr(logging, level, logging.INFO)

    # Processors for structlog-native calls
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.stdlib.ExtraAdder(),
    ]

    structlog.configure(
        processors=shared_processors + [
            structlog.processors.ExceptionRenderer(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(int_level),
        cache_logger_on_first_use=True,
    )

    # foreign_pre_chain handles stdlib records (e.g. from third-party libs)
    foreign_pre_chain: list[structlog.types.Processor] = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.ExceptionRenderer(),
    ]

    file_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=foreign_pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    root = logging.getLogger()
    root.setLevel(int_level)
    root.handlers.clear()

    # httpx/httpcore are very chatty at DEBUG/INFO — keep them at WARNING
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(file_formatter)
    root.addHandler(file_handler)

    if console:
        console_formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=foreign_pre_chain,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(colors=True),
            ],
        )
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(console_formatter)
        root.addHandler(stream_handler)
