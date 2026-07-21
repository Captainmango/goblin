"""Tests for PromptPhase workspace switching — local vs remote conversation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from goblin.output_schema import PhaseResult
from goblin.phase import PromptPhase
from tests._helpers import MagicAgent


def _make_prompt_phase(raw: dict) -> PromptPhase:
    blueprint = MagicMock()
    blueprint._build_hooks.return_value = None
    blueprint._build_mcp_servers.return_value = {}
    blueprint.project_path = None
    blueprint.project_refs = ""
    blueprint.project_agents = {}
    return PromptPhase(raw, blueprint)


@pytest.fixture
def mock_local_conversation():
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


@pytest.fixture
def mock_remote_conversation():
    conversation = MagicMock()
    conversation.run = MagicMock()
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
# Local workspace
# ---------------------------------------------------------------------------


class TestPromptPhaseLocalWorkspace:
    @pytest.mark.asyncio
    async def test_uses_local_conversation_for_local_workspace(self, mock_local_conversation):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi"})
        local_ws = MagicMock()
        local_ws.__class__.__name__ = "LocalWorkspace"
        phase._blueprint._get_workspace.return_value = local_ws

        with (
            patch("goblin.utils.llm.build_llm"),
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation", return_value=mock_local_conversation) as mock_conv_cls,
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="ok"),
        ):
            mock_agent_cls.return_value = MagicAgent()
            result = await phase.run({})

        assert result["success"] is True
        mock_conv_cls.assert_called_once()
        call_kwargs = mock_conv_cls.call_args.kwargs
        assert call_kwargs["workspace"] is local_ws

    @pytest.mark.asyncio
    async def test_calls_arun_for_local_workspace(self, mock_local_conversation):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi"})
        local_ws = MagicMock()
        local_ws.__class__.__name__ = "LocalWorkspace"
        phase._blueprint._get_workspace.return_value = local_ws

        with (
            patch("goblin.utils.llm.build_llm"),
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation", return_value=mock_local_conversation),
            patch(
                "goblin.tools.structured_finish.extract_structured_result",
                return_value=PhaseResult(success=True, message=["ok"]),
            ),
        ):
            mock_agent_cls.return_value = MagicAgent()
            await phase.run({})

        mock_local_conversation.arun.assert_awaited_once()


# ---------------------------------------------------------------------------
# Remote workspace
# ---------------------------------------------------------------------------


class _FakeRemoteWorkspace:
    pass


class TestPromptPhaseRemoteWorkspace:
    @pytest.mark.asyncio
    async def test_uses_remote_conversation_for_remote_workspace(self, mock_remote_conversation):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi"})
        remote_ws = _FakeRemoteWorkspace()
        phase._blueprint._get_workspace.return_value = remote_ws

        with (
            patch("goblin.utils.llm.build_llm"),
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.conversation.RemoteWorkspace", _FakeRemoteWorkspace),
            patch("goblin.conversation.RemoteConversation", return_value=mock_remote_conversation) as mock_conv_cls,
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="ok"),
        ):
            mock_agent_cls.return_value = MagicAgent()
            result = await phase.run({})

        assert result["success"] is True
        mock_conv_cls.assert_called_once()
        call_kwargs = mock_conv_cls.call_args.kwargs
        assert call_kwargs["workspace"] is remote_ws

    @pytest.mark.asyncio
    async def test_wraps_run_in_asyncio_to_thread(self, mock_remote_conversation):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi"})
        remote_ws = _FakeRemoteWorkspace()
        phase._blueprint._get_workspace.return_value = remote_ws

        with (
            patch("goblin.utils.llm.build_llm"),
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.conversation.RemoteWorkspace", _FakeRemoteWorkspace),
            patch("goblin.conversation.RemoteConversation", return_value=mock_remote_conversation),
            patch("goblin.conversation.asyncio.to_thread") as mock_to_thread,
            patch(
                "goblin.tools.structured_finish.extract_structured_result",
                return_value=PhaseResult(success=True, message=["ok"]),
            ),
        ):
            mock_agent_cls.return_value = MagicAgent()
            mock_to_thread.return_value = AsyncMock()
            await phase.run({})

        mock_to_thread.assert_awaited_once()
        assert mock_to_thread.call_args.args[0] is mock_remote_conversation.run


# ---------------------------------------------------------------------------
# Workspace passed to _get_workspace
# ---------------------------------------------------------------------------


class TestPromptPhaseGetWorkspaceCalled:
    @pytest.mark.asyncio
    async def test_get_workspace_called_with_context(self, mock_local_conversation):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hi"})
        local_ws = MagicMock()
        local_ws.__class__.__name__ = "LocalWorkspace"
        phase._blueprint._get_workspace.return_value = local_ws

        with (
            patch("goblin.utils.llm.build_llm"),
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation", return_value=mock_local_conversation),
            patch(
                "goblin.tools.structured_finish.extract_structured_result",
                return_value=PhaseResult(success=True, message=["ok"]),
            ),
        ):
            mock_agent_cls.return_value = MagicAgent()
            await phase.run({"repo": "my-service"})

        phase._blueprint._get_workspace.assert_called_once_with({"repo": "my-service"})
