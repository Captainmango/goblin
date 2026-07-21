"""Tests for PromptPhase cost control integration — CircuitBreaker, limits, and spend reporting."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from goblin.output_schema import PhaseResult
from tests._helpers import _make_prompt_phase, _mock_llm_infra

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

        p1, p2, p3, p4, p5 = _mock_llm_infra(mock_conversation)
        with (
            p1,
            p2,
            p3 as mock_conv_cls,
            p4,
            p5,
            patch(
                "goblin.tools.structured_finish.extract_structured_result",
                return_value=PhaseResult(success=True, message=["ok"]),
            ),
        ):
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

        p1, p2, p3, p4, p5 = _mock_llm_infra(mock_conversation)
        with (
            p1,
            p2,
            p3 as mock_conv_cls,
            p4,
            p5,
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="ok"),
        ):
            result = await phase.run({})

        call_kwargs = mock_conv_cls.call_args.kwargs
        assert call_kwargs["max_iteration_per_run"] == 20
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_max_cost_passed_to_conversation(self, mock_conversation):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi", "max_cost": 0.5})

        p1, p2, p3, p4, p5 = _mock_llm_infra(mock_conversation)
        with (
            p1,
            p2,
            p3 as mock_conv_cls,
            p4,
            p5,
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="ok"),
        ):
            result = await phase.run({})

        call_kwargs = mock_conv_cls.call_args.kwargs
        assert call_kwargs["max_budget_per_run"] == 0.5
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_max_tokens_stops_when_exceeded(self, mock_conversation):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi", "max_tokens": 1})

        p1, p2, p3, p4, p5 = _mock_llm_infra(mock_conversation)
        with p1, p2, p3, p4, p5:
            result = await phase.run({})

        assert result["success"] is False
        assert "max_tokens limit exceeded" in result["message"][0]
        assert "spend" in result
        assert result["spend"].tokens == 150

    @pytest.mark.asyncio
    async def test_max_iterations_stops_when_exceeded(self):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi", "max_iterations": 1})

        p1, p2, p3, p4, p5 = _mock_llm_infra()
        with p1, p2, p3, p4, p5:
            # With max_iterations=1, the breaker fires after the first (and only) arun()
            result1 = await phase.run({})
            assert result1["success"] is False
            assert "max_iterations limit exceeded" in result1["message"][0]
            assert result1["spend"].iterations == 1

    @pytest.mark.asyncio
    async def test_no_text_output_includes_spend(self, mock_conversation):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi"})

        p1, p2, p3, p4, p5 = _mock_llm_infra(mock_conversation)
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch("openhands.sdk.conversation.get_agent_final_response", return_value=None),
        ):
            result = await phase.run({})

        # Empty agent reply reports failure; the spend tracker is still
        # populated from the conversation.
        assert result["success"] is False
        assert "spend" in result
        assert result["spend"].iterations == 1
