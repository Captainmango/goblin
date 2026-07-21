from __future__ import annotations

import json
import logging
import logging.config
import sys
from datetime import UTC, datetime
from pathlib import Path

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
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
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


def _find_project_root() -> Path:
    """Return the workspace root by locating the workspace ``pyproject.toml``.

    Falls back to the current working directory when the workspace root cannot
    be determined.
    """
    start = Path(__file__).resolve()
    for parent in start.parents:
        marker = parent / "pyproject.toml"
        if marker.exists() and "[tool.uv.workspace]" in marker.read_text():
            return parent
    return Path.cwd()


def configure_logging(
    logger_name: str,
    config_path: str | None = None,
    log_level: str | None = None,
    log_file: str | None = None,
    quiet: bool = False,
) -> None:
    """Configure JSON logging for ``logger_name``.

    If ``config_path`` is provided, it is loaded as a YAML logging dict config
    and all other arguments are ignored. Otherwise, a ``JSONFormatter`` file
    handler is attached to ``logger_name`` and a plain-text ``StreamHandler``
    is attached to ``stderr`` unless ``quiet`` is set.

    When ``log_file`` is omitted, logs are written to
    ``logs/<logger_name>.jsonl`` under the project root.
    """
    if config_path:
        with open(config_path) as f:
            config = yaml.safe_load(f)
        logging.config.dictConfig(config)
        return

    logger = logging.getLogger(logger_name)
    logger.setLevel(getattr(logging, log_level or "INFO"))
    logger.propagate = False

    # Remove existing handlers to avoid duplicates on repeated calls
    logger.handlers.clear()

    if log_file is None:
        log_dir = _find_project_root() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = str(log_dir / f"{logger_name}.jsonl")

    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(JSONFormatter())
    logger.addHandler(file_handler)

    if not quiet:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setLevel(logging.WARNING)
        stderr_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(stderr_handler)
