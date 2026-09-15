"""Structured JSONL logs with UTC timestamps and exception tracebacks."""

import logging
from pathlib import Path

import structlog

from src.core.time_utils import now, to_utc_iso


def _timestamp(logger, method_name, event_dict):
    event_dict["at"] = to_utc_iso(now())
    return event_dict


def configure_logging(path: Path | None = None, *, level: int = logging.INFO) -> None:
    """Configure application logging once at the entry point.

    A supplied file always receives DEBUG and higher events, independently of
    console verbosity. Importing storage never changes the caller's handlers.
    """
    shared = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        _timestamp,
        structlog.processors.format_exc_info,
    ]
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(ensure_ascii=False),
        ],
    )
    logger = logging.getLogger("blogai")
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    logger.addHandler(console)
    if path is not None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )
