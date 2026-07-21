"""Tests for the `notifications` blueprint config section.

Mirrors the `worktree` config pattern (see test_worktree_tool.py): a
`defaults.notifications` / `phase.notifications` section resolves to a
per-phase `label`, which PromptPhase wires into the AskUser tool's
`tool_params` so blueprint authors can pick which paired operator gets asked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from goblin.blueprint import V1Blueprint
from goblin.phase import PromptPhase
from goblin.validation import validate_blueprint


def _make_blueprint(phases: list[dict], defaults: dict | None = None) -> V1Blueprint:
    spec: dict[str, Any] = {"name": "test_bp", "phases": phases}
    if defaults is not None:
        spec["defaults"] = defaults
    return V1Blueprint(spec)


# ---------------------------------------------------------------------------
# JSON Schema (spec/v1.json)
# ---------------------------------------------------------------------------


class TestNotificationsSchema:
    def test_phase_level_notifications_is_valid(self):
        raw = {
            "version": "v1",
            "blueprint": {
                "name": "test",
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "ask the user",
                        "allowed_tools": ["AskUser"],
                        "notifications": {"label": "ops-oncall"},
                    }
                ],
            },
        }
        assert validate_blueprint("unused.yaml", raw=raw) == []

    def test_defaults_level_notifications_is_valid(self):
        raw = {
            "version": "v1",
            "blueprint": {
                "name": "test",
                "defaults": {"notifications": {"label": "ops-oncall"}},
                "phases": [{"id": "p1", "prompt": "ask the user", "allowed_tools": ["AskUser"]}],
            },
        }
        assert validate_blueprint("unused.yaml", raw=raw) == []

    def test_unknown_notifications_property_is_rejected(self):
        raw = {
            "version": "v1",
            "blueprint": {
                "name": "test",
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "ask the user",
                        "notifications": {"label": "ops-oncall", "bogus": True},
                    }
                ],
            },
        }
        errors = validate_blueprint("unused.yaml", raw=raw)
        assert errors != []


# ---------------------------------------------------------------------------
# _resolve_notifications_config
# ---------------------------------------------------------------------------


class TestResolveNotificationsConfig:
    def test_no_config_returns_none(self):
        bp = _make_blueprint([{"id": "p1", "prompt": "hi"}])
        phase = bp.phases[0]
        assert bp._resolve_notifications_config(phase) is None

    def test_defaults_only(self):
        bp = _make_blueprint(
            [{"id": "p1", "prompt": "hi"}],
            defaults={"notifications": {"label": "ops-oncall"}},
        )
        phase = bp.phases[0]
        assert bp._resolve_notifications_config(phase) == {"label": "ops-oncall"}

    def test_phase_only(self):
        bp = _make_blueprint([{"id": "p1", "prompt": "hi", "notifications": {"label": "reviewer"}}])
        phase = bp.phases[0]
        assert bp._resolve_notifications_config(phase) == {"label": "reviewer"}

    def test_phase_overrides_defaults(self):
        bp = _make_blueprint(
            [{"id": "p1", "prompt": "hi", "notifications": {"label": "reviewer"}}],
            defaults={"notifications": {"label": "ops-oncall"}},
        )
        phase = bp.phases[0]
        assert bp._resolve_notifications_config(phase) == {"label": "reviewer"}

    def test_phase_merges_over_defaults(self):
        bp = _make_blueprint(
            [{"id": "p1", "prompt": "hi", "notifications": {"label": "reviewer"}}],
            defaults={"notifications": {"label": "ops-oncall", "extra": "kept"}},
        )
        phase = bp.phases[0]
        assert bp._resolve_notifications_config(phase) == {"label": "reviewer", "extra": "kept"}


# ---------------------------------------------------------------------------
# PromptPhase -> AskUser tool_params wiring
# ---------------------------------------------------------------------------


def _make_prompt_phase(raw: dict) -> PromptPhase:
    blueprint = MagicMock()
    blueprint._build_hooks.return_value = None
    blueprint._build_mcp_servers.return_value = {}
    blueprint._resolve_worktree_config.return_value = None
    blueprint._resolve_notifications_config.return_value = None
    blueprint.project_path = None
    blueprint.project_refs = ""
    blueprint.project_agents = {}
    return PromptPhase(raw, blueprint)


def _mock_conversation() -> MagicMock:
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


class TestPromptPhaseAskUserToolParams:
    @pytest.mark.asyncio
    async def test_ask_user_not_requested_gets_no_tool_params(self):
        phase = _make_prompt_phase({"id": "p1", "prompt": "hi", "allowed_tools": ["Bash"]})
        local_ws = MagicMock()
        local_ws.__class__.__name__ = "LocalWorkspace"
        phase._blueprint._get_workspace.return_value = local_ws

        with (
            patch("goblin.utils.llm.build_llm"),
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.tools.tool_map.resolve_tools") as mock_resolve_tools,
            patch("goblin.conversation.LocalConversation", return_value=_mock_conversation()),
            patch(
                "openhands.sdk.conversation.get_agent_final_response",
                return_value='{"success": true, "message": ["ok"]}',
            ),
        ):
            mock_agent_cls.return_value = MagicMock()
            await phase.run({})

        tool_params = mock_resolve_tools.call_args.kwargs["tool_params"]
        assert "AskUser" not in tool_params
        phase._blueprint._resolve_notifications_config.assert_not_called()

    @pytest.mark.asyncio
    async def test_ask_user_gets_resolved_label(self):
        phase = _make_prompt_phase({"id": "p1", "prompt": "hi", "allowed_tools": ["AskUser"]})
        phase._blueprint._resolve_notifications_config.return_value = {"label": "ops-oncall"}
        local_ws = MagicMock()
        local_ws.__class__.__name__ = "LocalWorkspace"
        phase._blueprint._get_workspace.return_value = local_ws

        with (
            patch("goblin.utils.llm.build_llm"),
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.tools.tool_map.resolve_tools") as mock_resolve_tools,
            patch("goblin.conversation.LocalConversation", return_value=_mock_conversation()),
            patch(
                "openhands.sdk.conversation.get_agent_final_response",
                return_value='{"success": true, "message": ["ok"]}',
            ),
        ):
            mock_agent_cls.return_value = MagicMock()
            await phase.run({})

        tool_params = mock_resolve_tools.call_args.kwargs["tool_params"]
        assert tool_params["AskUser"] == {"label": "ops-oncall"}

    @pytest.mark.asyncio
    async def test_ask_user_with_no_config_gets_no_label_override(self):
        phase = _make_prompt_phase({"id": "p1", "prompt": "hi", "allowed_tools": ["AskUser"]})
        local_ws = MagicMock()
        local_ws.__class__.__name__ = "LocalWorkspace"
        phase._blueprint._get_workspace.return_value = local_ws

        with (
            patch("goblin.utils.llm.build_llm"),
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.tools.tool_map.resolve_tools") as mock_resolve_tools,
            patch("goblin.conversation.LocalConversation", return_value=_mock_conversation()),
            patch(
                "openhands.sdk.conversation.get_agent_final_response",
                return_value='{"success": true, "message": ["ok"]}',
            ),
        ):
            mock_agent_cls.return_value = MagicMock()
            await phase.run({})

        tool_params = mock_resolve_tools.call_args.kwargs["tool_params"]
        assert tool_params["AskUser"] == {"label": None}
