"""Tests for V1Blueprint._build_mcp_servers — MCP server merging, env resolution, and context interpolation."""

from __future__ import annotations

from typing import Any

import pytest

from goblin.blueprint import V1Blueprint
from goblin.phase import PromptPhase


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_blueprint(phases: list[dict], defaults: dict | None = None) -> V1Blueprint:
    spec: dict[str, Any] = {"name": "test_bp", "phases": phases}
    if defaults is not None:
        spec["defaults"] = defaults
    return V1Blueprint(spec)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class TestBuildMcpServersDefaults:
    def test_blueprint_defaults_used_when_phase_has_none(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff"}],
            defaults={"mcp_servers": {"gh": {"command": "npx", "args": ["-y", "@mcp/github"]}}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_mcp_servers(phase, {})
        assert "gh" in result
        assert result["gh"]["command"] == "npx"

    def test_empty_when_no_defaults_no_phase(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "do stuff"}])
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_mcp_servers(phase, {})
        assert result == {}


# ---------------------------------------------------------------------------
# Phase-level
# ---------------------------------------------------------------------------


class TestBuildMcpServersPhaseLevel:
    def test_phase_servers_returned(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "mcp_servers": {"jira": {"type": "http", "url": "https://j.co/mcp"}}}],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_mcp_servers(phase, {})
        assert "jira" in result

    def test_phase_overrides_default_by_name(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "mcp_servers": {"gh": {"command": "new"}}}],
            defaults={"mcp_servers": {"gh": {"command": "old"}}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_mcp_servers(phase, {})
        assert result["gh"]["command"] == "new"


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------


class TestBuildMcpServersMerge:
    def test_merge_additive(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "mcp_servers": {"jira": {"type": "http", "url": "https://j.co/mcp"}}}],
            defaults={"mcp_servers": {"gh": {"command": "npx"}}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_mcp_servers(phase, {})
        assert "gh" in result
        assert "jira" in result


# ---------------------------------------------------------------------------
# Env resolution
# ---------------------------------------------------------------------------


class TestBuildMcpServersEnvResolution:
    def test_env_var_in_env_field(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "secret")
        bp = _make_blueprint(
            [
                {
                    "id": "s1",
                    "prompt": "do stuff",
                    "mcp_servers": {"gh": {"command": "npx", "env": {"TOKEN": "$MY_TOKEN"}}},
                }
            ],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_mcp_servers(phase, {})
        assert result["gh"]["env"]["TOKEN"] == "secret"

    def test_env_var_in_url(self, monkeypatch):
        monkeypatch.setenv("HOST", "example.com")
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "mcp_servers": {"api": {"type": "http", "url": "https://$HOST/mcp"}}}],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_mcp_servers(phase, {})
        assert result["api"]["url"] == "https://example.com/mcp"

    def test_env_var_in_headers(self, monkeypatch):
        monkeypatch.setenv("KEY", "abc")
        bp = _make_blueprint(
            [
                {
                    "id": "s1",
                    "prompt": "do stuff",
                    "mcp_servers": {
                        "api": {"type": "http", "url": "https://x.co/mcp", "headers": {"Auth": "Bearer $KEY"}}
                    },
                }
            ],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_mcp_servers(phase, {})
        assert result["api"]["headers"]["Auth"] == "Bearer abc"

    def test_missing_env_var_raises(self, monkeypatch):
        monkeypatch.delenv("MISSING", raising=False)
        bp = _make_blueprint(
            [
                {
                    "id": "s1",
                    "prompt": "do stuff",
                    "mcp_servers": {"gh": {"command": "npx", "env": {"TOKEN": "$MISSING"}}},
                }
            ],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        with pytest.raises(ValueError, match="MISSING"):
            bp._build_mcp_servers(phase, {})


# ---------------------------------------------------------------------------
# Context interpolation
# ---------------------------------------------------------------------------


class TestBuildMcpServersContextInterpolation:
    def test_context_var_in_args(self):
        bp = _make_blueprint(
            [
                {
                    "id": "s1",
                    "prompt": "do stuff",
                    "mcp_servers": {"gh": {"command": "npx", "args": ["-y", "{package}"]}},
                }
            ],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_mcp_servers(phase, {"package": "@mcp/gh"})
        assert "@mcp/gh" in result["gh"]["args"]

    def test_context_var_in_url(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "mcp_servers": {"api": {"type": "http", "url": "https://{host}/mcp"}}}],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_mcp_servers(phase, {"host": "example.com"})
        assert result["api"]["url"] == "https://example.com/mcp"

    def test_mixed_env_and_context(self, monkeypatch):
        monkeypatch.setenv("TOKEN", "secret123")
        bp = _make_blueprint(
            [
                {
                    "id": "s1",
                    "prompt": "do stuff",
                    "mcp_servers": {
                        "api": {"type": "http", "url": "https://{host}/mcp", "headers": {"Auth": "Bearer $TOKEN"}}
                    },
                }
            ],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_mcp_servers(phase, {"host": "example.com"})
        assert result["api"]["url"] == "https://example.com/mcp"
        assert result["api"]["headers"]["Auth"] == "Bearer secret123"


# ---------------------------------------------------------------------------
# Type inference
# ---------------------------------------------------------------------------


class TestBuildMcpServersTypeInference:
    def test_stdio_inferred_when_command_present_no_type(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "mcp_servers": {"gh": {"command": "npx"}}}],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        result = bp._build_mcp_servers(phase, {})
        assert "gh" in result
        assert "type" not in result["gh"]
        assert result["gh"]["command"] == "npx"
