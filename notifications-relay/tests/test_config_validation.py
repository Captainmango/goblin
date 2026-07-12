from __future__ import annotations

import pytest
from pydantic import ValidationError

from notifications_relay.config import RelayConfig


def test_api_key_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RELAY_API_KEY", raising=False)
    with pytest.raises(ValidationError):
        RelayConfig.from_env()


def test_defaults_with_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAY_API_KEY", "secret")
    config = RelayConfig.from_env()
    assert config.api_key == "secret"
    assert config.host == "127.0.0.1"
    assert config.port == 8000
    assert str(config.db_path) == ".goblin/relay.db"
    assert config.pairing_token_ttl_seconds == 900
    assert config.question_mapping_ttl_seconds == 3600


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAY_API_KEY", "secret")
    monkeypatch.setenv("RELAY_HOST", "0.0.0.0")
    monkeypatch.setenv("RELAY_PORT", "9000")
    monkeypatch.setenv("RELAY_DB_PATH", "/tmp/relay.db")
    monkeypatch.setenv("RELAY_PAIRING_TOKEN_TTL_SECONDS", "60")
    monkeypatch.setenv("RELAY_QUESTION_MAPPING_TTL_SECONDS", "120")
    config = RelayConfig.from_env()
    assert config.host == "0.0.0.0"
    assert config.port == 9000
    assert str(config.db_path) == "/tmp/relay.db"
    assert config.pairing_token_ttl_seconds == 60
    assert config.question_mapping_ttl_seconds == 120


def test_invalid_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAY_API_KEY", "secret")
    monkeypatch.setenv("RELAY_PAIRING_TOKEN_TTL_SECONDS", "not-a-number")
    with pytest.raises(ValueError):
        RelayConfig.from_env()
