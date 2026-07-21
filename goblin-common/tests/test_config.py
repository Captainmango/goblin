from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import SecretStr

from goblin_common.config import env, load_dotenv_from_cwd, secret_env


class TestEnv:
    def test_returns_default_when_missing(self, monkeypatch):
        monkeypatch.delenv("GC_TEST_VAR", raising=False)
        assert env("GC_TEST_VAR", "default") == "default"
        assert env("GC_TEST_VAR") is None

    def test_returns_string_value(self, monkeypatch):
        monkeypatch.setenv("GC_TEST_VAR", "value")
        assert env("GC_TEST_VAR", "default") == "value"

    def test_coerces_to_int(self, monkeypatch):
        monkeypatch.setenv("GC_TEST_VAR", "42")
        assert env("GC_TEST_VAR", 0) == 42
        assert isinstance(env("GC_TEST_VAR", 0), int)

    def test_coerces_to_bool(self, monkeypatch):
        monkeypatch.setenv("GC_TEST_VAR", "true")
        assert env("GC_TEST_VAR", False) is True

        monkeypatch.setenv("GC_TEST_VAR", "False")
        assert env("GC_TEST_VAR", False) is False

        monkeypatch.setenv("GC_TEST_VAR", "1")
        assert env("GC_TEST_VAR", False) is True

        monkeypatch.setenv("GC_TEST_VAR", "0")
        assert env("GC_TEST_VAR", False) is False

    def test_uses_fallback(self, monkeypatch):
        monkeypatch.delenv("GC_PRIMARY", raising=False)
        monkeypatch.setenv("GC_FALLBACK", "fallback-value")
        assert env("GC_PRIMARY", "default", fallback="GC_FALLBACK") == "fallback-value"

    def test_prefers_primary_over_fallback(self, monkeypatch):
        monkeypatch.setenv("GC_PRIMARY", "primary-value")
        monkeypatch.setenv("GC_FALLBACK", "fallback-value")
        assert env("GC_PRIMARY", "default", fallback="GC_FALLBACK") == "primary-value"

    def test_invalid_int_raises(self, monkeypatch):
        monkeypatch.setenv("GC_TEST_VAR", "not-a-number")
        with pytest.raises(ValueError):
            env("GC_TEST_VAR", 0)


class TestSecretEnv:
    def test_returns_secret(self, monkeypatch):
        monkeypatch.setenv("GC_SECRET", "shh")
        value = secret_env("GC_SECRET")
        assert isinstance(value, SecretStr)
        assert value.get_secret_value() == "shh"

    def test_returns_none_when_missing(self, monkeypatch):
        monkeypatch.delenv("GC_SECRET", raising=False)
        assert secret_env("GC_SECRET") is None

    def test_uses_fallback(self, monkeypatch):
        monkeypatch.delenv("GC_PRIMARY_SECRET", raising=False)
        monkeypatch.setenv("GC_FALLBACK_SECRET", "fallback-secret")
        value = secret_env("GC_PRIMARY_SECRET", fallback="GC_FALLBACK_SECRET")
        assert value is not None
        assert value.get_secret_value() == "fallback-secret"


class TestLoadDotenvFromCwd:
    def test_loads_from_cwd(self, monkeypatch, tmp_path: Path):
        monkeypatch.delenv("GC_DOTENV_VAR", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("GC_DOTENV_VAR=from-cwd\n")
        monkeypatch.chdir(tmp_path)
        load_dotenv_from_cwd()
        assert os.environ.get("GC_DOTENV_VAR") == "from-cwd"

    def test_does_not_walk_up_to_repo_root(self, monkeypatch, tmp_path: Path):
        # Ensure a repo-root .env (if any) is not picked up from a temp cwd.
        monkeypatch.delenv("GC_DOTENV_VAR", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("GC_DOTENV_VAR=from-cwd\n")
        monkeypatch.chdir(tmp_path)
        load_dotenv_from_cwd()
        assert os.environ.get("GC_DOTENV_VAR") == "from-cwd"
