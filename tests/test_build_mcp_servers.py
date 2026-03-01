"""Tests for V1Blueprint._build_mcp_servers — MCP server merging, env resolution, and context interpolation."""

from __future__ import annotations

from typing import Any

import pytest

from goblin.blueprint import V1Blueprint
from goblin.step import PromptStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_blueprint(steps: list[dict], defaults: dict | None = None) -> V1Blueprint:
    spec: dict[str, Any] = {"name": "test_wf", "steps": steps}
    if defaults is not None:
        spec["defaults"] = defaults
    return V1Blueprint(spec)


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class TestBuildMcpServersDefaults:
    def test_workflow_defaults_used_when_step_has_none(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff"}],
            defaults={"mcp_servers": {"gh": {"command": "npx", "args": ["-y", "@mcp/github"]}}},
        )
        step = bp.steps[0]
        assert isinstance(step, PromptStep)
        result = bp._build_mcp_servers(step, {})
        assert "gh" in result
        assert result["gh"]["command"] == "npx"

    def test_empty_when_no_defaults_no_step(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "do stuff"}])
        step = bp.steps[0]
        assert isinstance(step, PromptStep)
        result = bp._build_mcp_servers(step, {})
        assert result == {}


# ---------------------------------------------------------------------------
# Step-level
# ---------------------------------------------------------------------------


class TestBuildMcpServersStepLevel:
    def test_step_servers_returned(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "mcp_servers": {"jira": {"type": "http", "url": "https://j.co/mcp"}}}],
        )
        step = bp.steps[0]
        assert isinstance(step, PromptStep)
        result = bp._build_mcp_servers(step, {})
        assert "jira" in result

    def test_step_overrides_default_by_name(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "mcp_servers": {"gh": {"command": "new"}}}],
            defaults={"mcp_servers": {"gh": {"command": "old"}}},
        )
        step = bp.steps[0]
        assert isinstance(step, PromptStep)
        result = bp._build_mcp_servers(step, {})
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
        step = bp.steps[0]
        assert isinstance(step, PromptStep)
        result = bp._build_mcp_servers(step, {})
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
        step = bp.steps[0]
        assert isinstance(step, PromptStep)
        result = bp._build_mcp_servers(step, {})
        assert result["gh"]["env"]["TOKEN"] == "secret"

    def test_env_var_in_url(self, monkeypatch):
        monkeypatch.setenv("HOST", "example.com")
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "mcp_servers": {"api": {"type": "http", "url": "https://$HOST/mcp"}}}],
        )
        step = bp.steps[0]
        assert isinstance(step, PromptStep)
        result = bp._build_mcp_servers(step, {})
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
        step = bp.steps[0]
        assert isinstance(step, PromptStep)
        result = bp._build_mcp_servers(step, {})
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
        step = bp.steps[0]
        assert isinstance(step, PromptStep)
        with pytest.raises(ValueError, match="MISSING"):
            bp._build_mcp_servers(step, {})


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
        step = bp.steps[0]
        assert isinstance(step, PromptStep)
        result = bp._build_mcp_servers(step, {"package": "@mcp/gh"})
        assert "@mcp/gh" in result["gh"]["args"]

    def test_context_var_in_url(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "mcp_servers": {"api": {"type": "http", "url": "https://{host}/mcp"}}}],
        )
        step = bp.steps[0]
        assert isinstance(step, PromptStep)
        result = bp._build_mcp_servers(step, {"host": "example.com"})
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
        step = bp.steps[0]
        assert isinstance(step, PromptStep)
        result = bp._build_mcp_servers(step, {"host": "example.com"})
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
        step = bp.steps[0]
        assert isinstance(step, PromptStep)
        result = bp._build_mcp_servers(step, {})
        assert "gh" in result
        assert "type" not in result["gh"]
        assert result["gh"]["command"] == "npx"
