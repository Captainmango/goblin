"""Tests for blueprint LLM configuration resolution and build_llm dict support."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from goblin.blueprint import V1Blueprint
from goblin.output_schema import PhaseResult
from goblin.phase import PromptPhase
from goblin.swarm import SwarmPhase
from goblin.utils.llm import build_llm
from tests.unit._helpers import MINIMAL_SWARM_RAW as MINIMAL_SWARM
from tests.unit._helpers import MagicAgent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_blueprint(phases: list[dict], defaults: dict | None = None) -> V1Blueprint:
    spec: dict[str, Any] = {"name": "test_bp", "phases": phases}
    if defaults is not None:
        spec["defaults"] = defaults
    return V1Blueprint(spec)


# ---------------------------------------------------------------------------
# _resolve_llm_config
# ---------------------------------------------------------------------------


class TestResolveLlmConfig:
    def test_no_config_returns_none(self):
        bp = _make_blueprint([{"id": "p1", "prompt": "hi"}])
        phase = bp.phases[0]
        assert bp._resolve_llm_config(phase) is None

    def test_phase_llm_overrides_default(self):
        bp = _make_blueprint(
            [
                {
                    "id": "p1",
                    "prompt": "hi",
                    "llm": {"model": "openai/gpt-4o", "api_key": "${OPENAI_API_KEY}"},
                }
            ],
            defaults={"llm": {"model": "anthropic/claude-opus-4", "temperature": 0.5}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        assert phase.llm_config is not None

        with pytest.MonkeyPatch().context() as mp:
            mp.setenv("OPENAI_API_KEY", "sk-openai")
            config = bp._resolve_llm_config(phase)

        assert isinstance(config, dict)
        assert config["model"] == "openai/gpt-4o"
        assert config["api_key"] == "sk-openai"

    def test_phase_model_string_used_when_no_llm(self):
        bp = _make_blueprint(
            [{"id": "p1", "prompt": "hi", "model": "openai/gpt-4o"}],
            defaults={"llm": {"model": "anthropic/claude-opus-4"}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        assert bp._resolve_llm_config(phase) == "openai/gpt-4o"

    def test_default_llm_used_when_phase_has_none(self):
        bp = _make_blueprint(
            [{"id": "p1", "prompt": "hi"}],
            defaults={"llm": {"model": "anthropic/claude-opus-4", "base_url": "https://proxy.example.com"}},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        config = bp._resolve_llm_config(phase)
        assert isinstance(config, dict)
        assert config["model"] == "anthropic/claude-opus-4"
        assert config["base_url"] == "https://proxy.example.com"

    def test_default_model_string_fallback(self):
        bp = _make_blueprint(
            [{"id": "p1", "prompt": "hi"}],
            defaults={"model": "anthropic/claude-opus-4"},
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        assert bp._resolve_llm_config(phase) == "anthropic/claude-opus-4"

    def test_env_vars_resolved_in_dict(self, monkeypatch):
        monkeypatch.setenv("AZURE_API_KEY", "sk-azure")
        monkeypatch.setenv("AZURE_BASE", "https://azure.example.com")
        bp = _make_blueprint(
            [
                {
                    "id": "p1",
                    "prompt": "hi",
                    "llm": {
                        "model": "azure/gpt-4o",
                        "api_key": "${AZURE_API_KEY}",
                        "base_url": "${AZURE_BASE}",
                        "api_version": "2024-08-01-preview",
                    },
                }
            ],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        config = bp._resolve_llm_config(phase)
        assert isinstance(config, dict)
        assert config["api_key"] == "sk-azure"
        assert config["base_url"] == "https://azure.example.com"

    def test_context_interpolation_in_dict(self):
        bp = _make_blueprint(
            [
                {
                    "id": "p1",
                    "prompt": "hi",
                    "llm": {"model": "{provider}/gpt-4o", "temperature": 0.2},
                }
            ],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        config = bp._resolve_llm_config(phase, {"provider": "openai"})
        assert isinstance(config, dict)
        assert config["model"] == "openai/gpt-4o"

    def test_aws_auth_fields_passed_through(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS", "AKIA")
        monkeypatch.setenv("AWS_SECRET", "secret")
        bp = _make_blueprint(
            [
                {
                    "id": "p1",
                    "prompt": "hi",
                    "llm": {
                        "model": "bedrock/anthropic.claude-sonnet-4-6-v1",
                        "auth_type": "api_key",
                        "aws_access_key_id": "${AWS_ACCESS}",
                        "aws_secret_access_key": "${AWS_SECRET}",
                        "aws_region_name": "us-east-1",
                    },
                }
            ],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        config = bp._resolve_llm_config(phase)
        assert isinstance(config, dict)
        assert config["aws_access_key_id"] == "AKIA"
        assert config["aws_secret_access_key"] == "secret"
        assert config["aws_region_name"] == "us-east-1"


# ---------------------------------------------------------------------------
# _resolve_model backward compatibility
# ---------------------------------------------------------------------------


class TestResolveModel:
    def test_extracts_model_from_dict(self):
        bp = _make_blueprint(
            [{"id": "p1", "prompt": "hi", "llm": {"model": "openai/gpt-4o", "temperature": 0.5}}],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        assert bp._resolve_model(phase) == "openai/gpt-4o"

    def test_returns_string_model(self):
        bp = _make_blueprint(
            [{"id": "p1", "prompt": "hi", "model": "openai/gpt-4o"}],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        assert bp._resolve_model(phase) == "openai/gpt-4o"


# ---------------------------------------------------------------------------
# build_llm
# ---------------------------------------------------------------------------


class TestBuildLlm:
    def test_dict_config_passed_to_llm(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "sk-test")
        llm = build_llm({"model": "openai/gpt-4o", "temperature": 0.5})
        assert llm.model == "openai/gpt-4o"
        assert llm.temperature == 0.5

    def test_dict_config_uses_env_default_model_when_missing(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "sk-test")
        monkeypatch.setenv("GOBLIN_DEFAULT_MODEL", "openai/gpt-4o")
        llm = build_llm({"temperature": 0.5})
        assert llm.model == "openai/gpt-4o"

    def test_dict_config_falls_back_to_env_api_key(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "sk-env")
        llm = build_llm({"model": "openai/gpt-4o", "temperature": 0.5})
        assert llm.model == "openai/gpt-4o"
        assert llm.api_key.get_secret_value() == "sk-env"

    def test_dict_config_falls_back_to_legacy_env_api_key(self, monkeypatch):
        monkeypatch.delenv("GOBLIN_MODEL_API_KEY", raising=False)
        monkeypatch.setenv("MODEL_API_KEY", "sk-legacy")
        llm = build_llm({"model": "openai/gpt-4o"})
        assert llm.api_key.get_secret_value() == "sk-legacy"

    def test_dict_config_uses_explicit_api_key_over_env(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "sk-env")
        llm = build_llm({"model": "openai/gpt-4o", "api_key": "sk-explicit"})
        assert llm.api_key.get_secret_value() == "sk-explicit"

    def test_dict_config_falls_back_to_env_base_url(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "sk-test")
        monkeypatch.setenv("GOBLIN_BASE_URL", "https://proxy.example.com")
        llm = build_llm({"model": "openai/gpt-4o"})
        assert llm.base_url == "https://proxy.example.com"

    def test_dict_config_falls_back_to_legacy_env_base_url(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "sk-test")
        monkeypatch.delenv("GOBLIN_BASE_URL", raising=False)
        monkeypatch.setenv("OPENHANDS_BASE_URL", "https://legacy.example.com")
        llm = build_llm({"model": "openai/gpt-4o"})
        assert llm.base_url == "https://legacy.example.com"

    def test_dict_config_uses_explicit_base_url_over_env(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "sk-test")
        monkeypatch.setenv("GOBLIN_BASE_URL", "https://env.example.com")
        llm = build_llm({"model": "openai/gpt-4o", "base_url": "https://explicit.example.com"})
        assert llm.base_url == "https://explicit.example.com"

    def test_dict_config_does_not_require_api_key(self, monkeypatch):
        monkeypatch.delenv("GOBLIN_MODEL_API_KEY", raising=False)
        monkeypatch.delenv("MODEL_API_KEY", raising=False)
        # Local providers such as Ollama do not need an API key.
        llm = build_llm(
            {
                "model": "ollama/llama3",
                "ollama_base_url": "http://localhost:11434",
                "max_input_tokens": 32000,
            }
        )
        assert llm.model == "ollama/llama3"
        assert llm.api_key is None

    def test_string_model_uses_env_credentials(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "sk-string")
        monkeypatch.setenv("GOBLIN_BASE_URL", "https://proxy.example.com")
        llm = build_llm("openai/gpt-4o")
        assert llm.model == "openai/gpt-4o"
        assert llm.api_key.get_secret_value() == "sk-string"
        assert llm.base_url == "https://proxy.example.com"

    def test_none_uses_env_default_model(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "sk-default")
        monkeypatch.setenv("GOBLIN_DEFAULT_MODEL", "anthropic/claude-opus-4")
        llm = build_llm()
        assert llm.model == "anthropic/claude-opus-4"

    def test_missing_model_and_env_raises(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "sk-test")
        monkeypatch.delenv("GOBLIN_DEFAULT_MODEL", raising=False)
        with pytest.raises(RuntimeError, match="No model configured"):
            build_llm()

    def test_explicit_model_overrides_env_default(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_MODEL_API_KEY", "sk-test")
        monkeypatch.setenv("GOBLIN_DEFAULT_MODEL", "openai/gpt-4o")
        llm = build_llm("anthropic/claude-opus-4")
        assert llm.model == "anthropic/claude-opus-4"

    @pytest.mark.asyncio
    async def test_prompt_phase_passes_llm_dict_to_build_llm(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        bp = _make_blueprint(
            [
                {
                    "id": "p1",
                    "prompt": "hi",
                    "llm": {"model": "openai/gpt-4o", "api_key": "${OPENAI_API_KEY}"},
                }
            ],
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)

        conversation = MagicMock()
        conversation.arun = AsyncMock()
        conversation.state.events = []
        conversation.conversation_stats = None

        with (
            patch("goblin.utils.llm.build_llm") as mock_build_llm,
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation", return_value=conversation),
            patch(
                "goblin.tools.structured_finish.extract_structured_result",
                return_value=PhaseResult(success=True, message=["ok"]),
            ),
        ):
            mock_agent_cls.return_value = MagicAgent()
            await phase.run({})

        # build_llm is called once for the primary run; the structured-output
        # follow-up is skipped because the in-loop Finish tool succeeded.
        mock_build_llm.assert_called_once()
        passed = mock_build_llm.call_args.kwargs["model"]
        assert isinstance(passed, dict)
        assert passed["model"] == "openai/gpt-4o"
        assert passed["api_key"] == "sk-test"


# ---------------------------------------------------------------------------
# Swarm LLM config resolution
# ---------------------------------------------------------------------------


class TestSwarmLlmConfig:
    def test_swarm_llm_config_stored(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "phases": [
                    {
                        **MINIMAL_SWARM,
                        "llm": {"model": "openai/gpt-4o"},
                        "swarm": {
                            **MINIMAL_SWARM["swarm"],
                            "llm": {"model": "anthropic/claude-opus-4", "temperature": 0.5},
                        },
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        assert phase.swarm_llm_config == {"model": "anthropic/claude-opus-4", "temperature": 0.5}

    def test_resolve_agent_uses_swarm_llm(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "phases": [
                    {
                        **MINIMAL_SWARM,
                        "swarm": {
                            **MINIMAL_SWARM["swarm"],
                            "llm": {"model": "anthropic/claude-opus-4"},
                        },
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        config = phase._resolve_swarm_llm_config(None, None, {})
        assert config == {"model": "anthropic/claude-opus-4"}

    def test_resolve_agent_prefers_agent_config(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "phases": [
                    {
                        **MINIMAL_SWARM,
                        "swarm": {
                            **MINIMAL_SWARM["swarm"],
                            "llm": {"model": "anthropic/claude-opus-4"},
                            "agents": [
                                {
                                    "name": "coder",
                                    "prompt": "Code.",
                                    "llm": {"model": "openai/gpt-4o"},
                                }
                            ],
                        },
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        config = phase._resolve_swarm_llm_config(
            phase.agents_config[0].get("llm"),
            phase.agents_config[0].get("model"),
            {},
        )
        assert config == {"model": "openai/gpt-4o"}

    def test_resolve_coordinator_prefers_coordinator_config(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "phases": [
                    {
                        **MINIMAL_SWARM,
                        "swarm": {
                            **MINIMAL_SWARM["swarm"],
                            "llm": {"model": "anthropic/claude-opus-4"},
                            "coordinator": {
                                "prompt": "Coordinate.",
                                "llm": {"model": "openai/gpt-4o"},
                            },
                        },
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        config = phase._resolve_swarm_llm_config(
            phase.coordinator_config.get("llm"),
            phase.coordinator_config.get("model"),
            {},
        )
        assert config == {"model": "openai/gpt-4o"}

    def test_resolve_swarm_env_vars(self, monkeypatch):
        monkeypatch.setenv("MODEL_KEY", "sk-secret")
        bp = V1Blueprint(
            {
                "name": "test",
                "phases": [
                    {
                        **MINIMAL_SWARM,
                        "swarm": {
                            **MINIMAL_SWARM["swarm"],
                            "llm": {"model": "openai/gpt-4o", "api_key": "${MODEL_KEY}"},
                        },
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        config = phase._resolve_swarm_llm_config(None, None, {})
        assert isinstance(config, dict)
        assert config["api_key"] == "sk-secret"

    def test_resolve_swarm_context_interpolation(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "phases": [
                    {
                        **MINIMAL_SWARM,
                        "swarm": {
                            **MINIMAL_SWARM["swarm"],
                            "llm": {"model": "{provider}/gpt-4o"},
                        },
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        config = phase._resolve_swarm_llm_config(None, None, {"provider": "openai"})
        assert config == {"model": "openai/gpt-4o"}
