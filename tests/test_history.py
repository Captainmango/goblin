"""Tests for step conversation history — validation, context building, and integration."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from goblin.blueprint import V1Blueprint
from goblin.step import PromptStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_blueprint(steps: list[dict], vars: dict | None = None) -> V1Blueprint:
    spec: dict[str, Any] = {"name": "test_wf", "steps": steps}
    if vars:
        spec["vars"] = vars
    return V1Blueprint(spec)


def _make_blueprint_with_mock_steps(steps_raw: list[dict], vars: dict | None = None) -> V1Blueprint:
    bp = _make_blueprint(steps_raw, vars)
    mock_steps = []
    for real_step in bp.steps:
        step = AsyncMock()
        step.id = real_step.id
        step.condition = real_step.condition
        step.foreach = real_step.foreach
        step.on_failure = real_step.on_failure
        step.history = getattr(real_step, "history", None)
        mock_steps.append(step)
    bp.steps = mock_steps
    return bp


# ---------------------------------------------------------------------------
# _validate_history
# ---------------------------------------------------------------------------


class TestValidateHistoryValid:
    def test_valid_history_reference(self):
        """Step B references step A (defined before it). No error."""
        bp = _make_blueprint(
            [
                {"id": "step_a", "prompt": "do A"},
                {"id": "step_b", "prompt": "do B", "history": ["step_a"]},
            ]
        )
        assert isinstance(bp.steps[1], PromptStep)
        assert bp.steps[1].history == ["step_a"]

    def test_foreach_step_with_history_is_allowed(self):
        """A step with both foreach and history parses without error."""
        bp = _make_blueprint(
            [
                {"id": "step_a", "prompt": "analyze"},
                {"id": "step_b", "prompt": "do {item}", "foreach": "3", "history": ["step_a"]},
            ]
        )
        assert isinstance(bp.steps[1], PromptStep)
        assert bp.steps[1].history == ["step_a"]
        assert bp.steps[1].foreach == "3"


class TestValidateHistoryInvalid:
    def test_reference_to_undefined_step(self):
        with pytest.raises(ValueError, match="not defined before this step"):
            _make_blueprint(
                [
                    {"id": "step_a", "prompt": "do A", "history": ["nonexistent"]},
                ]
            )

    def test_reference_to_later_step(self):
        with pytest.raises(ValueError, match="not defined before this step"):
            _make_blueprint(
                [
                    {"id": "step_a", "prompt": "do A", "history": ["step_b"]},
                    {"id": "step_b", "prompt": "do B"},
                ]
            )

    def test_reference_to_foreach_step(self):
        with pytest.raises(ValueError, match="cannot reference foreach"):
            _make_blueprint(
                [
                    {"id": "step_a", "prompt": "do A", "foreach": "3"},
                    {"id": "step_b", "prompt": "do B", "history": ["step_a"]},
                ]
            )

    def test_reference_to_load_project_step(self):
        with pytest.raises(ValueError, match="can only reference prompt steps"):
            _make_blueprint(
                [
                    {"id": "load", "load_project": "/some/path"},
                    {"id": "step_b", "prompt": "do B", "history": ["load"]},
                ]
            )

    def test_history_on_non_prompt_step(self):
        """history on a load_project step should raise."""
        # load_project steps don't parse history, but we need to verify
        # the validator catches it if someone manually sets it. Since
        # LoadProjectStep doesn't have a history field, we test indirectly
        # by checking that load_project steps don't have history attr set.
        # The validator uses getattr with a default of None, so a step
        # without the attribute is fine. A raw dict with history on a
        # load_project step won't even parse it — but the schema should
        # reject this. Let's test the validator path by creating a blueprint
        # and manually setting history on a non-prompt step.
        bp = _make_blueprint(
            [
                {"id": "load", "load_project": "/path"},
                {"id": "step_b", "prompt": "do B"},
            ]
        )
        # Manually set history on the load_project step to test the validator
        bp.steps[0].history = ["step_b"]  # type: ignore[attr-defined]
        with pytest.raises(ValueError, match="only valid on prompt steps"):
            bp._validate_history()


# ---------------------------------------------------------------------------
# _build_history_context
# ---------------------------------------------------------------------------


class TestBuildHistoryContext:
    def test_single_step_reference(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        results = {
            "step_a": {"prompt": "Do analysis", "output": "Found 3 issues"},
        }
        ctx = bp._build_history_context(["step_a"], results)
        assert "## Prior Step Results" in ctx
        assert "### Step: step_a" in ctx
        assert "**Prompt:** Do analysis" in ctx
        assert "Found 3 issues" in ctx
        assert ctx.endswith("---\n\n")

    def test_multiple_step_references_in_order(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        results = {
            "step_a": {"prompt": "First", "output": "Output A"},
            "step_b": {"prompt": "Second", "output": "Output B"},
        }
        ctx = bp._build_history_context(["step_a", "step_b"], results)
        pos_a = ctx.index("### Step: step_a")
        pos_b = ctx.index("### Step: step_b")
        assert pos_a < pos_b

    def test_skipped_step_omitted(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        results = {
            "step_a": {"prompt": "First", "output": "Output A"},
        }
        ctx = bp._build_history_context(["step_a", "step_missing"], results)
        assert "step_missing" not in ctx
        assert "### Step: step_a" in ctx

    def test_step_with_empty_output(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        results = {
            "step_a": {"prompt": "Do something", "output": ""},
        }
        ctx = bp._build_history_context(["step_a"], results)
        assert "**Prompt:** Do something" in ctx
        assert "**Output:**" not in ctx

    def test_no_valid_results_returns_empty(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        ctx = bp._build_history_context(["step_missing"], {})
        assert ctx == ""

    def test_step_with_no_prompt_and_no_output_omitted(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        results = {
            "step_a": {"prompt": "", "output": ""},
        }
        ctx = bp._build_history_context(["step_a"], results)
        assert ctx == ""


# ---------------------------------------------------------------------------
# run() integration — history context injection
# ---------------------------------------------------------------------------


class TestRunHistoryContextInjection:
    @pytest.mark.asyncio
    async def test_history_context_passed_to_step(self):
        """Step B with history: [step_a] receives _history_context in context."""
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "step_a", "prompt": "analyze"},
                {"id": "step_b", "prompt": "fix", "history": ["step_a"]},
            ]
        )
        bp.steps[0].run.return_value = {
            "success": True,
            "message": ["ok"],
            "prompt": "analyze",
            "output": "Found bugs",
        }
        bp.steps[1].run.return_value = {"success": True, "message": ["fixed"]}

        await bp.run({})

        ctx_b = bp.steps[1].run.call_args.args[0]
        assert "_history_context" in ctx_b
        assert "Found bugs" in ctx_b["_history_context"]
        assert "analyze" in ctx_b["_history_context"]

    @pytest.mark.asyncio
    async def test_step_without_history_has_no_history_context(self):
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "step_a", "prompt": "analyze"},
                {"id": "step_b", "prompt": "fix"},
            ]
        )
        bp.steps[0].run.return_value = {"success": True, "message": ["ok"]}
        bp.steps[1].run.return_value = {"success": True, "message": ["done"]}

        await bp.run({})

        ctx_b = bp.steps[1].run.call_args.args[0]
        assert "_history_context" not in ctx_b

    @pytest.mark.asyncio
    async def test_output_and_prompt_captured_in_results(self):
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "step_a", "prompt": "analyze"},
            ]
        )
        bp.steps[0].run.return_value = {
            "success": True,
            "message": ["ok"],
            "prompt": "analyze stuff",
            "output": "full text output",
        }

        results = await bp.run({})

        assert results["step_a"]["prompt"] == "analyze stuff"
        assert results["step_a"]["output"] == "full text output"

    @pytest.mark.asyncio
    async def test_foreach_with_history_each_iteration_gets_same_context(self):
        """Each foreach iteration receives the same history context."""
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "step_a", "prompt": "analyze"},
                {"id": "step_b", "prompt": "do {index}", "foreach": "3", "history": ["step_a"]},
            ]
        )
        bp.steps[0].run.return_value = {
            "success": True,
            "message": ["ok"],
            "prompt": "analyze",
            "output": "analysis result",
        }
        bp.steps[1].run.return_value = {"success": True, "message": ["done"]}

        await bp.run({})

        assert bp.steps[1].run.call_count == 3
        contexts = [call.args[0] for call in bp.steps[1].run.call_args_list]
        for ctx in contexts:
            assert "_history_context" in ctx
            assert "analysis result" in ctx["_history_context"]

    @pytest.mark.asyncio
    async def test_history_context_not_in_results(self):
        """_history_context should not leak into results dict."""
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "step_a", "prompt": "analyze"},
                {"id": "step_b", "prompt": "fix", "history": ["step_a"]},
            ]
        )
        bp.steps[0].run.return_value = {
            "success": True,
            "message": ["ok"],
            "prompt": "analyze",
            "output": "output",
        }
        bp.steps[1].run.return_value = {"success": True, "message": ["fixed"]}

        results = await bp.run({})

        assert "_history_context" not in results.get("step_a", {})
        assert "_history_context" not in results.get("step_b", {})
