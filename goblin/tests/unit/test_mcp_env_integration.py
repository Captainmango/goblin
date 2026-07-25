"""Integration tests for MCP env-var propagation through the full stack.

These tests verify that environment variables referenced in blueprint MCP
config (using ${VAR} syntax) actually reach the MCP server subprocess, and
that invalid $VAR (unbraced) syntax is rejected with a helpful message.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from openhands.sdk.mcp import create_mcp_tools
from openhands.sdk.mcp.tool import MCPToolAction

from goblin.blueprint import V1Blueprint
from goblin.mcp_config import to_native_mcp_servers
from goblin.phase import PromptPhase
from goblin.utils.env import validate_env_var_syntax

CANARY_SERVER_PATH = Path(__file__).with_name("canary_mcp_server.py")


def _make_blueprint(phases: list[dict], defaults: dict | None = None) -> V1Blueprint:
    spec: dict[str, Any] = {"name": "test_bp", "phases": phases}
    if defaults is not None:
        spec["defaults"] = defaults
    return V1Blueprint(spec)


def _get_tool_text(observation) -> str:
    """Extract the actual text content from an MCPToolObservation.

    OpenHands puts a generic '[Tool ... executed.]' message at index 0;
    real content starts at index 1.
    """
    blocks = [b for b in observation.content if hasattr(b, "text") and b.text]
    if len(blocks) > 1 and blocks[0].text.startswith("[Tool '"):
        return blocks[1].text
    return blocks[0].text if blocks else ""


class TestMcpEnvVarSyntaxValidation:
    """Verify that unbraced $VAR syntax is rejected early."""

    def test_unbraced_env_var_in_env_field_raises(self):
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
        with pytest.raises(ValueError, match=r"Invalid env-var syntax '\$MY_TOKEN'.*Use '\$\{MY_TOKEN\}'"):
            bp._build_mcp_servers(phase, {})

    def test_unbraced_env_var_in_url_raises(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff", "mcp_servers": {"api": {"type": "http", "url": "https://$HOST/mcp"}}}],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        with pytest.raises(ValueError, match=r"Invalid env-var syntax '\$HOST'.*Use '\$\{HOST\}'"):
            bp._build_mcp_servers(phase, {})

    def test_unbraced_env_var_in_headers_raises(self):
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
        with pytest.raises(ValueError, match=r"Invalid env-var syntax '\$KEY'.*Use '\$\{KEY\}'"):
            bp._build_mcp_servers(phase, {})

    def test_braced_syntax_is_accepted(self, monkeypatch):
        monkeypatch.setenv("VALID", "ok")
        bp = _make_blueprint(
            [
                {
                    "id": "s1",
                    "prompt": "do stuff",
                    "mcp_servers": {"gh": {"command": "npx", "env": {"TOKEN": "${VALID}"}}},
                }
            ],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        # Should not raise
        result = bp._build_mcp_servers(phase, {})
        assert result["mcpServers"]["gh"]["env"]["TOKEN"] == "ok"

    def test_validate_env_var_syntax_directly(self):
        with pytest.raises(ValueError, match="Invalid env-var syntax"):
            validate_env_var_syntax({"url": "https://$HOST/mcp"})

        with pytest.raises(ValueError, match="Invalid env-var syntax"):
            validate_env_var_syntax([{"env": {"TOKEN": "$API_KEY"}}])

        # Braced syntax should pass
        validate_env_var_syntax({"url": "https://${HOST}/mcp"})
        validate_env_var_syntax({"env": {"TOKEN": "${API_KEY}"}})

    def test_dollar_sign_without_var_name_is_allowed(self):
        """A bare '$' or '$123' should not trigger the validation."""
        validate_env_var_syntax({"url": "https://example.com/path?$=1"})
        validate_env_var_syntax({"note": "Price: $10"})


class TestMcpEnvVarPropagation:
    """End-to-end tests using the canary MCP server."""

    def test_canary_server_receives_env_var(self, monkeypatch):
        """Verify that ${CANARY_TOKEN} in blueprint env reaches the subprocess."""
        monkeypatch.setenv("CANARY_TOKEN", "secret123")

        bp = _make_blueprint(
            [
                {
                    "id": "s1",
                    "prompt": "do stuff",
                    "mcp_servers": {
                        "canary": {
                            "command": "python",
                            "args": [str(CANARY_SERVER_PATH)],
                            "env": {"CANARY_TOKEN": "${CANARY_TOKEN}"},
                        }
                    },
                }
            ],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)

        mcp_config = bp._build_mcp_servers(phase, {})
        assert "mcpServers" in mcp_config
        assert "canary" in mcp_config["mcpServers"]

        client = create_mcp_tools(to_native_mcp_servers(mcp_config), timeout=30.0)
        try:
            tools = {tool.name: tool for tool in client.tools}
            assert "get_canary" in tools, f"Available tools: {list(tools.keys())}"

            tool = tools["get_canary"]
            action = MCPToolAction(data={})
            observation = tool(action)
            assert not observation.is_error, f"Tool error: {observation.content}"
            text = _get_tool_text(observation)
            assert text == "secret123", f"Expected 'secret123', got {text!r}"
        finally:
            client.sync_close()

    def test_canary_server_reads_referenced_env(self, monkeypatch):
        """Verify that explicitly referenced ${VAR}s reach the subprocess."""
        monkeypatch.setenv("MY_CUSTOM_VAR", "hello_world")

        bp = _make_blueprint(
            [
                {
                    "id": "s1",
                    "prompt": "do stuff",
                    "mcp_servers": {
                        "canary": {
                            "command": "python",
                            "args": [str(CANARY_SERVER_PATH)],
                            "env": {"MY_CUSTOM_VAR": "${MY_CUSTOM_VAR}"},
                        }
                    },
                }
            ],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)

        mcp_config = bp._build_mcp_servers(phase, {})
        client = create_mcp_tools(to_native_mcp_servers(mcp_config), timeout=30.0)
        try:
            tools = {tool.name: tool for tool in client.tools}
            assert "get_env" in tools

            tool = tools["get_env"]
            action = MCPToolAction(data={"key": "MY_CUSTOM_VAR"})
            observation = tool(action)
            assert not observation.is_error
            text = _get_tool_text(observation)
            assert text == "hello_world", f"Expected 'hello_world', got {text!r}"
        finally:
            client.sync_close()

    def test_missing_env_var_raises(self, monkeypatch):
        """Blueprint referencing an unset ${VAR} should raise at build time."""
        monkeypatch.delenv("MISSING_VAR", raising=False)

        bp = _make_blueprint(
            [
                {
                    "id": "s1",
                    "prompt": "do stuff",
                    "mcp_servers": {
                        "canary": {
                            "command": "python",
                            "args": [str(CANARY_SERVER_PATH)],
                            "env": {"TOKEN": "${MISSING_VAR}"},
                        }
                    },
                }
            ],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)

        with pytest.raises(ValueError, match="MISSING_VAR"):
            bp._build_mcp_servers(phase, {})


class TestMcpReExpansionSafety:
    """Verify that OpenHands' expand_mcp_variables does not corrupt already-resolved values."""

    def test_resolved_value_with_dollar_brace_preserved(self, monkeypatch):
        """If an env var value contains ${...}, OpenHands should not re-expand it.

        This simulates what happens when Goblin resolves ${SENSITIVE} to a value
        like 'prefix${PATH}suffix', and then OpenHands calls expand_mcp_variables
        with check_env=True.
        """
        from openhands.sdk.skills.utils import expand_mcp_variables

        monkeypatch.setenv("SENSITIVE", "prefix${PATH}suffix")

        config = {
            "mcpServers": {
                "srv": {
                    "command": "echo",
                    "env": {"SENSITIVE": "${SENSITIVE}"},
                }
            }
        }

        expanded = expand_mcp_variables(
            config,
            variables={},
            get_secret=lambda _k: None,
            expand_defaults=True,
        )

        result = expanded["mcpServers"]["srv"]["env"]["SENSITIVE"]
        assert "${PATH}" in result, (
            f"Expected nested ${'{PATH}'} to be preserved in resolved value; final value was {result!r}"
        )
