"""Shared test helpers — fixtures and patching utilities for goblin tests.

This module's main job is to provide :class:`MagicAgent` so tests that mock
``Agent(...)`` with a bare ``MagicMock`` don't trip OpenHands SDK's stricter
discriminated-union check on ``kind`` (the SDK now rejects agents whose kind
isn't ``"Agent"`` or ``"ACPAgent"`` at construction time).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from goblin.blueprint import V1Blueprint
from goblin.phase import PromptPhase


class MagicAgent(MagicMock):
    """Drop-in replacement for ``MagicMock`` that satisfies the agent-kind check.

    Use this anywhere the code under test patches ``goblin.phase.Agent`` (or
    ``goblin.swarm.Agent``). ``kind`` is the OpenHands SDK's discriminator
    field; setting it to ``"Agent"`` mirrors the real class's default so the
    Pydantic validator picks the right branch and doesn't raise.
    """

    kind: str = "Agent"
    agent_kind: str = "openhands"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.kind = "Agent"
        self.agent_kind = "openhands"


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


def _make_prompt_phase(raw: dict[str, Any]) -> PromptPhase:
    """Build a :class:`PromptPhase` backed by a fully-stubbed blueprint."""
    blueprint = MagicMock()
    blueprint._build_hooks.return_value = None
    blueprint._build_mcp_servers.return_value = {}
    blueprint._resolve_worktree_config.return_value = None
    blueprint._resolve_notifications_config.return_value = None
    blueprint.project_path = None
    blueprint.project_refs = ""
    blueprint.project_agents = {}
    return PromptPhase(raw, blueprint)


def _mock_llm_infra() -> tuple:
    """Return the common patchers for mocking the LLM/agent/conversation stack.

    The returned tuple is ordered as:
      1. ``goblin.utils.llm.build_llm``
      2. ``openhands.sdk.Agent`` (returns a :class:`MagicAgent`)
      3. ``goblin.conversation.LocalConversation``
      4. ``openhands.sdk.conversation.get_agent_final_response``
      5. ``goblin.blueprint.V1Blueprint._get_workspace``

    Tests that need extra patches (e.g. for ``extract_structured_result``) can
    add them in the same ``with`` block.
    """
    mock_conversation = MagicMock()
    mock_conversation.arun = AsyncMock()
    mock_conversation.state.events = []
    mock_conversation.conversation_stats = None

    return (
        patch("goblin.utils.llm.build_llm"),
        patch("openhands.sdk.Agent", return_value=MagicAgent()),
        patch("goblin.conversation.LocalConversation", return_value=mock_conversation),
        patch("openhands.sdk.conversation.get_agent_final_response", return_value="mocked response"),
        patch.object(V1Blueprint, "_get_workspace", return_value=MagicMock()),
    )
