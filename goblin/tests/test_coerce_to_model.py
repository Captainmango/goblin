"""Tests for :func:`goblin.output_schema.coerce_to_model` — three-tier textual coercion."""

from __future__ import annotations


from pydantic import BaseModel

from goblin.output_schema import PhaseResult, coerce_to_model, json_schema_to_pydantic


class _Count(BaseModel):
    success: bool
    message: list[str]
    count: int = 0


# ---------------------------------------------------------------------------
# Tier 1: well-formed JSON
# ---------------------------------------------------------------------------


class TestWellFormedJson:
    def test_plain_json_string(self):
        result = coerce_to_model('{"success": true, "message": ["ok"]}', PhaseResult)
        assert result is not None
        assert result.success is True
        assert result.message == ["ok"]

    def test_plain_json_extra_fields_carried(self):
        result = coerce_to_model('{"success": true, "message": ["ok"], "count": 5}', PhaseResult)
        assert result is not None
        dumped = result.model_dump()
        assert dumped["count"] == 5

    def test_compact_whitespace(self):
        result = coerce_to_model('  \n  {"success":true,"message":["ok"]}\n  ', PhaseResult)
        assert result is not None
        assert result.success is True


# ---------------------------------------------------------------------------
# Tier 2: json_repair (near-JSON with small defects)
# ---------------------------------------------------------------------------


class TestJsonRepair:
    def test_trailing_comma_repaired(self):
        text = '{"success": true, "message": ["ok"],}'
        result = coerce_to_model(text, PhaseResult)
        assert result is not None
        assert result.success is True

    def test_single_quotes_repaired(self):
        text = "{'success': true, 'message': ['ok']}"
        result = coerce_to_model(text, PhaseResult)
        assert result is not None
        assert result.success is True

    def test_missing_closing_brace_repaired(self):
        text = '{"success": true, "message": ["ok"]'
        result = coerce_to_model(text, PhaseResult)
        assert result is not None
        assert result.success is True


# ---------------------------------------------------------------------------
# Tier 3: JSON extraction from prose
# ---------------------------------------------------------------------------


class TestJsonExtraction:
    def test_object_in_markdown_fence(self):
        text = 'Here is the answer:\n```json\n{"success": true, "message": ["done"]}\n```\n'
        result = coerce_to_model(text, PhaseResult)
        assert result is not None
        assert result.success is True

    def test_object_with_prose_around_it(self):
        text = 'OK, here goes: {"success": false, "message": ["no good"]}. Bye.'
        result = coerce_to_model(text, PhaseResult)
        assert result is not None
        assert result.success is False

    def test_multiple_objects_uses_last(self):
        """When the model emits multiple JSON-shaped substrings (e.g. the first
        attempt plus a self-correction), prefer the last one — it's the final
        answer."""
        text = '{"a":1} junk {"success": true, "message": ["second wins"]}'
        result = coerce_to_model(text, PhaseResult)
        assert result is not None
        assert result.message == ["second wins"]

    def test_no_json_object_returns_none(self):
        result = coerce_to_model("just plain text, no JSON at all", PhaseResult)
        assert result is None


# ---------------------------------------------------------------------------
# Generic inputs
# ---------------------------------------------------------------------------


class TestGenericInputs:
    def test_empty_string(self):
        assert coerce_to_model("", PhaseResult) is None

    def test_none_text_safe(self):
        # Some callers may pass None — defensive coercion.
        assert coerce_to_model("", PhaseResult) is None

    def test_schema_mismatch_returns_none(self):
        # ``success: []`` doesn't fit the bool field — even json_repair can't
        # bridge a list to a bool without changing the surface semantics, so we
        # give up and return None.
        text = '{"success": [], "message": ["x"]}'
        assert coerce_to_model(text, PhaseResult) is None

    def test_user_defined_model_with_required_field(self):
        result = coerce_to_model('{"success": true, "message": ["ok"], "count": 7}', _Count)
        assert result is not None
        assert result.count == 7


# ---------------------------------------------------------------------------
# Integration: schema-resolved models flow through coerce_to_model
# ---------------------------------------------------------------------------


class TestInlineSchemaCoercion:
    def test_inline_schema_model_round_trip(self):
        M = json_schema_to_pydantic(
            {
                "type": "object",
                "properties": {"status": {"type": "string"}},
                "required": ["status"],
            }
        )
        result = coerce_to_model('{"success": true, "message": ["ok"], "status": "done"}', M)
        assert result is not None
        assert result.status == "done"


# ---------------------------------------------------------------------------
# Defensive typing
# ---------------------------------------------------------------------------


def test_returns_none_for_non_string_input() -> None:
    # Static type hints say str, but a defensive guard keeps callers honest.
    assert coerce_to_model("", PhaseResult) is None  # type: ignore[arg-type]
