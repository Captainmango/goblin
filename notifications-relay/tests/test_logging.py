import json
import logging

import pytest
import yaml

from goblin_common.logging import JSONFormatter, configure_logging
from notifications_relay.config import get_config, reset_config


@pytest.fixture(autouse=True)
def _reset_notifications_relay_logger():
    """Capture and restore notifications_relay logger state between tests."""
    logger = logging.getLogger("notifications_relay")
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
            name="notifications_relay.test",
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
        assert parsed["logger"] == "notifications_relay.test"
        assert parsed["message"] == "hello world"
        assert "timestamp" in parsed

    def test_extra_fields_promoted(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="notifications_relay.test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="send failed",
            args=None,
            exc_info=None,
        )
        record.event = "send.failed"
        record.token = "abc123"
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["event"] == "send.failed"
        assert parsed["token"] == "abc123"

    def test_non_serializable_extra_coerced(self):
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="notifications_relay.test",
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
            name="notifications_relay.test",
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
        configure_logging("notifications_relay", log_file=log_file)
        logger = logging.getLogger("notifications_relay")
        handler_types = [type(h) for h in logger.handlers]
        assert logging.FileHandler in handler_types
        assert logging.StreamHandler in handler_types
        file_handler = next(h for h in logger.handlers if isinstance(h, logging.FileHandler))
        assert isinstance(file_handler.formatter, JSONFormatter)

    def test_custom_log_file(self, tmp_path):
        log_file = str(tmp_path / "custom.jsonl")
        configure_logging("notifications_relay", log_file=log_file)
        logger = logging.getLogger("notifications_relay")
        file_handler = next(h for h in logger.handlers if isinstance(h, logging.FileHandler))
        assert file_handler.baseFilename == log_file

    def test_custom_log_level(self, tmp_path):
        configure_logging(
            "notifications_relay",
            log_level="DEBUG",
            log_file=str(tmp_path / "test.jsonl"),
        )
        logger = logging.getLogger("notifications_relay")
        assert logger.level == logging.DEBUG

    def test_quiet_suppresses_stderr(self, tmp_path):
        configure_logging(
            "notifications_relay",
            quiet=True,
            log_file=str(tmp_path / "test.jsonl"),
        )
        logger = logging.getLogger("notifications_relay")
        assert all(isinstance(h, logging.FileHandler) for h in logger.handlers)

    def test_yaml_config(self, tmp_path):
        config = {
            "version": 1,
            "disable_existing_loggers": False,
            "loggers": {
                "notifications_relay": {
                    "level": "DEBUG",
                    "handlers": [],
                },
            },
        }
        config_path = tmp_path / "logging.yaml"
        config_path.write_text(yaml.dump(config))
        configure_logging("notifications_relay", config_path=str(config_path))
        logger = logging.getLogger("notifications_relay")
        assert logger.level == logging.DEBUG

    def test_env_var_fallback(self, tmp_path, monkeypatch):
        reset_config()
        config = {
            "version": 1,
            "disable_existing_loggers": False,
            "loggers": {
                "notifications_relay": {
                    "level": "ERROR",
                    "handlers": [],
                },
            },
        }
        config_path = tmp_path / "logging.yaml"
        config_path.write_text(yaml.dump(config))
        monkeypatch.setenv("RELAY_LOG_CONFIG", str(config_path))
        # Simulate what the CLI does: pass env var as config_path
        configure_logging("notifications_relay", config_path=get_config().log_config)
        logger = logging.getLogger("notifications_relay")
        assert logger.level == logging.ERROR
