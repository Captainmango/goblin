"""Tests for output schema resolution, validation, and structured finish tool wiring."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from goblin.blueprint import V1Blueprint
from goblin.output_schema import (
    PhaseResult,
    json_schema_to_pydantic,
    register_output_model,
    resolve_output_model,
    validate_against_schema,
)
from tests.unit._helpers import _make_prompt_phase, _mock_llm_infra

# ---------------------------------------------------------------------------
# _validate_output_schema / validate_against_schema (legacy alias)
# ---------------------------------------------------------------------------


class TestValidateOutputSchema:
    def test_valid_data_returns_empty(self):
        schema = {"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]}
        errors = validate_against_schema({"status": "ok", "success": True, "message": []}, schema)
        assert errors == []

    def test_missing_required_field(self):
        schema = {"type": "object", "required": ["status"]}
        errors = validate_against_schema({"success": True, "message": []}, schema)
        assert any("'status'" in e for e in errors)

    def test_wrong_type(self):
        schema = {"type": "object", "properties": {"count": {"type": "integer"}}}
        errors = validate_against_schema({"success": True, "message": [], "count": "five"}, schema)
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
        errors = validate_against_schema({"success": True, "message": [], "data": {"id": 42}}, schema)
        assert errors == []

        errors = validate_against_schema({"success": True, "message": [], "data": {"id": "not-int"}}, schema)
        assert any("integer" in e for e in errors)


# ---------------------------------------------------------------------------
# resolve_output_model
# ---------------------------------------------------------------------------


class TestResolveOutputModel:
    def test_returns_phase_result_when_no_schema_or_model(self):
        bp = V1Blueprint({"name": "t", "phases": [{"id": "s1", "prompt": "p"}]})
        assert resolve_output_model(bp, bp.phases[0]) is PhaseResult

    def test_inline_json_schema_produces_pydantic_model(self):
        bp = V1Blueprint(
            {
                "name": "t",
                "phases": [
                    {
                        "id": "s1",
                        "prompt": "p",
                        "output_schema": {
                            "type": "object",
                            "properties": {"status": {"type": "string"}},
                            "required": ["status"],
                        },
                    }
                ],
            }
        )
        model = resolve_output_model(bp, bp.phases[0])
        instance = model(success=True, message=["ok"], status="done")
        assert instance.status == "done"
        assert instance.success is True

    def test_registered_dotted_name_resolves(self):
        from pydantic import BaseModel, Field

        class SummaryResult(BaseModel):
            success: bool
            message: list[str]
            summary: str = Field(...)

        register_output_model("tests.test_output_model_dummy.SummaryResult", SummaryResult)
        bp = V1Blueprint(
            {
                "name": "t",
                "phases": [{"id": "s1", "prompt": "p", "output_model": "tests.test_output_model_dummy.SummaryResult"}],
            }
        )
        model = resolve_output_model(bp, bp.phases[0])
        assert model is SummaryResult

    def test_unknown_dotted_name_raises(self):
        bp = V1Blueprint(
            {
                "name": "t",
                "phases": [{"id": "s1", "prompt": "p", "output_model": "no_such_module_xyz.NoSuchModel"}],
            }
        )
        with pytest.raises((ValueError, ImportError)):
            resolve_output_model(bp, bp.phases[0])

    def test_same_bp_phase_different_schemas_resolve_differently(self):
        """The schema cache key must include the schema contents, not just ids."""
        base = {"name": "test_bp", "phases": [{"id": "s1", "prompt": "p"}]}

        raw_a = {
            **base,
            "phases": [
                {
                    "id": "s1",
                    "prompt": "p",
                    "output_schema": {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                        "required": ["status"],
                    },
                }
            ],
        }
        raw_b = {
            **base,
            "phases": [
                {
                    "id": "s1",
                    "prompt": "p",
                    "output_schema": {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                        "required": ["count"],
                    },
                }
            ],
        }
        model_a = resolve_output_model(V1Blueprint(raw_a), V1Blueprint(raw_a).phases[0])
        model_b = resolve_output_model(V1Blueprint(raw_b), V1Blueprint(raw_b).phases[0])
        assert model_a is not model_b
        assert "status" in model_a.model_fields
        assert "count" in model_b.model_fields

    def test_register_output_model_rejects_non_basemodel(self):
        with pytest.raises(ValueError, match="BaseModel"):
            register_output_model("bad", dict)


# ---------------------------------------------------------------------------
# json_schema_to_pydantic
# ---------------------------------------------------------------------------


class TestJsonSchemaToPydantic:
    def test_empty_object_schema_yields_permissive_model(self):
        M = json_schema_to_pydantic({"type": "object"})
        instance = M(success=True, message=["ok"], anything="goes")
        assert instance.anything == "goes"

    def test_required_and_optional_fields(self):
        M = json_schema_to_pydantic(
            {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "count": {"type": "integer"},
                },
                "required": ["status"],
            }
        )
        instance = M(success=True, message=["ok"], status="done", count=5)
        assert instance.status == "done"
        assert instance.count == 5

        # Missing required raises
        with pytest.raises(ValidationError):
            M(success=True, message=["ok"], count=5)


# ---------------------------------------------------------------------------
# PromptPhase with output_schema (end-to-end via mocks)
# ---------------------------------------------------------------------------


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
            },
            real_blueprint=True,
        )

        p1, p2, p3, p4, p5 = _mock_llm_infra()
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch(
                "goblin.tools.structured_finish.extract_structured_result",
                return_value=phase._resolve_output_model()(success=True, message=["ok"], status="done"),
            ),
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
            },
            real_blueprint=True,
        )

        # The structured finish tool returns a model missing the required field.
        # Since ``extract_structured_result`` is typed to ``output_model``, it
        # would otherwise raise. Patch it to return a malformed dict instead.
        p1, p2, p3, p4, p5 = _mock_llm_infra()
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch(
                "goblin.tools.structured_finish.extract_structured_result",
                return_value=None,
            ),
            patch(
                "goblin.phase.coerce_to_model",
                return_value=phase._resolve_output_model().model_construct(success=True, message=["ok"]),
            ),
        ):
            result = await phase.run({})

        assert result["success"] is False
        assert any("count" in m for m in result["message"])

    @pytest.mark.asyncio
    async def test_no_output_schema_wraps_chat_text(self):
        """Default flow (no output_model/output_schema): chat text wraps into PhaseResult."""
        phase = _make_prompt_phase({"id": "s1", "prompt": "hello"}, real_blueprint=True)

        p1, p2, p3, p4, p5 = _mock_llm_infra()
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch(
                "openhands.sdk.conversation.get_agent_final_response",
                return_value="Some agent reply with arbitrary shape.",
            ),
        ):
            result = await phase.run({})

        assert result["success"] is True
        assert result["message"] == ["Some agent reply with arbitrary shape."]

    @pytest.mark.asyncio
    async def test_no_output_schema_empty_text_reports_failure(self):
        """With no agent text, the default flow reports failure."""
        phase = _make_prompt_phase({"id": "s1", "prompt": "hello"}, real_blueprint=True)

        p1, p2, p3, p4, p5 = _mock_llm_infra()
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch("openhands.sdk.conversation.get_agent_final_response", return_value=""),
        ):
            result = await phase.run({})

        assert result["success"] is False
        assert result["message"] == ["Agent produced no text output"]

    @pytest.mark.asyncio
    async def test_no_output_schema_does_not_register_structured_finish(self):
        """Default flow doesn't wire the typed Finish tool into the agent."""
        phase = _make_prompt_phase({"id": "s1", "prompt": "hello"}, real_blueprint=True)

        captured_tools: list = []

        class FakeAgent:
            def __init__(self, *, tools, **kwargs: Any) -> None:
                captured_tools.extend(tools)

        p1, p2, p3, p4, p5 = _mock_llm_infra()
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch("openhands.sdk.Agent", FakeAgent),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="ok"),
        ):
            await phase.run({})

        names = {t.name for t in captured_tools}
        assert "structured_finish" not in names

    @pytest.mark.asyncio
    async def test_output_schema_registers_structured_finish_with_model_ref(self):
        """Structured-output configuration wires the typed Finish tool."""
        phase = _make_prompt_phase(
            {
                "id": "s1",
                "prompt": "hello",
                "output_schema": {
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                    "required": ["status"],
                },
            },
            real_blueprint=True,
        )

        captured_tools: list = []

        class FakeAgent:
            def __init__(self, *, tools, **kwargs: Any) -> None:
                captured_tools.extend(tools)

        p1, p2, p3, p4, p5 = _mock_llm_infra()
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch("openhands.sdk.Agent", FakeAgent),
            patch("openhands.sdk.conversation.get_agent_final_response", return_value="ok"),
        ):
            await phase.run({})

        finish_tool = next((t for t in captured_tools if t.name == "structured_finish"), None)
        assert finish_tool is not None
        assert "output_model_ref" in finish_tool.params
        resolved = phase._resolve_output_model()
        assert finish_tool.params["output_model_ref"].endswith(resolved.__name__)

    @pytest.mark.asyncio
    async def test_schema_failure_includes_prompt_and_output(self):
        phase = _make_prompt_phase(
            {
                "id": "s1",
                "prompt": "hello",
                "output_schema": {"type": "object", "required": ["missing"]},
            },
            real_blueprint=True,
        )

        p1, p2, p3, p4, p5 = _mock_llm_infra()
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch(
                "goblin.tools.structured_finish.extract_structured_result",
                return_value=PhaseResult(success=True, message=["ok"]),
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
            },
            real_blueprint=True,
        )

        p1, p2, p3, p4, p5 = _mock_llm_infra()
        with (
            p1,
            p2,
            p3,
            p4,
            p5,
            patch(
                "goblin.tools.structured_finish.extract_structured_result",
                return_value=phase._resolve_output_model()(success=True, message=["ok"], status="done", count=42),
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
