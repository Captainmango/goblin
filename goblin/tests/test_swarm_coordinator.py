"""Tests for the swarm coordinator's chat-text wrap behaviour."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from goblin.output_schema import SwarmCoordinatorResult
from goblin.swarm import SwarmPhase
from tests._helpers import MINIMAL_SWARM_RAW, _mock_llm_infra


def _make_blueprint_with_swarm(raw: dict) -> SwarmPhase:
    from goblin.blueprint import V1Blueprint

    bp = V1Blueprint({"name": "test", "phases": [raw]})
    return bp.phases[0]  # type: ignore[return-value]


def _make_conv(events=None):
    conversation = MagicMock()
    conversation.arun = AsyncMock()
    conversation.state.events = events or []
    conversation.conversation_stats = None
    return conversation


class TestSwarmCoordinatorChatTextWrap:
    """Default behaviour: chat text gets wrapped into a SwarmCoordinatorResult."""

    @pytest.mark.asyncio
    async def test_coordinator_wraps_chat_text(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "workspace": str(tmp_path / "ws"),
                "cleanup": "never",
            },
        }
        phase = _make_blueprint_with_swarm(raw)
        phase._setup({})

        p1, p2, p3, p4, p5 = _mock_llm_infra(_make_conv())
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="still work to do"),
        ):
            breaker = MagicMock()
            result = await phase._run_coordinator({}, breaker)

        # Plain text that does not parse as SwarmCoordinatorResult is treated
        # as "more work pending".
        assert result["done"] is False
        assert result["message"] == ["still work to do"]

    @pytest.mark.asyncio
    async def test_coordinator_empty_text_synthesizes_placeholder(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "workspace": str(tmp_path / "ws"),
                "cleanup": "never",
            },
        }
        phase = _make_blueprint_with_swarm(raw)
        phase._setup({})

        p1, p2, p3, p4, p5 = _mock_llm_infra(_make_conv())
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch("openhands.sdk.conversation.get_agent_final_response", return_value=""),
        ):
            breaker = MagicMock()
            result = await phase._run_coordinator({}, breaker)

        assert result["done"] is False
        assert result["message"] == ["No response from coordinator"]

    @pytest.mark.asyncio
    async def test_coordinator_honours_json_done_signal(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "workspace": str(tmp_path / "ws"),
                "cleanup": "never",
            },
        }
        phase = _make_blueprint_with_swarm(raw)
        phase._setup({})

        p1, p2, p3, p4, p5 = _mock_llm_infra(_make_conv())
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch(
                "openhands.sdk.conversation.get_agent_final_response",
                return_value='{"done": true, "message": ["all tasks complete"]}',
            ),
        ):
            breaker = MagicMock()
            result = await phase._run_coordinator({}, breaker)

        assert result["done"] is True
        assert result["message"] == ["all tasks complete"]

    @pytest.mark.asyncio
    async def test_coordinator_does_not_register_structured_finish(self, tmp_path):
        """The coordinator no longer wires the typed Finish tool by default."""
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "workspace": str(tmp_path / "ws"),
                "cleanup": "never",
            },
        }
        phase = _make_blueprint_with_swarm(raw)
        phase._setup({})

        captured_tools: list = []

        class FakeAgent:
            def __init__(self, *, tools, **kwargs: Any) -> None:
                captured_tools.extend(tools)

        p1, p2, p3, p4, p5 = _mock_llm_infra(_make_conv())
        with (
            p1,
            p2,
            p3,
            patch("openhands.sdk.Agent", FakeAgent),
            p4,
            p5,
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="ok"),
        ):
            breaker = MagicMock()
            await phase._run_coordinator({}, breaker)

        names = {t.name for t in captured_tools}
        assert "swarm_tasks" in names
        assert "structured_finish" not in names


class TestSwarmCoordinatorResultModel:
    def test_default_done_false_message(self):
        r = SwarmCoordinatorResult(done=False, message=["x"])
        assert r.done is False
        assert r.message == ["x"]

    def test_extra_fields_allowed(self):
        r = SwarmCoordinatorResult(done=True, message=["x"], next_round_summary="brief")
        dumped = r.model_dump()
        assert dumped["next_round_summary"] == "brief"
