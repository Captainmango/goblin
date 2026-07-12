"""Tests for PromptPhase cost control integration — CircuitBreaker, limits, and spend reporting."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from goblin_cli.phase import PromptPhase


def _make_prompt_phase(raw: dict) -> PromptPhase:
    blueprint = MagicMock()
    blueprint._build_hooks.return_value = None
    blueprint._build_mcp_servers.return_value = {}
    blueprint.project_path = None
    blueprint.project_refs = ""
    blueprint.project_agents = {}
    return PromptPhase(raw, blueprint)


@pytest.fixture
def mock_conversation():
    conversation = MagicMock()
    conversation.arun = AsyncMock()
    conversation.state.events = []
    stats = MagicMock()
    metrics = MagicMock()
    token_usage = MagicMock()
    token_usage.prompt_tokens = 100
    token_usage.completion_tokens = 50
    metrics.accumulated_token_usage = token_usage
    metrics.accumulated_cost = 0.1
    stats.get_combined_metrics.return_value = metrics
    conversation.conversation_stats = stats
    return conversation


# ---------------------------------------------------------------------------
# PromptPhase parsing of new limit fields
# ---------------------------------------------------------------------------


class TestPromptPhaseParsesLimits:
    def test_max_iterations_default_none(self):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi"})
        assert phase.max_iterations is None
        assert phase.max_tokens is None
        assert phase.max_cost is None

    def test_max_iterations_stored(self):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi", "max_iterations": 20})
        assert phase.max_iterations == 20

    def test_max_tokens_stored(self):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi", "max_tokens": 5000})
        assert phase.max_tokens == 5000

    def test_max_cost_stored(self):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi", "max_cost": 0.5})
        assert phase.max_cost == 0.5


# ---------------------------------------------------------------------------
# PromptPhase.run with CircuitBreaker
# ---------------------------------------------------------------------------


class TestPromptPhaseRunCostControl:
    @pytest.mark.asyncio
    async def test_result_contains_spend(self, mock_conversation):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi"})

        with (
            patch("goblin_cli.phase.build_llm"),
            patch("goblin_cli.phase.Agent") as mock_agent_cls,
            patch("goblin_cli.conversation.LocalConversation", return_value=mock_conversation) as mock_conv_cls,
            patch("goblin_cli.phase.get_agent_final_response", return_value='{"success": true, "message": ["ok"]}'),
        ):
            mock_agent_cls.return_value = MagicMock()
            result = await phase.run({})

        assert "spend" in result
        assert result["spend"].iterations == 1
        assert result["spend"].tokens == 150
        assert result["spend"].cost == 0.1
        mock_conv_cls.assert_called_once()
        call_kwargs = mock_conv_cls.call_args.kwargs
        assert call_kwargs["max_iteration_per_run"] == 500
        assert call_kwargs["max_budget_per_run"] is None

    @pytest.mark.asyncio
    async def test_max_iterations_passed_to_conversation(self, mock_conversation):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi", "max_iterations": 20})

        with (
            patch("goblin_cli.phase.build_llm"),
            patch("goblin_cli.phase.Agent") as mock_agent_cls,
            patch("goblin_cli.conversation.LocalConversation", return_value=mock_conversation) as mock_conv_cls,
            patch("goblin_cli.phase.get_agent_final_response", return_value='{"success": true, "message": ["ok"]}'),
        ):
            mock_agent_cls.return_value = MagicMock()
            result = await phase.run({})

        call_kwargs = mock_conv_cls.call_args.kwargs
        assert call_kwargs["max_iteration_per_run"] == 20
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_max_cost_passed_to_conversation(self, mock_conversation):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi", "max_cost": 0.5})

        with (
            patch("goblin_cli.phase.build_llm"),
            patch("goblin_cli.phase.Agent") as mock_agent_cls,
            patch("goblin_cli.conversation.LocalConversation", return_value=mock_conversation) as mock_conv_cls,
            patch("goblin_cli.phase.get_agent_final_response", return_value='{"success": true, "message": ["ok"]}'),
        ):
            mock_agent_cls.return_value = MagicMock()
            result = await phase.run({})

        call_kwargs = mock_conv_cls.call_args.kwargs
        assert call_kwargs["max_budget_per_run"] == 0.5
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_max_tokens_stops_when_exceeded(self, mock_conversation):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi", "max_tokens": 1})

        with (
            patch("goblin_cli.phase.build_llm"),
            patch("goblin_cli.phase.Agent") as mock_agent_cls,
            patch("goblin_cli.conversation.LocalConversation", return_value=mock_conversation),
        ):
            mock_agent_cls.return_value = MagicMock()
            result = await phase.run({})

        assert result["success"] is False
        assert "max_tokens limit exceeded" in result["message"][0]
        assert "spend" in result
        assert result["spend"].tokens == 150

    @pytest.mark.asyncio
    async def test_max_iterations_stops_when_exceeded(self):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi", "max_iterations": 1})

        conversation = MagicMock()
        conversation.arun = AsyncMock()
        conversation.state.events = []
        conversation.conversation_stats = None

        with (
            patch("goblin_cli.phase.build_llm"),
            patch("goblin_cli.phase.Agent") as mock_agent_cls,
            patch("goblin_cli.conversation.LocalConversation", return_value=conversation),
        ):
            mock_agent_cls.return_value = MagicMock()
            # With max_iterations=1, the breaker fires after the first (and only) arun()
            result1 = await phase.run({})
            assert result1["success"] is False
            assert "max_iterations limit exceeded" in result1["message"][0]
            assert result1["spend"].iterations == 1

    @pytest.mark.asyncio
    async def test_no_text_output_includes_spend(self, mock_conversation):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi"})

        with (
            patch("goblin_cli.phase.build_llm"),
            patch("goblin_cli.phase.Agent") as mock_agent_cls,
            patch("goblin_cli.conversation.LocalConversation", return_value=mock_conversation),
            patch("goblin_cli.phase.get_agent_final_response", return_value=None),
        ):
            mock_agent_cls.return_value = MagicMock()
            result = await phase.run({})

        assert result["success"] is False
        assert "spend" in result
        assert result["spend"].iterations == 1
