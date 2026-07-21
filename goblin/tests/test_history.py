"""Tests for phase conversation history — validation, context building, and integration."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from goblin.blueprint import V1Blueprint
from goblin.phase import PromptPhase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_blueprint(phases: list[dict], vars: dict | None = None) -> V1Blueprint:
    spec: dict[str, Any] = {"name": "test_bp", "phases": phases}
    if vars:
        spec["vars"] = vars
    return V1Blueprint(spec)


def _make_blueprint_with_mock_phases(phases_raw: list[dict], vars: dict | None = None) -> V1Blueprint:
    bp = _make_blueprint(phases_raw, vars)
    mock_phases = []
    for real_phase in bp.phases:
        phase = AsyncMock()
        phase.id = real_phase.id
        phase.condition = real_phase.condition
        phase.foreach = real_phase.foreach
        phase.on_failure = real_phase.on_failure
        phase.history = getattr(real_phase, "history", None)
        phase.depends_on = real_phase.depends_on
        mock_phases.append(phase)
    bp.phases = mock_phases
    return bp


# ---------------------------------------------------------------------------
# _validate_history
# ---------------------------------------------------------------------------


class TestValidateHistoryValid:
    def test_valid_history_reference(self):
        """Phase B references phase A (defined before it). No error."""
        bp = _make_blueprint(
            [
                {"id": "phase_a", "prompt": "do A"},
                {"id": "phase_b", "prompt": "do B", "history": ["phase_a"]},
            ]
        )
        assert isinstance(bp.phases[1], PromptPhase)
        assert bp.phases[1].history == ["phase_a"]

    def test_foreach_phase_with_history_is_allowed(self):
        """A phase with both foreach and history parses without error."""
        bp = _make_blueprint(
            [
                {"id": "phase_a", "prompt": "analyze"},
                {"id": "phase_b", "prompt": "do {item}", "foreach": "3", "history": ["phase_a"]},
            ]
        )
        assert isinstance(bp.phases[1], PromptPhase)
        assert bp.phases[1].history == ["phase_a"]
        assert bp.phases[1].foreach == "3"


class TestValidateHistoryInvalid:
    def test_reference_to_undefined_phase(self):
        with pytest.raises(ValueError, match="not defined before this phase"):
            _make_blueprint(
                [
                    {"id": "phase_a", "prompt": "do A", "history": ["nonexistent"]},
                ]
            )

    def test_reference_to_later_phase(self):
        with pytest.raises(ValueError, match="not in its dependency closure"):
            _make_blueprint(
                [
                    {"id": "phase_a", "prompt": "do A", "history": ["phase_b"]},
                    {"id": "phase_b", "prompt": "do B"},
                ]
            )

    def test_reference_to_foreach_phase(self):
        with pytest.raises(ValueError, match="cannot reference foreach"):
            _make_blueprint(
                [
                    {"id": "phase_a", "prompt": "do A", "foreach": "3"},
                    {"id": "phase_b", "prompt": "do B", "history": ["phase_a"]},
                ]
            )

    def test_reference_to_load_project_phase(self):
        with pytest.raises(ValueError, match="can only reference prompt"):
            _make_blueprint(
                [
                    {"id": "load", "load_project": "/some/path"},
                    {"id": "phase_b", "prompt": "do B", "history": ["load"]},
                ]
            )

    def test_history_on_non_prompt_phase(self):
        """history on a load_project phase should raise."""
        bp = _make_blueprint(
            [
                {"id": "load", "load_project": "/path"},
                {"id": "phase_b", "prompt": "do B"},
            ]
        )
        # Manually set history on the load_project phase to test the validator
        bp.phases[0].history = ["phase_b"]  # type: ignore[attr-defined]
        with pytest.raises(ValueError, match="only valid on prompt phases"):
            bp._dependency_graph._validate_history()

    def test_history_across_parallel_phases_rejected(self):
        """A phase cannot reference history from a phase that could run in parallel."""
        with pytest.raises(ValueError, match="not in its dependency closure"):
            _make_blueprint(
                [
                    {"id": "parent", "prompt": "go"},
                    {"id": "child_a", "prompt": "do A", "depends_on": ["parent"]},
                    {"id": "child_b", "prompt": "do B", "depends_on": ["parent"], "history": ["child_a"]},
                ]
            )


# ---------------------------------------------------------------------------
# _build_history_context
# ---------------------------------------------------------------------------


class TestBuildHistoryContext:
    def test_single_phase_reference(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        results = {
            "phase_a": {"prompt": "Do analysis", "output": "Found 3 issues"},
        }
        ctx = bp._build_history_context(["phase_a"], results)
        assert "## Prior Phase Results" in ctx
        assert "### Phase: phase_a" in ctx
        assert "**Prompt:** Do analysis" in ctx
        assert "Found 3 issues" in ctx
        assert ctx.endswith("---\n\n")

    def test_multiple_phase_references_in_order(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        results = {
            "phase_a": {"prompt": "First", "output": "Output A"},
            "phase_b": {"prompt": "Second", "output": "Output B"},
        }
        ctx = bp._build_history_context(["phase_a", "phase_b"], results)
        pos_a = ctx.index("### Phase: phase_a")
        pos_b = ctx.index("### Phase: phase_b")
        assert pos_a < pos_b

    def test_skipped_phase_omitted(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        results = {
            "phase_a": {"prompt": "First", "output": "Output A"},
        }
        ctx = bp._build_history_context(["phase_a", "phase_missing"], results)
        assert "phase_missing" not in ctx
        assert "### Phase: phase_a" in ctx

    def test_phase_with_empty_output(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        results = {
            "phase_a": {"prompt": "Do something", "output": ""},
        }
        ctx = bp._build_history_context(["phase_a"], results)
        assert "**Prompt:** Do something" in ctx
        assert "**Output:**" not in ctx

    def test_no_valid_results_returns_empty(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        ctx = bp._build_history_context(["phase_missing"], {})
        assert ctx == ""

    def test_phase_with_no_prompt_and_no_output_omitted(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        results = {
            "phase_a": {"prompt": "", "output": ""},
        }
        ctx = bp._build_history_context(["phase_a"], results)
        assert ctx == ""


# ---------------------------------------------------------------------------
# run() integration — history context injection
# ---------------------------------------------------------------------------


class TestRunHistoryContextInjection:
    @pytest.mark.asyncio
    async def test_history_context_passed_to_phase(self):
        """Phase B with history: [phase_a] receives _history_context in context."""
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "phase_a", "prompt": "analyze"},
                {"id": "phase_b", "prompt": "fix", "history": ["phase_a"]},
            ]
        )
        bp.phases[0].run.return_value = {
            "success": True,
            "message": ["ok"],
            "prompt": "analyze",
            "output": "Found bugs",
        }
        bp.phases[1].run.return_value = {"success": True, "message": ["fixed"]}

        await bp.run({})

        ctx_b = bp.phases[1].run.call_args.args[0]
        assert "_history_context" in ctx_b
        assert "Found bugs" in ctx_b["_history_context"]
        assert "analyze" in ctx_b["_history_context"]

    @pytest.mark.asyncio
    async def test_phase_without_history_has_no_history_context(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "phase_a", "prompt": "analyze"},
                {"id": "phase_b", "prompt": "fix"},
            ]
        )
        bp.phases[0].run.return_value = {"success": True, "message": ["ok"]}
        bp.phases[1].run.return_value = {"success": True, "message": ["done"]}

        await bp.run({})

        ctx_b = bp.phases[1].run.call_args.args[0]
        assert "_history_context" not in ctx_b

    @pytest.mark.asyncio
    async def test_output_and_prompt_captured_in_results(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "phase_a", "prompt": "analyze"},
            ]
        )
        bp.phases[0].run.return_value = {
            "success": True,
            "message": ["ok"],
            "prompt": "analyze stuff",
            "output": "full text output",
        }

        results = await bp.run({})

        assert results["phase_a"]["prompt"] == "analyze stuff"
        assert results["phase_a"]["output"] == "full text output"

    @pytest.mark.asyncio
    async def test_foreach_with_history_each_iteration_gets_same_context(self):
        """Each foreach iteration receives the same history context."""
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "phase_a", "prompt": "analyze"},
                {"id": "phase_b", "prompt": "do {index}", "foreach": "3", "history": ["phase_a"]},
            ]
        )
        bp.phases[0].run.return_value = {
            "success": True,
            "message": ["ok"],
            "prompt": "analyze",
            "output": "analysis result",
        }
        bp.phases[1].run.return_value = {"success": True, "message": ["done"]}

        await bp.run({})

        assert bp.phases[1].run.call_count == 3
        contexts = [call.args[0] for call in bp.phases[1].run.call_args_list]
        for ctx in contexts:
            assert "_history_context" in ctx
            assert "analysis result" in ctx["_history_context"]

    @pytest.mark.asyncio
    async def test_history_context_not_in_results(self):
        """_history_context should not leak into results dict."""
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "phase_a", "prompt": "analyze"},
                {"id": "phase_b", "prompt": "fix", "history": ["phase_a"]},
            ]
        )
        bp.phases[0].run.return_value = {
            "success": True,
            "message": ["ok"],
            "prompt": "analyze",
            "output": "output",
        }
        bp.phases[1].run.return_value = {"success": True, "message": ["fixed"]}

        results = await bp.run({})

        assert "_history_context" not in results.get("phase_a", {})
        assert "_history_context" not in results.get("phase_b", {})
