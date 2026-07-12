"""Tests for resolve_env_vars — ${VAR} environment variable resolution."""

from __future__ import annotations

import pytest

from goblin_cli.utils.env import resolve_env_vars


class TestSimpleVars:
    def test_braced_var(self, monkeypatch):
        monkeypatch.setenv("FOO", "bar")
        assert resolve_env_vars("${FOO}") == "bar"

    def test_var_embedded_in_string(self, monkeypatch):
        monkeypatch.setenv("TOKEN", "abc")
        assert resolve_env_vars("Bearer ${TOKEN}") == "Bearer abc"

    def test_no_vars_unchanged(self):
        assert resolve_env_vars("plain string") == "plain string"

    def test_multiple_vars_in_one_string(self, monkeypatch):
        monkeypatch.setenv("A", "x")
        monkeypatch.setenv("B", "y")
        assert resolve_env_vars("${A} and ${B}") == "x and y"


class TestDicts:
    def test_dict_values_resolved(self, monkeypatch):
        monkeypatch.setenv("FOO", "bar")
        assert resolve_env_vars({"key": "${FOO}"}) == {"key": "bar"}

    def test_dict_keys_unchanged(self, monkeypatch):
        monkeypatch.setenv("FOO", "bar")
        assert resolve_env_vars({"${FOO}": "val"}) == {"${FOO}": "val"}

    def test_nested_dict(self, monkeypatch):
        monkeypatch.setenv("X", "y")
        assert resolve_env_vars({"a": {"b": "${X}"}}) == {"a": {"b": "y"}}


class TestLists:
    def test_list_values_resolved(self, monkeypatch):
        monkeypatch.setenv("A", "1")
        monkeypatch.setenv("B", "2")
        assert resolve_env_vars(["${A}", "${B}"]) == ["1", "2"]


class TestNonStringPassthrough:
    def test_int_passthrough(self):
        assert resolve_env_vars(42) == 42

    def test_bool_passthrough(self):
        assert resolve_env_vars(True) is True

    def test_none_passthrough(self):
        assert resolve_env_vars(None) is None


class TestMissingVars:
    def test_missing_braced_var_raises(self, monkeypatch):
        monkeypatch.delenv("MISSING", raising=False)
        with pytest.raises(ValueError, match="MISSING"):
            resolve_env_vars("${MISSING}")
