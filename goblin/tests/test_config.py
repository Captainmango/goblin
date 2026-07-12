"""Tests for the central GoblinConfig dataclass."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from goblin.utils.config import get_config, load_config, reset_config


class TestModelApiKey:
    def test_prefers_goblin_prefixed_var(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "prefixed")
        monkeypatch.setenv("MODEL_API_KEY", "legacy")
        config = load_config()
        assert isinstance(config.model_api_key, SecretStr)
        assert config.model_api_key.get_secret_value() == "prefixed"

    def test_falls_back_to_legacy_var(self, monkeypatch):
        monkeypatch.delenv("GOBLIN_MODEL_API_KEY", raising=False)
        monkeypatch.setenv("MODEL_API_KEY", "legacy")
        config = load_config()
        assert config.model_api_key is not None
        assert config.model_api_key.get_secret_value() == "legacy"

    def test_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("GOBLIN_MODEL_API_KEY", raising=False)
        monkeypatch.delenv("MODEL_API_KEY", raising=False)
        config = load_config()
        with pytest.raises(RuntimeError, match="GOBLIN_MODEL_API_KEY"):
            config.require_model_api_key()


class TestBaseUrl:
    def test_prefers_goblin_prefixed_var(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_BASE_URL", "https://new.example.com")
        monkeypatch.setenv("OPENHANDS_BASE_URL", "https://old.example.com")
        config = load_config()
        assert config.base_url == "https://new.example.com"

    def test_falls_back_to_legacy_var(self, monkeypatch):
        monkeypatch.delenv("GOBLIN_BASE_URL", raising=False)
        monkeypatch.setenv("OPENHANDS_BASE_URL", "https://old.example.com")
        config = load_config()
        assert config.base_url == "https://old.example.com"

    def test_missing_value_is_none(self, monkeypatch):
        monkeypatch.delenv("GOBLIN_BASE_URL", raising=False)
        monkeypatch.delenv("OPENHANDS_BASE_URL", raising=False)
        config = load_config()
        assert config.base_url is None


class TestSingleton:
    def test_get_config_is_lazy(self, monkeypatch):
        reset_config()
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "first")
        first = get_config()
        assert first.model_api_key is not None
        assert first.model_api_key.get_secret_value() == "first"

        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "second")
        second = get_config()
        assert second is first
        assert second.model_api_key.get_secret_value() == "first"

    def test_reset_config_reloads(self, monkeypatch):
        reset_config()
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "before")
        before = get_config()
        assert before.model_api_key.get_secret_value() == "before"

        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "after")
        reset_config()
        after = get_config()
        assert after.model_api_key.get_secret_value() == "after"
