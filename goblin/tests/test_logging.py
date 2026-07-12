import json
import logging

import pytest
import yaml

from goblin.logging import JSONFormatter, configure_logging
from goblin.utils.config import get_config, reset_config


@pytest.fixture(autouse=True)
def _reset_goblin_logger():
    """Capture and restore goblin logger state between tests."""
    logger = logging.getLogger("goblin")
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
            name="goblin.test",
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
        assert parsed["logger"] == "goblin.test"
        assert parsed["message"] == "hello world"
        assert "timestamp" in parsed

    def test_extra_fields_promoted(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="goblin.test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="phase failed",
            args=None,
            exc_info=None,
        )
        record.event = "phase.failed"
        record.phase_id = "s1"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["event"] == "phase.failed"
        assert parsed["phase_id"] == "s1"

    def test_non_serializable_extra_coerced(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="goblin.test",
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
            name="goblin.test",
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
        configure_logging(log_file=log_file)
        logger = logging.getLogger("goblin")
        handler_types = [type(h) for h in logger.handlers]
        assert logging.FileHandler in handler_types
        assert logging.StreamHandler in handler_types
        file_handler = next(h for h in logger.handlers if isinstance(h, logging.FileHandler))
        assert isinstance(file_handler.formatter, JSONFormatter)

    def test_custom_log_file(self, tmp_path):
        log_file = str(tmp_path / "custom.jsonl")
        configure_logging(log_file=log_file)
        logger = logging.getLogger("goblin")
        file_handler = next(h for h in logger.handlers if isinstance(h, logging.FileHandler))
        assert file_handler.baseFilename == log_file

    def test_custom_log_level(self, tmp_path):
        configure_logging(log_level="DEBUG", log_file=str(tmp_path / "test.jsonl"))
        logger = logging.getLogger("goblin")
        assert logger.level == logging.DEBUG

    def test_quiet_suppresses_stderr(self, tmp_path):
        configure_logging(quiet=True, log_file=str(tmp_path / "test.jsonl"))
        logger = logging.getLogger("goblin")
        handler_types = [type(h) for h in logger.handlers]
        assert logging.FileHandler in handler_types
        # Only FileHandler, no StreamHandler
        assert all(isinstance(h, logging.FileHandler) for h in logger.handlers)

    def test_yaml_config(self, tmp_path):
        config = {
            "version": 1,
            "disable_existing_loggers": False,
            "loggers": {
                "goblin": {
                    "level": "DEBUG",
                    "handlers": [],
                },
            },
        }
        config_path = tmp_path / "logging.yaml"
        config_path.write_text(yaml.dump(config))
        configure_logging(config_path=str(config_path))
        logger = logging.getLogger("goblin")
        assert logger.level == logging.DEBUG

    def test_env_var_fallback(self, tmp_path, monkeypatch):
        reset_config()
        config = {
            "version": 1,
            "disable_existing_loggers": False,
            "loggers": {
                "goblin": {
                    "level": "ERROR",
                    "handlers": [],
                },
            },
        }
        config_path = tmp_path / "logging.yaml"
        config_path.write_text(yaml.dump(config))
        monkeypatch.setenv("GOBLIN_LOG_CONFIG", str(config_path))
        # Simulate what the CLI does: pass env var as config_path
        configure_logging(config_path=get_config().log_config)
        logger = logging.getLogger("goblin")
        assert logger.level == logging.ERROR
