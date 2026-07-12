from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from notifications_relay.config import get_config, load_config, reset_config


def _clear_relay_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset relay-specific environment variables so dotenv values do not leak."""
    for name in (
        "RELAY_API_KEY",
        "RELAY_HOST",
        "RELAY_PORT",
        "RELAY_DB_PATH",
        "RELAY_PAIRING_TOKEN_TTL_SECONDS",
        "RELAY_QUESTION_MAPPING_TTL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_api_key_required(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_relay_env(monkeypatch)
    monkeypatch.setenv("RELAY_API_KEY", "")
    reset_config()
    with pytest.raises(ValidationError):
        load_config()


def test_defaults_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_relay_env(monkeypatch)
    monkeypatch.setenv("RELAY_API_KEY", "secret")
    reset_config()
    config = load_config()
    assert config.api_key.get_secret_value() == "secret"
    assert config.host == "127.0.0.1"
    assert config.port == 8000
    assert str(config.db_path) == ".goblin/relay.db"
    assert config.pairing_token_ttl_seconds == 900
    assert config.question_mapping_ttl_seconds == 3600


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_relay_env(monkeypatch)
    monkeypatch.setenv("RELAY_API_KEY", "secret")
    monkeypatch.setenv("RELAY_HOST", "0.0.0.0")
    monkeypatch.setenv("RELAY_PORT", "9000")
    monkeypatch.setenv("RELAY_DB_PATH", "/tmp/relay.db")
    monkeypatch.setenv("RELAY_PAIRING_TOKEN_TTL_SECONDS", "60")
    monkeypatch.setenv("RELAY_QUESTION_MAPPING_TTL_SECONDS", "120")
    reset_config()
    config = load_config()
    assert config.host == "0.0.0.0"
    assert config.port == 9000
    assert str(config.db_path) == "/tmp/relay.db"
    assert config.pairing_token_ttl_seconds == 60
    assert config.question_mapping_ttl_seconds == 120


def test_invalid_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_relay_env(monkeypatch)
    monkeypatch.setenv("RELAY_API_KEY", "secret")
    monkeypatch.setenv("RELAY_PAIRING_TOKEN_TTL_SECONDS", "not-a-number")
    reset_config()
    with pytest.raises(ValueError):
        load_config()


def test_loads_dotenv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_relay_env(monkeypatch)
    env_file = tmp_path / ".env"
    env_file.write_text("RELAY_API_KEY=from-dotenv\nRELAY_HOST=dotenv-host\nRELAY_PORT=7000\n")
    monkeypatch.chdir(tmp_path)
    reset_config()
    config = load_config()
    assert config.api_key.get_secret_value() == "from-dotenv"
    assert config.host == "dotenv-host"
    assert config.port == 7000


class TestSingleton:
    def test_get_config_is_lazy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_relay_env(monkeypatch)
        reset_config()
        monkeypatch.setenv("RELAY_API_KEY", "first")
        first = get_config()
        assert first.api_key.get_secret_value() == "first"

        monkeypatch.setenv("RELAY_API_KEY", "second")
        second = get_config()
        assert second is first
        assert second.api_key.get_secret_value() == "first"

    def test_reset_config_reloads(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_relay_env(monkeypatch)
        reset_config()
        monkeypatch.setenv("RELAY_API_KEY", "before")
        before = get_config()
        assert before.api_key.get_secret_value() == "before"

        monkeypatch.setenv("RELAY_API_KEY", "after")
        reset_config()
        after = get_config()
        assert after.api_key.get_secret_value() == "after"
