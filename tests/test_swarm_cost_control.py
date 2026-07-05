"""Tests for SwarmPhase cost control integration — CircuitBreaker, limits, and spend reporting."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from goblin.blueprint import V1Blueprint
from goblin.swarm import SwarmPhase


def _make_blueprint(phases: list[dict]) -> V1Blueprint:
    return V1Blueprint({"name": "test_bp", "phases": phases})


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


def _make_conversation(tokens: int = 0, cost: float = 0.0):
    conversation = MagicMock()
    conversation.arun = AsyncMock()
    conversation.state.events = []
    stats = MagicMock()
    metrics = MagicMock()
    token_usage = MagicMock()
    token_usage.prompt_tokens = tokens
    token_usage.completion_tokens = 0
    metrics.accumulated_token_usage = token_usage
    metrics.accumulated_cost = cost
    stats.get_combined_metrics.return_value = metrics
    conversation.conversation_stats = stats
    return conversation


# ---------------------------------------------------------------------------
# SwarmPhase parsing of new limit fields
# ---------------------------------------------------------------------------


class TestSwarmPhaseParsesLimits:
    def test_default_limits(self):
        bp = _make_blueprint([MINIMAL_SWARM_RAW])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        assert phase.max_iterations == 100
        assert phase.max_tokens is None
        assert phase.max_cost is None

    def test_custom_limits(self):
        raw = {
            "id": "custom_swarm",
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "max_iterations": 200,
                "max_tokens": 500000,
                "max_cost": 5.0,
            },
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        assert phase.max_iterations == 200
        assert phase.max_tokens == 500000
        assert phase.max_cost == 5.0

    def test_backward_compatible_max_iterations(self):
        raw = {
            "id": "compat_swarm",
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "max_iterations": 50,
            },
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        assert phase.max_iterations == 50


# ---------------------------------------------------------------------------
# SwarmPhase.run with CircuitBreaker
# ---------------------------------------------------------------------------


class TestSwarmPhaseRunCostControl:
    @pytest.mark.asyncio
    async def test_result_contains_spend(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws"), "cleanup": "never"},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)

        coordinator_conv = _make_conversation(tokens=10, cost=0.05)

        async def fake_run_coordinator(ctx, breaker):
            breaker.absorb(coordinator_conv)
            return {"done": True}

        with patch.object(phase, "_run_coordinator", fake_run_coordinator):
            result = await phase.run({})

        assert "spend" in result
        assert result["spend"].iterations == 1
        assert result["spend"].tokens == 10
        assert result["spend"].cost == 0.05

    @pytest.mark.asyncio
    async def test_max_iterations_stops_after_limit(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "workspace": str(tmp_path / "ws"),
                "cleanup": "never",
                "max_iterations": 2,
            },
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)

        call_count = 0

        async def fake_run_coordinator(ctx, breaker):
            nonlocal call_count
            call_count += 1
            conversation = _make_conversation()
            conversation.conversation_stats = None
            breaker.absorb(conversation)
            return {"done": False}

        async def fake_run_agents(ctx, breaker):
            nonlocal call_count
            call_count += 1
            for _ in range(2):
                conversation = _make_conversation()
                conversation.conversation_stats = None
                breaker.absorb(conversation)

        with (
            patch.object(phase, "_run_coordinator", fake_run_coordinator),
            patch.object(phase, "_run_agents", fake_run_agents),
        ):
            result = await phase.run({})

        assert result["success"] is False
        assert "max_iterations limit exceeded" in result["message"][-1]
        assert result["spend"].iterations == 2

    @pytest.mark.asyncio
    async def test_max_cost_stops_when_exceeded(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "workspace": str(tmp_path / "ws"),
                "cleanup": "never",
                "max_cost": 0.01,
            },
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)

        async def fake_run_coordinator(ctx, breaker):
            conversation = _make_conversation(cost=0.02)
            breaker.absorb(conversation)
            return {"done": False}

        with patch.object(phase, "_run_coordinator", fake_run_coordinator):
            result = await phase.run({})

        assert result["success"] is False
        assert "max_cost limit exceeded" in result["message"][-1]
        assert result["spend"].cost == 0.02

    @pytest.mark.asyncio
    async def test_per_agent_max_iterations_passed_to_local_conversation(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "workspace": str(tmp_path / "ws"),
                "cleanup": "never",
                "agents": [
                    {"name": "coder", "prompt": "Implement.", "max_iterations": 30},
                    {"name": "tester", "prompt": "Write tests."},
                ],
            },
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})

        created_conversations = []

        def fake_local_conversation(*, max_iteration_per_run, **kwargs):
            conv = _make_conversation()
            created_conversations.append(max_iteration_per_run)
            return conv

        with (
            patch("goblin.swarm.LocalConversation", side_effect=fake_local_conversation),
            patch("goblin.swarm.build_llm"),
            patch("goblin.swarm.Agent") as mock_agent_cls,
            patch("goblin.swarm.get_agent_final_response", return_value=""),
        ):
            mock_agent_cls.return_value = MagicMock()
            breaker = MagicMock()
            await phase._run_single_agent(phase.agents_config[0], {}, breaker)
            await phase._run_single_agent(phase.agents_config[1], {}, breaker)

        assert 30 in created_conversations
        assert 100 in created_conversations


# ---------------------------------------------------------------------------
# SwarmPhase coordinator per-run limits
# ---------------------------------------------------------------------------


class TestSwarmCoordinatorLimits:
    @pytest.mark.asyncio
    async def test_coordinator_max_iterations_passed(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "workspace": str(tmp_path / "ws"),
                "cleanup": "never",
                "coordinator": {
                    "prompt": "Break down.",
                    "max_iterations": 10,
                    "max_cost": 0.25,
                },
            },
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})

        created_conversations = []

        def fake_local_conversation(*, max_iteration_per_run, max_budget_per_run, **kwargs):
            conv = _make_conversation()
            created_conversations.append((max_iteration_per_run, max_budget_per_run))
            return conv

        with (
            patch("goblin.swarm.LocalConversation", side_effect=fake_local_conversation),
            patch("goblin.swarm.build_llm"),
            patch("goblin.swarm.Agent") as mock_agent_cls,
            patch("goblin.swarm.get_agent_final_response", return_value='{"done": true}'),
        ):
            mock_agent_cls.return_value = MagicMock()
            breaker = MagicMock()
            await phase._run_coordinator({}, breaker)

        coordinator_call = [c for c in created_conversations if c[0] == 10]
        assert len(coordinator_call) == 1
        assert coordinator_call[0][1] == 0.25
