"""Tests for phase output_schema validation."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from goblin.blueprint import V1Blueprint
from goblin.phase import PromptPhase, _validate_output_schema


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_prompt_phase(raw: dict[str, Any]) -> PromptPhase:
    blueprint = V1Blueprint({"name": "test_bp", "phases": [raw]})
    return blueprint.phases[0]  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# _validate_output_schema
# ---------------------------------------------------------------------------


class TestValidateOutputSchema:
    def test_valid_data_returns_empty(self):
        schema = {"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]}
        errors = _validate_output_schema({"status": "ok", "success": True, "message": []}, schema)
        assert errors == []

    def test_missing_required_field(self):
        schema = {"type": "object", "required": ["status"]}
        errors = _validate_output_schema({"success": True, "message": []}, schema)
        assert any("'status'" in e for e in errors)

    def test_wrong_type(self):
        schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
        errors = _validate_output_schema({"success": True, "message": [], "count": "five"}, schema)
        assert any("integer" in e for e in errors)

    def test_nested_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "data": {
                    "type": "object",
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                }
            },
        }
        errors = _validate_output_schema({"success": True, "message": [], "data": {"id": 42}}, schema)
        assert errors == []

        errors = _validate_output_schema({"success": True, "message": [], "data": {"id": "not-int"}}, schema)
        assert any("integer" in e for e in errors)


# ---------------------------------------------------------------------------
# PromptPhase with output_schema
# ---------------------------------------------------------------------------


def _mock_llm_infra():
    """Return patchers for build_llm, Agent, LocalConversation, and get_agent_final_response."""
    mock_llm = MagicMock()
    mock_conversation = MagicMock()
    mock_conversation.state.events = []
    mock_conversation.arun = AsyncMock()
    mock_agent = MagicMock()

    patch_build_llm = patch("goblin.phase.build_llm", return_value=mock_llm)
    patch_agent = patch("goblin.phase.Agent", return_value=mock_agent)
    patch_local_conv = patch("goblin.phase.LocalConversation", return_value=mock_conversation)
    patch_get_final = patch("goblin.phase.get_agent_final_response", return_value="mocked response")
    patch_workspace = patch.object(V1Blueprint, "_get_workspace", return_value=MagicMock())

    return patch_build_llm, patch_agent, patch_local_conv, patch_get_final, patch_workspace


class TestPromptPhaseOutputSchema:
    @pytest.mark.asyncio
    async def test_valid_output_passes_validation(self):
        phase = _make_prompt_phase(
            {
                "id": "s1",
                "prompt": "hello",
                "output_schema": {
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                    "required": ["status"],
                },
            }
        )

        p1, p2, p3, p4, p5 = _mock_llm_infra()
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch.object(phase, "_parse_response", return_value={"success": True, "message": ["ok"], "status": "done"}),
        ):
            result = await phase.run({})

        assert result["success"] is True
        assert result["status"] == "done"

    @pytest.mark.asyncio
    async def test_invalid_output_fails_phase(self):
        phase = _make_prompt_phase(
            {
                "id": "s1",
                "prompt": "hello",
                "output_schema": {
                    "type": "object",
                    "properties": {"count": {"type": "integer"}},
                    "required": ["count"],
                },
            }
        )

        p1, p2, p3, p4, p5 = _mock_llm_infra()
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch.object(
                phase, "_parse_response", return_value={"success": True, "message": ["ok"]}
            ),
        ):
            result = await phase.run({})

        assert result["success"] is False
        assert any("count" in m for m in result["message"])

    @pytest.mark.asyncio
    async def test_no_output_schema_allows_any_shape(self):
        phase = _make_prompt_phase({"id": "s1", "prompt": "hello"})

        p1, p2, p3, p4, p5 = _mock_llm_infra()
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch.object(
                phase, "_parse_response", return_value={"success": True, "message": ["ok"], "extra": 123}
            ),
        ):
            result = await phase.run({})

        assert result["success"] is True
        assert result["extra"] == 123

    @pytest.mark.asyncio
    async def test_schema_failure_includes_prompt_and_output(self):
        phase = _make_prompt_phase(
            {
                "id": "s1",
                "prompt": "hello",
                "output_schema": {"type": "object", "required": ["missing"]},
            }
        )

        p1, p2, p3, p4, p5 = _mock_llm_infra()
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch.object(
                phase, "_parse_response", return_value={"success": True, "message": ["ok"]}
            ),
        ):
            result = await phase.run({})

        assert result["success"] is False
        assert "prompt" in result
        assert "output" in result
        assert "spend" in result

    @pytest.mark.asyncio
    async def test_extra_fields_preserved_with_schema(self):
        """When output_schema is set, extra fields from the agent response should still be preserved."""
        phase = _make_prompt_phase(
            {
                "id": "s1",
                "prompt": "hello",
                "output_schema": {
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                },
            }
        )

        p1, p2, p3, p4, p5 = _mock_llm_infra()
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch.object(
                phase,
                "_parse_response",
                return_value={"success": True, "message": ["ok"], "status": "done", "count": 42},
            ),
        ):
            result = await phase.run({})

        assert result["success"] is True
        assert result["status"] == "done"
        assert result["count"] == 42


# ---------------------------------------------------------------------------
# Blueprint-level integration
# ---------------------------------------------------------------------------


class TestBlueprintOutputSchemaIntegration:
    @pytest.mark.asyncio
    async def test_downstream_phase_can_access_validated_fields(self):
        """A phase with output_schema should expose its extra fields to downstream phases."""
        bp = V1Blueprint(
            {
                "name": "test_bp",
                "phases": [
                    {
                        "id": "api",
                        "prompt": "call api",
                        "output_schema": {
                            "type": "object",
                            "properties": {"status": {"type": "string"}},
                            "required": ["status"],
                        },
                    },
                    {
                        "id": "check",
                        "prompt": "check result",
                        "condition": "{{ steps.api.status == 'ok' }}",
                    },
                ],
            }
        )

        # Mock phases
        mock_phases = []
        for raw, real_phase in zip(
            [
                {
                    "id": "api",
                    "prompt": "call api",
                    "output_schema": {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                        "required": ["status"],
                    },
                },
                {"id": "check", "prompt": "check result", "condition": "{{ steps.api.status == 'ok' }}"},
            ],
            bp.phases,
        ):
            phase = AsyncMock()
            phase.id = raw["id"]
            phase.condition = raw.get("condition")
            phase.foreach = raw.get("foreach")
            phase.on_failure = raw.get("on_failure")
            phase.depends_on = real_phase.depends_on
            mock_phases.append(phase)
        bp.phases = mock_phases  # type: ignore[assignment]

        mock_phases[0].run.return_value = {
            "success": True,
            "message": ["ok"],
            "status": "ok",
            "prompt": "call api",
            "output": '{"success": true, "message": ["ok"], "status": "ok"}',
        }
        mock_phases[1].run.return_value = {"success": True, "message": ["done"]}

        results = await bp.run({})

        assert results["api"]["success"] is True
        assert results["api"]["status"] == "ok"
        mock_phases[1].run.assert_called_once()

    @pytest.mark.asyncio
    async def test_schema_failure_blocks_downstream_phase(self):
        """When output_schema validation fails, the phase fails and can abort the blueprint."""
        bp = V1Blueprint(
            {
                "name": "test_bp",
                "phases": [
                    {
                        "id": "api",
                        "prompt": "call api",
                        "output_schema": {
                            "type": "object",
                            "properties": {"status": {"type": "string"}},
                            "required": ["status"],
                        },
                    },
                    {"id": "next", "prompt": "after"},
                ],
            }
        )

        mock_phases = []
        for raw, real_phase in zip(
            [
                {
                    "id": "api",
                    "prompt": "call api",
                    "output_schema": {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                        "required": ["status"],
                    },
                },
                {"id": "next", "prompt": "after"},
            ],
            bp.phases,
        ):
            phase = AsyncMock()
            phase.id = raw["id"]
            phase.condition = raw.get("condition")
            phase.foreach = raw.get("foreach")
            phase.on_failure = raw.get("on_failure")
            phase.depends_on = real_phase.depends_on
            mock_phases.append(phase)
        bp.phases = mock_phases  # type: ignore[assignment]

        mock_phases[0].run.return_value = {
            "success": False,
            "message": ["Output schema validation failed: 'status' is a required property"],
            "prompt": "call api",
            "output": '{"success": true, "message": ["ok"]}',
        }
        mock_phases[1].run.return_value = {"success": True, "message": ["done"]}

        results = await bp.run({})

        assert results["api"]["success"] is False
        # Default on_failure is abort, so next should not run
        mock_phases[1].run.assert_not_called()
