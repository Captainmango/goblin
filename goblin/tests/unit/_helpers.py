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


def _make_prompt_phase(raw: dict[str, Any], *, real_blueprint: bool = False) -> PromptPhase:
    """Build a :class:`PromptPhase` backed by a stubbed or real blueprint.

    The default stubbed blueprint avoids the overhead of loading a full
    :class:`V1Blueprint` for tests that only need :class:`PromptPhase` parsing
    or :meth:`PromptPhase.run` wiring. Set ``real_blueprint=True`` when the
    test needs real blueprint resolution (e.g. output_model / output_schema).
    """
    if real_blueprint:
        blueprint = V1Blueprint({"name": "test_bp", "phases": [raw]})
    else:
        blueprint = MagicMock()
        blueprint.build_hooks.return_value = None
        blueprint.build_mcp_servers.return_value = {}
        blueprint.resolve_worktree_config.return_value = None
        blueprint.resolve_notifications_config.return_value = None
        blueprint.get_workspace.return_value = MagicMock()
        blueprint.project_path = None
        blueprint.project_refs = ""
        blueprint.project_agents = {}
    return PromptPhase(raw, blueprint)


def _mock_llm_infra(conversation: MagicMock | None = None) -> tuple:
    """Return the common patchers for mocking the LLM/agent/conversation stack.

    The returned tuple is ordered as:
      1. ``goblin.utils.llm.build_llm``
      2. ``openhands.sdk.Agent`` (returns a :class:`MagicAgent`)
      3. ``goblin.conversation.LocalConversation``
      4. ``openhands.sdk.conversation.get_agent_final_response``
      5. ``goblin.blueprint.V1Blueprint._get_workspace``

    Tests that need extra patches (e.g. for ``extract_structured_result``) can
    add them in the same ``with`` block.

    Args:
        conversation: Optional conversation mock to return from
            ``LocalConversation``. Defaults to a minimal mock with no stats.
    """
    mock_conversation = conversation or MagicMock()
    # Always provide an awaitable arun() so callers don't trip over a lazily
    # created MagicMock when they ``await conversation.arun()``.
    mock_conversation.arun = AsyncMock()
    mock_conversation.state = getattr(mock_conversation, "state", MagicMock())
    mock_conversation.state.events = getattr(mock_conversation.state, "events", [])

    return (
        patch("goblin.utils.llm.build_llm"),
        patch("openhands.sdk.Agent", return_value=MagicAgent()),
        patch("goblin.conversation.LocalConversation", return_value=mock_conversation),
        patch("openhands.sdk.conversation.get_agent_final_response", return_value="mocked response"),
        patch.object(V1Blueprint, "_get_workspace", return_value=MagicMock()),
    )
