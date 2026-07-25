"""Tests for the ACP harness execution branch in PromptPhase."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from goblin.blueprint import V1Blueprint
from goblin.output_schema import PhaseResult
from goblin.phase import PromptPhase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_acp_blueprint(phase_harness: dict | None = None, defaults_harness: dict | None = None) -> V1Blueprint:
    spec: dict[str, Any] = {"name": "test_bp", "phases": [{"id": "p1", "prompt": "hi"}]}
    if phase_harness is not None:
        spec["phases"][0]["harness"] = phase_harness
    if defaults_harness is not None:
        spec["defaults"] = {"harness": defaults_harness}
    return V1Blueprint(spec)


def _fake_conversation(final_text: str = "ACP did the thing") -> MagicMock:
    conversation = MagicMock()
    conversation.arun = AsyncMock()
    conversation.state.events = []
    conversation.conversation_stats = None
    conversation.close = MagicMock()
    return conversation


def _conversation_with_cost(cost: float) -> MagicMock:
    """Return a fake conversation whose reported cost trips the circuit breaker."""
    conversation = _fake_conversation()
    metrics = MagicMock()
    metrics.accumulated_cost = cost
    token_usage = MagicMock()
    token_usage.prompt_tokens = 0
    token_usage.completion_tokens = 0
    metrics.accumulated_token_usage = token_usage
    conversation.conversation_stats = MagicMock()
    conversation.conversation_stats.get_combined_metrics.return_value = metrics
    return conversation


def _fake_settings(agent: MagicMock | None = None) -> MagicMock:
    settings = MagicMock()
    settings.create_agent.return_value = agent or MagicMock()
    return settings


# ---------------------------------------------------------------------------
# Branch selection
# ---------------------------------------------------------------------------


class TestHarnessBranchSelection:
    @pytest.mark.asyncio
    async def test_no_harness_uses_openhands_path(self):
        bp = V1Blueprint({"name": "test_bp", "phases": [{"id": "p1", "prompt": "hi"}]})
        phase = bp.phases[0]
        assert isinstance(phase, PromptPhase)

        conversation = _fake_conversation()
        with (
            patch("goblin.utils.llm.build_llm") as mock_build_llm,
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation", return_value=conversation),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
        ):
            mock_agent_cls.return_value = MagicMock()
            result = await phase.run({})

        assert result["success"] is True
        mock_build_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_openhands_harness_uses_openhands_path(self):
        bp = _make_acp_blueprint(phase_harness={"type": "openhands"})
        phase = bp.phases[0]

        conversation = _fake_conversation()
        with (
            patch("goblin.utils.llm.build_llm") as mock_build_llm,
            patch("openhands.sdk.Agent") as mock_agent_cls,
            patch("goblin.conversation.LocalConversation", return_value=conversation),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
        ):
            mock_agent_cls.return_value = MagicMock()
            result = await phase.run({})

        assert result["success"] is True
        mock_build_llm.assert_called_once()

    @pytest.mark.asyncio
    async def test_acp_harness_uses_acp_path(self):
        bp = _make_acp_blueprint(phase_harness={"type": "acp"})
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", return_value=conversation),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="acp result"),
        ):
            result = await phase.run({"task": "do it"})

        assert result["success"] is True
        assert result["output"] == "acp result"
        assert "acp result" in result["message"]


# ---------------------------------------------------------------------------
# Settings mapping
# ---------------------------------------------------------------------------


class TestACPSettingsMapping:
    @pytest.mark.asyncio
    async def test_all_harness_fields_mapped_to_settings(self):
        bp = _make_acp_blueprint(
            phase_harness={
                "type": "acp",
                "server": "codex",
                "command": ["/usr/local/bin/codex"],
                "args": ["--acp"],
                "model": "gpt-5.5",
                "session_mode": "full-access",
                "prompt_timeout": 300,
                "isolate_data_dir": True,
                "working_dir": "/tmp/repo",
            }
        )
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings) as mock_settings_cls,
            patch("goblin.conversation.run_conversation", return_value=conversation),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
            patch.object(bp, "build_mcp_servers", return_value={"mcpServers": {}}),
        ):
            await phase.run({"task": "hi"})

        mock_settings_cls.assert_called_once()
        kwargs = mock_settings_cls.call_args.kwargs
        assert kwargs["acp_server"] == "codex"
        assert kwargs["acp_command"] == ["/usr/local/bin/codex"]
        assert kwargs["acp_args"] == ["--acp"]
        assert kwargs["acp_model"] == "gpt-5.5"
        assert kwargs["acp_session_mode"] == "full-access"
        assert kwargs["acp_prompt_timeout"] == 300
        assert kwargs["acp_isolate_data_dir"] is True

    @pytest.mark.asyncio
    async def test_settings_defaults(self):
        bp = _make_acp_blueprint(phase_harness={"type": "acp"})
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings) as mock_settings_cls,
            patch("goblin.conversation.run_conversation", return_value=conversation),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
        ):
            await phase.run({"task": "hi"})

        kwargs = mock_settings_cls.call_args.kwargs
        assert kwargs["acp_server"] == "claude-code"
        assert kwargs["acp_command"] == []
        assert kwargs["acp_args"] == []
        assert kwargs["acp_model"] is None
        assert kwargs["acp_session_mode"] is None
        assert kwargs["acp_prompt_timeout"] == 1800.0
        assert kwargs["acp_isolate_data_dir"] is False

    @pytest.mark.asyncio
    async def test_mcp_config_passed_to_settings(self):
        bp = _make_acp_blueprint(phase_harness={"type": "acp"})
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        mcp_config = {"mcpServers": {"github": {"command": "npx"}}}
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings) as mock_settings_cls,
            patch("goblin.conversation.run_conversation", return_value=conversation),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
            patch.object(bp, "build_mcp_servers", return_value=mcp_config),
        ):
            await phase.run({"task": "hi"})

        kwargs = mock_settings_cls.call_args.kwargs
        assert kwargs["mcp_config"] == mcp_config


# ---------------------------------------------------------------------------
# working_dir fallback chain
# ---------------------------------------------------------------------------


class TestACPWorkingDirFallback:
    @pytest.mark.asyncio
    async def test_harness_working_dir_wins(self):
        bp = V1Blueprint(
            {
                "name": "test_bp",
                "workspace": {"type": "local", "working_dir": "/workspace"},
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hi",
                        "harness": {"type": "acp", "working_dir": "/harness"},
                    }
                ],
            }
        )
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", return_value=conversation),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
            patch("openhands.sdk.LocalWorkspace") as mock_ws_cls,
        ):
            await phase.run({})

        mock_ws_cls.assert_called_once_with(working_dir="/harness")

    @pytest.mark.asyncio
    async def test_workspace_working_dir_fallback(self):
        bp = V1Blueprint(
            {
                "name": "test_bp",
                "workspace": {"type": "local", "working_dir": "/workspace"},
                "phases": [{"id": "p1", "prompt": "hi", "harness": {"type": "acp"}}],
            }
        )
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", return_value=conversation),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
            patch("openhands.sdk.LocalWorkspace") as mock_ws_cls,
        ):
            await phase.run({})

        mock_ws_cls.assert_called_once_with(working_dir="/workspace")

    @pytest.mark.asyncio
    async def test_project_path_fallback(self):
        bp = V1Blueprint(
            {
                "name": "test_bp",
                "phases": [{"id": "p1", "prompt": "hi", "harness": {"type": "acp"}}],
            }
        )
        bp.project_path = "/project"
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", return_value=conversation),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
            patch("openhands.sdk.LocalWorkspace") as mock_ws_cls,
        ):
            await phase.run({})

        mock_ws_cls.assert_called_once_with(working_dir="/project")

    @pytest.mark.asyncio
    async def test_cwd_final_fallback(self):
        bp = _make_acp_blueprint(phase_harness={"type": "acp"})
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", return_value=conversation),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
            patch("openhands.sdk.LocalWorkspace") as mock_ws_cls,
        ):
            await phase.run({"task": "hi"})

        mock_ws_cls.assert_called_once_with(working_dir=".")


# ---------------------------------------------------------------------------
# Secrets and remote workspace guard
# ---------------------------------------------------------------------------


class TestACPSecretsAndGuards:
    @pytest.mark.asyncio
    async def test_secrets_passed_to_run_conversation(self):
        bp = _make_acp_blueprint(
            phase_harness={
                "type": "acp",
                "secrets": {"ANTHROPIC_API_KEY": "secret-value"},
            }
        )
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", return_value=conversation) as mock_run,
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
        ):
            await phase.run({"task": "hi"})

        kwargs = mock_run.call_args.kwargs
        assert kwargs["secrets"] == {"ANTHROPIC_API_KEY": "secret-value"}

    @pytest.mark.asyncio
    async def test_no_secrets_passes_none(self):
        bp = _make_acp_blueprint(phase_harness={"type": "acp"})
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", return_value=conversation) as mock_run,
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
        ):
            await phase.run({"task": "hi"})

        kwargs = mock_run.call_args.kwargs
        assert kwargs["secrets"] is None

    @pytest.mark.asyncio
    async def test_remote_workspace_rejected(self):
        bp = V1Blueprint(
            {
                "name": "test_bp",
                "workspace": {"type": "remote", "host": "http://example.com"},
                "phases": [{"id": "p1", "prompt": "hi", "harness": {"type": "acp"}}],
            }
        )
        phase = bp.phases[0]

        result = await phase.run({})

        assert result["success"] is False
        assert "remote workspace" in result["message"][0].lower()


# ---------------------------------------------------------------------------
# MCP forwarding
# ---------------------------------------------------------------------------


class TestACPMcpForwarding:
    @pytest.mark.asyncio
    async def test_defaults_and_phase_mcp_servers_merged(self):
        bp = V1Blueprint(
            {
                "name": "test_bp",
                "defaults": {
                    "mcp_servers": {
                        "shared": {
                            "command": "npx",
                            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                        }
                    }
                },
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hi",
                        "harness": {"type": "acp"},
                        "mcp_servers": {
                            "local": {
                                "command": "uvx",
                                "args": ["mcp-server-time"],
                            }
                        },
                    }
                ],
            }
        )
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings) as mock_settings_cls,
            patch("goblin.conversation.run_conversation", return_value=conversation),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
        ):
            await phase.run({"task": "hi"})

        kwargs = mock_settings_cls.call_args.kwargs
        mcp_config = kwargs["mcp_config"]
        assert "shared" in mcp_config["mcpServers"]
        assert "local" in mcp_config["mcpServers"]
        assert mcp_config["mcpServers"]["local"]["command"] == "uvx"


# ---------------------------------------------------------------------------
# Failure paths and cleanup
# ---------------------------------------------------------------------------


class TestACPFailurePaths:
    @pytest.mark.asyncio
    async def test_create_agent_validation_error_returns_failed_result(self):
        bp = _make_acp_blueprint(phase_harness={"type": "acp", "server": "custom"})
        phase = bp.phases[0]

        settings = MagicMock()
        settings.create_agent.side_effect = ValueError("command is required for custom server")
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation") as mock_run,
        ):
            result = await phase.run({"task": "hi"})

        assert result["success"] is False
        assert "command is required" in result["message"][0]
        mock_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_conversation_closed_on_success(self):
        bp = _make_acp_blueprint(phase_harness={"type": "acp"})
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", return_value=conversation),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
        ):
            await phase.run({"task": "hi"})

        conversation.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_conversation_exception_returns_failed_result(self):
        bp = _make_acp_blueprint(phase_harness={"type": "acp"})
        phase = bp.phases[0]

        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", side_effect=RuntimeError("boom")),
        ):
            result = await phase.run({"task": "hi"})

        assert result["success"] is False
        assert "boom" in result["message"][0]

    @pytest.mark.asyncio
    async def test_conversation_closed_on_limit_exceeded(self):
        bp = _make_acp_blueprint(phase_harness={"type": "acp"})
        phase = bp.phases[0]
        phase.max_cost = 0.01

        conversation = _conversation_with_cost(0.02)
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", return_value=conversation),
        ):
            result = await phase.run({"task": "hi"})

        assert result["success"] is False
        assert "Circuit breaker" in result["message"][0]
        conversation.close.assert_called_once()


# ---------------------------------------------------------------------------
# Structured output
# ---------------------------------------------------------------------------


class TestACPStructuredOutput:
    @pytest.mark.asyncio
    async def test_valid_json_coerced_without_llm_repair(self):
        bp = V1Blueprint(
            {
                "name": "test_bp",
                "defaults": {"model": "openai/gpt-4o"},
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hi",
                        "harness": {"type": "acp"},
                        "output_model": "goblin.output_schema.PhaseResult",
                    }
                ],
            }
        )
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", return_value=conversation),
            patch(
                "openhands.sdk.conversation.get_agent_final_response",
                return_value='{"success": true, "message": ["from acp"]}',
            ),
            patch("goblin.phase.PromptPhase._request_structured_output") as mock_repair,
        ):
            result = await phase.run({})

        assert result["success"] is True
        assert result["message"] == ["from acp"]
        mock_repair.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_prose_repaired_by_llm_follow_up(self):
        bp = V1Blueprint(
            {
                "name": "test_bp",
                "defaults": {"model": "openai/gpt-4o"},
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hi",
                        "harness": {"type": "acp"},
                        "output_model": "goblin.output_schema.PhaseResult",
                    }
                ],
            }
        )
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        repaired = PhaseResult(success=True, message=["repaired"])
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", return_value=conversation),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="plain prose"),
            patch(
                "goblin.phase.PromptPhase._request_structured_output",
                return_value=repaired,
            ) as mock_repair,
        ):
            result = await phase.run({})

        assert result["success"] is True
        assert result["message"] == ["repaired"]
        mock_repair.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_total_failure_returns_synthetic_failure(self):
        bp = V1Blueprint(
            {
                "name": "test_bp",
                "defaults": {"model": "openai/gpt-4o"},
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hi",
                        "harness": {"type": "acp"},
                        "output_model": "goblin.output_schema.PhaseResult",
                    }
                ],
            }
        )
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", return_value=conversation),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="garbage"),
            patch("goblin.phase.PromptPhase._request_structured_output", return_value=None),
        ):
            result = await phase.run({})

        assert result["success"] is False
        assert any("Agent produced no structured output" in m for m in result["message"])

    @pytest.mark.asyncio
    async def test_output_schema_validation_failure(self):
        bp = V1Blueprint(
            {
                "name": "test_bp",
                "defaults": {"model": "openai/gpt-4o"},
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hi",
                        "harness": {"type": "acp"},
                        "output_model": "goblin.output_schema.PhaseResult",
                        "output_schema": {
                            "type": "object",
                            "properties": {"status": {"type": "string"}},
                            "required": ["status"],
                        },
                    }
                ],
            }
        )
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", return_value=conversation),
            patch(
                "openhands.sdk.conversation.get_agent_final_response",
                return_value='{"success": true, "message": ["ok"]}',
            ),
        ):
            result = await phase.run({})

        assert result["success"] is False
        assert any("Output schema validation failed" in m for m in result["message"])

    @pytest.mark.asyncio
    async def test_extra_model_fields_surface_in_result(self):
        from pydantic import BaseModel

        class SummaryResult(BaseModel):
            success: bool
            message: list[str]
            summary: str

        from goblin.output_schema import register_output_model

        register_output_model("tests.unit.test_harness_execution.SummaryResult", SummaryResult)

        bp = V1Blueprint(
            {
                "name": "test_bp",
                "defaults": {"model": "openai/gpt-4o"},
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "hi",
                        "harness": {"type": "acp"},
                        "output_model": "tests.unit.test_harness_execution.SummaryResult",
                    }
                ],
            }
        )
        phase = bp.phases[0]

        conversation = _fake_conversation()
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", return_value=conversation),
            patch(
                "openhands.sdk.conversation.get_agent_final_response",
                return_value='{"success": true, "message": ["ok"], "summary": "short"}',
            ),
        ):
            result = await phase.run({})

        assert result["success"] is True
        assert result["summary"] == "short"


# ---------------------------------------------------------------------------
# Circuit breaker against ACP usage
# ---------------------------------------------------------------------------


class TestACPCircuitBreaker:
    @pytest.mark.asyncio
    async def test_breaker_trips_on_max_cost(self):
        bp = _make_acp_blueprint(phase_harness={"type": "acp"})
        phase = bp.phases[0]
        phase.max_cost = 0.01

        conversation = _conversation_with_cost(0.02)
        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", return_value=conversation),
        ):
            result = await phase.run({"task": "hi"})

        assert result["success"] is False
        assert "max_cost" in result["message"][0]

    @pytest.mark.asyncio
    async def test_breaker_trips_on_max_tokens(self):
        bp = _make_acp_blueprint(phase_harness={"type": "acp"})
        phase = bp.phases[0]
        phase.max_tokens = 10

        conversation = _fake_conversation()
        metrics = MagicMock()
        metrics.accumulated_cost = 0.0
        token_usage = MagicMock()
        token_usage.prompt_tokens = 7
        token_usage.completion_tokens = 4
        metrics.accumulated_token_usage = token_usage
        conversation.conversation_stats = MagicMock()
        conversation.conversation_stats.get_combined_metrics.return_value = metrics

        settings = _fake_settings()
        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=settings),
            patch("goblin.conversation.run_conversation", return_value=conversation),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
        ):
            result = await phase.run({"task": "hi"})

        assert result["success"] is False
        assert "max_tokens" in result["message"][0]


# ---------------------------------------------------------------------------
# Concurrency / foreach
# ---------------------------------------------------------------------------


class TestACPConcurrency:
    @pytest.mark.asyncio
    async def test_foreach_acp_runs_fresh_agent_per_item(self):
        bp = V1Blueprint(
            {
                "name": "test_bp",
                "phases": [
                    {
                        "id": "p1",
                        "prompt": "process {index}",
                        "harness": {"type": "acp"},
                        "foreach": "2",
                    }
                ],
            }
        )

        def make_conv(*args, **kwargs):
            conv = _fake_conversation()
            return conv

        settings_call_count = 0

        def make_settings(*args, **kwargs):
            nonlocal settings_call_count
            settings_call_count += 1
            return _fake_settings()

        with (
            patch("openhands.sdk.ACPAgentSettings", side_effect=make_settings),
            patch("goblin.conversation.run_conversation", side_effect=make_conv),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
        ):
            result = await bp.run({})

        assert settings_call_count == 2
        assert result["p1"]["success"] is True

    @pytest.mark.asyncio
    async def test_concurrent_acp_phases_use_distinct_working_dirs(self):
        bp = V1Blueprint(
            {
                "name": "test_bp",
                "phases": [
                    {
                        "id": "api",
                        "prompt": "work in {api_dir}",
                        "harness": {"type": "acp", "working_dir": "{api_dir}"},
                    },
                    {
                        "id": "ui",
                        "prompt": "work in {ui_dir}",
                        "harness": {"type": "acp", "working_dir": "{ui_dir}"},
                    },
                ],
            }
        )

        seen_working_dirs: list[str] = []

        def capture_conv(*args, **kwargs):
            seen_working_dirs.append(args[1].working_dir)
            return _fake_conversation()

        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=_fake_settings()),
            patch("goblin.conversation.run_conversation", side_effect=capture_conv),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
        ):
            result = await bp.run({"api_dir": "/api", "ui_dir": "/ui"})

        assert result["api"]["success"] is True
        assert result["ui"]["success"] is True
        assert set(seen_working_dirs) == {"/api", "/ui"}

    @pytest.mark.asyncio
    async def test_foreach_item_as_working_dir(self, tmp_path):
        repos_file = tmp_path / "repos.txt"
        repos_file.write_text("/repo-a\n/repo-b\n")

        bp = V1Blueprint(
            {
                "name": "test_bp",
                "phases": [
                    {
                        "id": "rollout",
                        "prompt": "update {item}",
                        "harness": {"type": "acp", "working_dir": "{item}"},
                        "foreach": str(repos_file),
                    }
                ],
            }
        )

        seen_working_dirs: list[str] = []

        def capture_conv(*args, **kwargs):
            seen_working_dirs.append(args[1].working_dir)
            return _fake_conversation()

        with (
            patch("openhands.sdk.ACPAgentSettings", return_value=_fake_settings()),
            patch("goblin.conversation.run_conversation", side_effect=capture_conv),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="done"),
        ):
            result = await bp.run({})

        assert result["rollout"]["success"] is True
        assert seen_working_dirs == ["/repo-a", "/repo-b"]
