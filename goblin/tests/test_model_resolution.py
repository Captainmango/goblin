"""Tests for blueprint/phase/swarm model resolution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from goblin.blueprint import V1Blueprint
from goblin.phase import PromptPhase
from goblin.swarm import SwarmPhase


# ---------------------------------------------------------------------------
# _resolve_model helper
# ---------------------------------------------------------------------------


class TestResolveModel:
    def test_no_model_returns_none(self):
        bp = V1Blueprint({"name": "test", "phases": [{"id": "p1", "prompt": "hi"}]})
        phase = bp.phases[0]
        assert bp._resolve_model(phase) is None

    def test_blueprint_default_used_when_phase_has_no_model(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "defaults": {"model": "anthropic/claude-opus-4"},
                "phases": [{"id": "p1", "prompt": "hi"}],
            }
        )
        phase = bp.phases[0]
        assert bp._resolve_model(phase) == "anthropic/claude-opus-4"

    def test_phase_model_overrides_blueprint_default(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "defaults": {"model": "anthropic/claude-opus-4"},
                "phases": [{"id": "p1", "prompt": "hi", "model": "openai/gpt-4o"}],
            }
        )
        phase = bp.phases[0]
        assert bp._resolve_model(phase) == "openai/gpt-4o"


# ---------------------------------------------------------------------------
# PromptPhase model propagation
# ---------------------------------------------------------------------------


class TestPromptPhaseModel:
    def test_model_attribute_stored(self):
        bp = V1Blueprint({"name": "test", "phases": [{"id": "p1", "prompt": "hi", "model": "openai/gpt-4o"}]})
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)
        assert phase.model == "openai/gpt-4o"

    @pytest.mark.asyncio
    async def test_run_passes_model_to_build_llm(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "defaults": {"model": "anthropic/claude-opus-4"},
                "phases": [{"id": "p1", "prompt": "hi", "model": "openai/gpt-4o"}],
            }
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)

        conversation = MagicMock()
        conversation.arun = AsyncMock()
        conversation.state.events = []
        conversation.conversation_stats = None

        with (
            patch("goblin.phase.build_llm") as mock_build_llm,
            patch("goblin.phase.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation", return_value=conversation),
            patch(
                "goblin.phase.get_agent_final_response",
                return_value='{"success": true, "message": ["ok"]}',
            ),
        ):
            mock_agent_cls.return_value = MagicMock()
            await phase.run({})

        mock_build_llm.assert_called_once_with(model="openai/gpt-4o")

    @pytest.mark.asyncio
    async def test_json_repair_passes_model_to_build_llm(self):
        bp = V1Blueprint(
            {
                "name": "test",
                "defaults": {"model": "anthropic/claude-opus-4"},
                "phases": [{"id": "p1", "prompt": "hi", "model": "openai/gpt-4o"}],
            }
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)

        conversation = MagicMock()
        conversation.arun = AsyncMock()
        conversation.state.events = []
        conversation.conversation_stats = None

        with (
            patch("goblin.phase.build_llm") as mock_build_llm,
            patch("goblin.phase.Agent") as mock_agent_cls,
            patch("goblin.phase.LocalConversation", return_value=conversation),
            patch("goblin.phase.get_agent_final_response", return_value='{"success": true}'),
        ):
            mock_agent_cls.return_value = MagicMock()
            await phase._request_json_repair("broken")

        mock_build_llm.assert_called_once_with(model="openai/gpt-4o")


# ---------------------------------------------------------------------------
# SwarmPhase model propagation
# ---------------------------------------------------------------------------


MINIMAL_SWARM_RAW = {
    "id": "test_swarm",
    "swarm": {
        "goal": "Build a calculator",
        "coordinator": {
            "prompt": "Break down the goal into tasks.",
            "allowed_tools": ["Read"],
        },
        "agents": [
            {"name": "coder", "prompt": "Implement assigned tasks."},
            {"name": "tester", "prompt": "Write tests for completed tasks."},
        ],
    },
}


class TestSwarmPhaseModel:
    def test_model_attribute_stored(self):
        raw = {
            **MINIMAL_SWARM_RAW,
            "model": "openai/gpt-4o",
        }
        bp = V1Blueprint({"name": "test", "phases": [raw]})
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        assert phase.model == "openai/gpt-4o"

    @pytest.mark.asyncio
    async def test_coordinator_uses_phase_model(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "model": "openai/gpt-4o",
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "workspace": str(tmp_path / "ws"),
                "cleanup": "never",
            },
        }
        bp = V1Blueprint({"name": "test", "phases": [raw]})
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})

        with (
            patch("goblin.swarm.build_llm") as mock_build_llm,
            patch("goblin.swarm.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation") as mock_conv_cls,
            patch("goblin.swarm.get_agent_final_response", return_value='{"done": true}'),
        ):
            mock_agent_cls.return_value = MagicMock()
            mock_conv_cls.return_value = MagicMock()
            mock_conv_cls.return_value.arun = AsyncMock()
            mock_conv_cls.return_value.state.events = []
            mock_conv_cls.return_value.conversation_stats = None
            breaker = MagicMock()
            await phase._run_coordinator({}, breaker)

        mock_build_llm.assert_called_once_with(model="openai/gpt-4o")

    @pytest.mark.asyncio
    async def test_coordinator_model_overrides_phase_model(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "model": "openai/gpt-4o",
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "workspace": str(tmp_path / "ws"),
                "cleanup": "never",
                "coordinator": {
                    "prompt": "Break down.",
                    "model": "anthropic/claude-opus-4",
                },
            },
        }
        bp = V1Blueprint({"name": "test", "phases": [raw]})
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})

        with (
            patch("goblin.swarm.build_llm") as mock_build_llm,
            patch("goblin.swarm.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation") as mock_conv_cls,
            patch("goblin.swarm.get_agent_final_response", return_value='{"done": true}'),
        ):
            mock_agent_cls.return_value = MagicMock()
            mock_conv_cls.return_value = MagicMock()
            mock_conv_cls.return_value.arun = AsyncMock()
            mock_conv_cls.return_value.state.events = []
            mock_conv_cls.return_value.conversation_stats = None
            breaker = MagicMock()
            await phase._run_coordinator({}, breaker)

        mock_build_llm.assert_called_once_with(model="anthropic/claude-opus-4")

    @pytest.mark.asyncio
    async def test_agent_uses_phase_model(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "model": "openai/gpt-4o",
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "workspace": str(tmp_path / "ws"),
                "cleanup": "never",
            },
        }
        bp = V1Blueprint({"name": "test", "phases": [raw]})
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})

        with (
            patch("goblin.swarm.build_llm") as mock_build_llm,
            patch("goblin.swarm.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation") as mock_conv_cls,
            patch("goblin.swarm.get_agent_final_response", return_value="done"),
        ):
            mock_agent_cls.return_value = MagicMock()
            mock_conv_cls.return_value = MagicMock()
            mock_conv_cls.return_value.arun = AsyncMock()
            mock_conv_cls.return_value.state.events = []
            mock_conv_cls.return_value.conversation_stats = None
            breaker = MagicMock()
            await phase._run_single_agent(phase.agents_config[0], {}, breaker)

        mock_build_llm.assert_called_once_with(model="openai/gpt-4o")

    @pytest.mark.asyncio
    async def test_agent_model_overrides_phase_model(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "model": "openai/gpt-4o",
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "workspace": str(tmp_path / "ws"),
                "cleanup": "never",
                "agents": [
                    {"name": "coder", "prompt": "Implement.", "model": "anthropic/claude-opus-4"},
                    {"name": "tester", "prompt": "Write tests."},
                ],
            },
        }
        bp = V1Blueprint({"name": "test", "phases": [raw]})
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})

        with (
            patch("goblin.swarm.build_llm") as mock_build_llm,
            patch("goblin.swarm.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation") as mock_conv_cls,
            patch("goblin.swarm.get_agent_final_response", return_value="done"),
        ):
            mock_agent_cls.return_value = MagicMock()
            mock_conv_cls.return_value = MagicMock()
            mock_conv_cls.return_value.arun = AsyncMock()
            mock_conv_cls.return_value.state.events = []
            mock_conv_cls.return_value.conversation_stats = None
            breaker = MagicMock()
            await phase._run_single_agent(phase.agents_config[0], {}, breaker)
            await phase._run_single_agent(phase.agents_config[1], {}, breaker)

        assert mock_build_llm.call_args_list == [
            call(model="anthropic/claude-opus-4"),
            call(model="openai/gpt-4o"),
        ]

    @pytest.mark.asyncio
    async def test_blueprint_default_falls_back_when_no_phase_model(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "workspace": str(tmp_path / "ws"),
                "cleanup": "never",
            },
        }
        bp = V1Blueprint(
            {
                "name": "test",
                "defaults": {"model": "anthropic/claude-opus-4"},
                "phases": [raw],
            }
        )
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})

        with (
            patch("goblin.swarm.build_llm") as mock_build_llm,
            patch("goblin.swarm.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation") as mock_conv_cls,
            patch("goblin.swarm.get_agent_final_response", return_value='{"done": true}'),
        ):
            mock_agent_cls.return_value = MagicMock()
            mock_conv_cls.return_value = MagicMock()
            mock_conv_cls.return_value.arun = AsyncMock()
            mock_conv_cls.return_value.state.events = []
            mock_conv_cls.return_value.conversation_stats = None
            breaker = MagicMock()
            await phase._run_coordinator({}, breaker)

        mock_build_llm.assert_called_once_with(model="anthropic/claude-opus-4")
