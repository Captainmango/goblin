"""Tests that spec/v1.json supports OpenHands LLM auth and MCP server modes."""

from __future__ import annotations

import json
import os

import pytest
import yaml
from goblin.validation import validate_blueprint


def _load_schema() -> dict:
    path = os.path.join(os.path.dirname(__file__), "..", "..", "spec", "v1.json")
    with open(path) as f:
        return json.load(f)


@pytest.fixture
def schema() -> dict:
    return _load_schema()


# ---------------------------------------------------------------------------
# LLM config schema
# ---------------------------------------------------------------------------


class TestSchemaLlmConfig:
    def test_llm_config_definition_exists(self, schema):
        assert "llmConfig" in schema["definitions"]

    def test_llm_config_has_auth_fields(self, schema):
        props = schema["definitions"]["llmConfig"]["properties"]
        for field in (
            "model",
            "api_key",
            "auth_type",
            "subscription_vendor",
            "base_url",
            "api_version",
            "aws_access_key_id",
            "aws_secret_access_key",
            "aws_session_token",
            "aws_region_name",
            "aws_profile_name",
            "aws_role_name",
            "aws_session_name",
            "aws_bedrock_runtime_endpoint",
            "openrouter_site_url",
            "openrouter_app_name",
            "extra_headers",
            "ollama_base_url",
        ):
            assert field in props, f"missing {field}"

    def test_phase_has_llm_field(self, schema):
        props = schema["definitions"]["phase"]["properties"]
        assert "llm" in props
        assert props["llm"]["$ref"] == "#/definitions/llmConfig"

    def test_defaults_has_llm_field(self, schema):
        props = schema["properties"]["blueprint"]["properties"]["defaults"]["properties"]
        assert "llm" in props

    def test_swarm_has_llm_and_model_fields(self, schema):
        props = schema["definitions"]["swarm"]["properties"]
        assert "llm" in props
        assert "model" in props

    def test_swarm_coordinator_has_llm_field(self, schema):
        props = schema["definitions"]["swarmCoordinator"]["properties"]
        assert "llm" in props

    def test_swarm_agent_has_llm_field(self, schema):
        props = schema["definitions"]["swarmAgent"]["properties"]
        assert "llm" in props


# ---------------------------------------------------------------------------
# MCP server schema
# ---------------------------------------------------------------------------


class TestSchemaMcpServers:
    def test_stdio_has_extended_fields(self, schema):
        props = schema["definitions"]["mcpServerStdio"]["properties"]
        for field in (
            "cwd",
            "timeout",
            "keep_alive",
            "description",
            "icon",
            "authentication",
            "tools",
            "include_tags",
            "exclude_tags",
        ):
            assert field in props, f"missing {field}"

    def test_remote_definition_exists(self, schema):
        assert "mcpServerRemote" in schema["definitions"]

    def test_remote_has_all_transports(self, schema):
        transport = schema["definitions"]["mcpServerRemote"]["properties"]["transport"]
        assert set(transport["enum"]) == {"http", "streamable-http", "sse"}

    def test_remote_has_auth_fields(self, schema):
        props = schema["definitions"]["mcpServerRemote"]["properties"]
        for field in ("auth", "sse_read_timeout", "timeout", "authentication", "tools", "include_tags", "exclude_tags"):
            assert field in props, f"missing {field}"


# ---------------------------------------------------------------------------
# Validation of example configs
# ---------------------------------------------------------------------------


class TestValidateExtendedConfigs:
    def test_validates_blueprint_with_llm_config(self, tmp_path):
        bp = {
            "version": "v1",
            "blueprint": {
                "name": "test",
                "defaults": {
                    "llm": {
                        "model": "openai/gpt-4o",
                        "api_key": "${OPENAI_API_KEY}",
                        "temperature": 0.5,
                    }
                },
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hello",
                        "llm": {"model": "anthropic/claude-opus-4", "auth_type": "api_key"},
                    }
                ],
            },
        }
        path = tmp_path / "bp.yaml"
        path.write_text(yaml.safe_dump(bp))
        assert validate_blueprint(str(path)) == []

    def test_validates_blueprint_with_streamable_http_mcp(self, tmp_path):
        bp = {
            "version": "v1",
            "blueprint": {
                "name": "test",
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hello",
                        "mcp_servers": {
                            "api": {
                                "transport": "streamable-http",
                                "url": "https://example.com/mcp",
                                "auth": "${MCP_TOKEN}",
                            }
                        },
                    }
                ],
            },
        }
        path = tmp_path / "bp.yaml"
        path.write_text(yaml.safe_dump(bp))
        assert validate_blueprint(str(path)) == []

    def test_validates_blueprint_with_swarm_llm(self, tmp_path):
        bp = {
            "version": "v1",
            "blueprint": {
                "name": "test",
                "phases": [
                    {
                        "id": "s1",
                        "swarm": {
                            "goal": "Build",
                            "model": "openai/gpt-4o",
                            "llm": {"temperature": 0.2},
                            "coordinator": {
                                "prompt": "Coordinate.",
                                "llm": {"model": "anthropic/claude-opus-4"},
                            },
                            "agents": [
                                {
                                    "name": "coder",
                                    "prompt": "Code.",
                                    "llm": {"model": "openai/gpt-4o-mini"},
                                }
                            ],
                        },
                    }
                ],
            },
        }
        path = tmp_path / "bp.yaml"
        path.write_text(yaml.safe_dump(bp))
        assert validate_blueprint(str(path)) == []
