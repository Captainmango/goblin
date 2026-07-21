"""Tests for blueprint/phase/swarm model resolution."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from goblin.blueprint import V1Blueprint
from goblin.phase import PromptPhase
from goblin.swarm import SwarmPhase

from tests._helpers import MINIMAL_SWARM_RAW, MagicAgent

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
            patch("goblin.utils.llm.build_llm") as mock_build_llm,
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation", return_value=conversation),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="all done"),
        ):
            mock_agent_cls.return_value = MagicAgent()
            await phase.run({})

        # Default flow: no structured output, so build_llm receives no
        # output_model and the harness wraps the chat text afterwards.
        assert mock_build_llm.call_count == 1
        kwargs = mock_build_llm.call_args.kwargs
        assert kwargs["model"] == "openai/gpt-4o"
        assert "output_model" not in kwargs

    @pytest.mark.asyncio
    async def test_structured_output_passes_model_to_build_llm(self):
        """Structured-output opt-in: ``output_model`` flows through build_llm.

        When a phase declares ``output_model`` or ``output_schema``,
        PromptPhase wires up the typed Finish tool and ``response_format``
        follow-up. The primary and follow-up ``build_llm`` calls both
        forward the same resolved model id.
        """
        bp = V1Blueprint(
            {
                "name": "test",
                "defaults": {"model": "anthropic/claude-opus-4"},
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hi",
                        "model": "openai/gpt-4o",
                        "output_model": "goblin.output_schema.PhaseResult",
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)

        primary_conversation = MagicMock()
        primary_conversation.arun = AsyncMock()
        primary_conversation.state.events = []
        primary_conversation.conversation_stats = None

        followup_conversation = MagicMock()
        followup_conversation.arun = AsyncMock()
        followup_conversation.state.events = []
        followup_conversation.conversation_stats = None

        def make_conv(*args, **kwargs):
            return primary_conversation if kwargs.get("hook_config") is not None else followup_conversation

        with (
            patch("goblin.utils.llm.build_llm") as mock_build_llm,
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.tools.structured_finish.extract_structured_result", return_value=None),
            patch("goblin.conversation.LocalConversation", side_effect=make_conv),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value=""),
        ):
            mock_agent_cls.return_value = MagicAgent()
            mock_build_llm.return_value = MagicAgent()
            await phase.run({})

        assert mock_build_llm.call_count == 2
        for call_args in mock_build_llm.call_args_list:
            assert call_args.kwargs["model"] == "openai/gpt-4o"


# ---------------------------------------------------------------------------
# PromptPhase structured-output follow-up
# ---------------------------------------------------------------------------


class TestPromptPhaseStructuredFollowUp:
    @pytest.mark.asyncio
    async def test_follow_up_includes_prior_text_and_uses_structured_prompt(self):
        """J-3: the Layer-2 follow-up must see the prior text and use a structured system prompt."""
        bp = V1Blueprint(
            {
                "name": "test",
                "defaults": {"model": "openai/gpt-4o"},
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "Summarize the codebase.",
                        "model": "openai/gpt-4o",
                        "output_model": "goblin.output_schema.PhaseResult",
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)

        primary_conversation = MagicMock()
        primary_conversation.arun = AsyncMock()
        primary_conversation.state.events = []
        primary_conversation.conversation_stats = None

        followup_conversation = MagicMock()
        followup_conversation.arun = AsyncMock()
        followup_conversation.state.events = []
        followup_conversation.conversation_stats = None

        def make_conv(*args, **kwargs):
            return primary_conversation if kwargs.get("hook_config") is not None else followup_conversation

        captured_agents: list[dict] = []

        def capture_agent(*args, **kwargs):
            captured_agents.append(kwargs)
            return MagicAgent()

        with (
            patch("goblin.utils.llm.build_llm") as mock_build_llm,
            patch("openhands.sdk.Agent", side_effect=capture_agent),
            patch("goblin.tools.structured_finish.extract_structured_result", return_value=None),
            patch("goblin.conversation.LocalConversation", side_effect=make_conv),
            patch("openhands.sdk.conversation.get_agent_final_response", side_effect=["prior final text", ""]),
        ):
            mock_build_llm.return_value = MagicAgent()
            await phase.run({})

        assert len(captured_agents) == 2
        follow_up_agent = captured_agents[1]

        # Follow-up agent uses the dedicated structured follow-up prompt, not
        # the plain-text BASE_SYSTEM_PROMPT.
        system_prompt = follow_up_agent["system_prompt"]
        assert "previous run produced an answer but not in the required structured format" in system_prompt.lower()
        assert "call the `finish` tool" not in system_prompt.lower()

        # Follow-up prompt includes the prior run's final text and the original task.
        follow_up_prompt = followup_conversation.send_message.call_args[0][0]
        assert "prior final text" in follow_up_prompt
        assert "Summarize the codebase." in follow_up_prompt


# ---------------------------------------------------------------------------
# PromptPhase structured-output fallback
# ---------------------------------------------------------------------------


class TestPromptPhaseStructuredFallback:
    @pytest.mark.asyncio
    async def test_fallback_contract(self):
        """TS-3: when no layer produces structured output the phase fails cleanly
        and the follow-up call carries the structured prompt and output_model.
        """
        bp = V1Blueprint(
            {
                "name": "test",
                "defaults": {"model": "openai/gpt-4o"},
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "Say something structured.",
                        "output_model": "goblin.output_schema.PhaseResult",
                    }
                ],
            }
        )
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)

        primary_conversation = MagicMock()
        primary_conversation.arun = AsyncMock()
        primary_conversation.state.events = []
        primary_conversation.conversation_stats = None

        followup_conversation = MagicMock()
        followup_conversation.arun = AsyncMock()
        followup_conversation.state.events = []
        followup_conversation.conversation_stats = None

        def make_conv(*args, **kwargs):
            return primary_conversation if kwargs.get("hook_config") is not None else followup_conversation

        with (
            patch("goblin.utils.llm.build_llm") as mock_build_llm,
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.tools.structured_finish.extract_structured_result", return_value=None),
            patch("goblin.conversation.LocalConversation", side_effect=make_conv),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value=""),
        ):
            mock_agent_cls.return_value = MagicAgent()
            mock_build_llm.return_value = MagicAgent()
            result = await phase.run({})

        assert result["success"] is False
        assert any("Agent produced no structured output" in m for m in result["message"])

        # Follow-up prompt tells the model why it is being called again.
        follow_up_prompt = followup_conversation.send_message.call_args[0][0]
        assert "did not return a structured answer" in follow_up_prompt

        # The follow-up LLM is built with the resolved output model so the
        # provider receives the right structured-output payload.
        assert mock_build_llm.call_count == 2
        follow_up_call = mock_build_llm.call_args_list[1]
        assert follow_up_call.kwargs["output_model"] is phase._resolve_output_model()


# ---------------------------------------------------------------------------
# SwarmPhase model propagation
# ---------------------------------------------------------------------------


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
            patch("goblin.utils.llm.build_llm") as mock_build_llm,
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation") as mock_conv_cls,
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="still work to do"),
        ):
            mock_agent_cls.return_value = MagicAgent()
            mock_conv_cls.return_value = MagicMock()
            mock_conv_cls.return_value.arun = AsyncMock()
            mock_conv_cls.return_value.state.events = []
            mock_conv_cls.return_value.conversation_stats = None
            breaker = MagicMock()
            await phase._run_coordinator({}, breaker)

        # Coordinator default: chat text → wrapped SwarmCoordinatorResult.
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
            patch("goblin.utils.llm.build_llm") as mock_build_llm,
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation") as mock_conv_cls,
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="still work to do"),
        ):
            mock_agent_cls.return_value = MagicAgent()
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
            patch("goblin.utils.llm.build_llm") as mock_build_llm,
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation") as mock_conv_cls,
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
        ):
            mock_agent_cls.return_value = MagicAgent()
            mock_conv_cls.return_value = MagicMock()
            mock_conv_cls.return_value.arun = AsyncMock()
            mock_conv_cls.return_value.state.events = []
            mock_conv_cls.return_value.conversation_stats = None
            breaker = MagicMock()
            await phase._run_single_agent(phase.agents_config[0], {}, breaker)

        # Worker agents don't get output_model — their text output is consumed
        # by the coordinator, not validated against a typed Finish tool.
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
            patch("goblin.utils.llm.build_llm") as mock_build_llm,
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation") as mock_conv_cls,
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
        ):
            mock_agent_cls.return_value = MagicAgent()
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
            patch("goblin.utils.llm.build_llm") as mock_build_llm,
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation") as mock_conv_cls,
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="still work to do"),
        ):
            mock_agent_cls.return_value = MagicAgent()
            mock_conv_cls.return_value = MagicMock()
            mock_conv_cls.return_value.arun = AsyncMock()
            mock_conv_cls.return_value.state.events = []
            mock_conv_cls.return_value.conversation_stats = None
            breaker = MagicMock()
            await phase._run_coordinator({}, breaker)

        mock_build_llm.assert_called_once_with(model="anthropic/claude-opus-4")
