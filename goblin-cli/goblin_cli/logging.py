from __future__ import annotations

import json
import logging
import logging.config
import sys
from datetime import datetime, timezone

import yaml


class JSONFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    _BUILTIN_ATTRS = frozenset(
        {
            "args",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "message",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "taskName",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        log_dict: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key not in self._BUILTIN_ATTRS and key not in log_dict:
                try:
                    json.dumps(value)
                    log_dict[key] = value
                except (TypeError, ValueError):
                    log_dict[key] = str(value)

        if record.exc_info and record.exc_info[1] is not None:
            log_dict["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_dict, default=str)


class BlueprintLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        kwargs.setdefault("extra", {}).update(self.extra)
        return msg, kwargs


def configure_logging(
    config_path: str | None = None,
    log_level: str | None = None,
    log_file: str | None = None,
    quiet: bool = False,
) -> None:
    if config_path:
        with open(config_path) as f:
            config = yaml.safe_load(f)
        logging.config.dictConfig(config)
        return

    logger = logging.getLogger("goblin_cli")
    logger.setLevel(getattr(logging, log_level or "INFO"))
    logger.propagate = False

    # Remove existing handlers to avoid duplicates on repeated calls
    logger.handlers.clear()

    file_handler = logging.FileHandler(log_file or "goblin-run.jsonl")
    file_handler.setFormatter(JSONFormatter())
    logger.addHandler(file_handler)

    if not quiet:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.WARNING)
        stderr_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(stderr_handler)
