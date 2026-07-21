from __future__ import annotations

import json
import logging
from unittest.mock import patch

import pytest
import yaml

from goblin_common.logging import JSONFormatter, configure_logging


@pytest.fixture(autouse=True)
def _reset_test_logger():
    """Capture and restore the test logger state between tests."""
    logger = logging.getLogger("goblin_common_test")
    original_handlers = logger.handlers[:]
    original_level = logger.level
    original_propagate = logger.propagate
    yield
    logger.handlers = original_handlers
    logger.level = original_level
    logger.propagate = original_propagate


class TestJSONFormatter:
    def test_basic_format(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="hello world",
            args=None,
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["logger"] == "test"
        assert parsed["message"] == "hello world"
        assert "timestamp" in parsed

    def test_extra_fields_promoted(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="event happened",
            args=None,
            exc_info=None,
        )
        record.event = "event.name"
        record.context_id = "ctx-1"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["event"] == "event.name"
        assert parsed["context_id"] == "ctx-1"

    def test_non_serializable_extra_coerced(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test",
            args=None,
            exc_info=None,
        )
        record.bad_value = object()
        output = formatter.format(record)
        parsed = json.loads(output)
        assert isinstance(parsed["bad_value"], str)

    def test_exception_included(self):
        formatter = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="error",
            args=None,
            exc_info=exc_info,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert "exception" in parsed
        assert "boom" in parsed["exception"]


class TestConfigureLogging:
    def test_default_config(self, tmp_path):
        log_file = str(tmp_path / "test.jsonl")
        configure_logging("goblin_common_test", log_file=log_file)
        logger = logging.getLogger("goblin_common_test")
        handler_types = [type(h) for h in logger.handlers]
        assert logging.FileHandler in handler_types
        assert logging.StreamHandler in handler_types
        file_handler = next(h for h in logger.handlers if isinstance(h, logging.FileHandler))
        assert isinstance(file_handler.formatter, JSONFormatter)

    def test_custom_log_file(self, tmp_path):
        log_file = str(tmp_path / "custom.jsonl")
        configure_logging("goblin_common_test", log_file=log_file)
        logger = logging.getLogger("goblin_common_test")
        file_handler = next(h for h in logger.handlers if isinstance(h, logging.FileHandler))
        assert file_handler.baseFilename == log_file

    def test_default_log_file_uses_logger_name(self, tmp_path):
        with patch("goblin_common.logging._find_project_root", return_value=tmp_path):
            configure_logging("goblin_common_test")
            logger = logging.getLogger("goblin_common_test")
            file_handler = next(h for h in logger.handlers if isinstance(h, logging.FileHandler))
            assert file_handler.baseFilename == str(tmp_path / "logs" / "goblin_common_test.jsonl")
            assert (tmp_path / "logs").is_dir()

    def test_custom_log_level(self, tmp_path):
        configure_logging("goblin_common_test", log_level="DEBUG", log_file=str(tmp_path / "test.jsonl"))
        logger = logging.getLogger("goblin_common_test")
        assert logger.level == logging.DEBUG

    def test_quiet_suppresses_stderr(self, tmp_path):
        configure_logging("goblin_common_test", quiet=True, log_file=str(tmp_path / "test.jsonl"))
        logger = logging.getLogger("goblin_common_test")
        assert all(isinstance(h, logging.FileHandler) for h in logger.handlers)

    def test_yaml_config(self, tmp_path):
        config = {
            "version": 1,
            "disable_existing_loggers": False,
            "loggers": {
                "goblin_common_test": {
                    "level": "DEBUG",
                    "handlers": [],
                },
            },
        }
        config_path = tmp_path / "logging.yaml"
        config_path.write_text(yaml.dump(config))
        configure_logging("goblin_common_test", config_path=str(config_path))
        logger = logging.getLogger("goblin_common_test")
        assert logger.level == logging.DEBUG
