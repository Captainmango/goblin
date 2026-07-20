"""Shared test helpers — fixtures and patching utilities for goblin tests.

This module's main job is to provide :class:`MagicAgent` so tests that mock
``Agent(...)`` with a bare ``MagicMock`` don't trip OpenHands SDK's stricter
discriminated-union check on ``kind`` (the SDK now rejects agents whose kind
isn't ``"Agent"`` or ``"ACPAgent"`` at construction time).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock


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
