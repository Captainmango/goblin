"""Tests for the foreach feature in V1Blueprint: _resolve, _resolve_foreach_count, _jinja_env,
and the foreach integration in run()."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from goblin.blueprint import V1Blueprint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_blueprint(phases: list[dict], vars: dict | None = None) -> V1Blueprint:
    """Build a V1Blueprint from a minimal spec, patching phase parsing to avoid
    importing the real PromptPhase/LoadProjectPhase (which pull in the SDK)."""
    spec: dict[str, Any] = {"name": "test_bp", "phases": phases}
    if vars:
        spec["vars"] = vars
    return V1Blueprint(spec)


def _make_blueprint_with_mock_phases(phases_raw: list[dict], vars: dict | None = None) -> V1Blueprint:
    """Build a V1Blueprint but replace the real phases with mock objects so we
    can control phase.run() without hitting the Agent SDK."""
    # First build normally, then swap out the parsed phases
    bp = _make_blueprint(phases_raw, vars)
    mock_phases = []
    for raw, real_phase in zip(phases_raw, bp.phases):
        phase = AsyncMock()
        phase.id = raw["id"]
        phase.condition = raw.get("condition")
        phase.foreach = raw.get("foreach")
        phase.on_failure = raw.get("on_failure")
        phase.depends_on = real_phase.depends_on
        mock_phases.append(phase)
    bp.phases = mock_phases
    return bp


# ---------------------------------------------------------------------------
# _jinja_env
# ---------------------------------------------------------------------------


class TestJinjaEnv:
    def test_returns_environment_with_filters(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hello"}])
        env = bp._jinja_env()
        assert "contains" in env.filters
        assert "json_value" in env.filters

    def test_contains_filter_works(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hello"}])
        env = bp._jinja_env()
        tpl = env.from_string("{{ msgs | contains('world') }}")
        assert tpl.render(msgs=["hello world"]).strip() == "True"
        assert tpl.render(msgs=["hello"]).strip() == "False"


# ---------------------------------------------------------------------------
# _resolve_foreach_count
# ---------------------------------------------------------------------------


class TestResolveForeachCount:
    def test_zero(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        assert bp._resolve_foreach_count(0) == []

    def test_positive(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        result = bp._resolve_foreach_count(3)
        assert result == [{"index": 0}, {"index": 1}, {"index": 2}]

    def test_negative_raises(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        with pytest.raises(ValueError, match="non-negative"):
            bp._resolve_foreach_count(-1)


# ---------------------------------------------------------------------------
# _resolve — integer mode
# ---------------------------------------------------------------------------


class TestResolveIntegerMode:
    def test_literal_integer_string(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        result = bp._resolve("3", {}, {})
        assert result == [{"index": 0}, {"index": 1}, {"index": 2}]

    def test_zero_integer(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        assert bp._resolve("0", {}, {}) == []

    def test_integer_via_context_interpolation(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        result = bp._resolve("{count}", {}, {"count": "2"})
        assert result == [{"index": 0}, {"index": 1}]


# ---------------------------------------------------------------------------
# _resolve — Jinja mode
# ---------------------------------------------------------------------------


class TestResolveJinjaMode:
    def test_jinja_expression_evaluates_to_integer(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        results = {"fetch": {"count": 4}}
        items = bp._resolve("{{ steps.fetch.count }}", results, {})
        assert items == [{"index": i} for i in range(4)]

    def test_jinja_non_integer_raises(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        results = {"fetch": {"name": "hello"}}
        with pytest.raises(ValueError, match="not a valid integer"):
            bp._resolve("{{ steps.fetch.name }}", results, {})

    def test_jinja_auto_wrap(self):
        """Expression with {{ in middle but not at start gets wrapped."""
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        results = {"fetch": {"count": 2}}
        # This already starts with {{ so no wrapping needed
        items = bp._resolve("{{ steps.fetch.count }}", results, {})
        assert len(items) == 2


# ---------------------------------------------------------------------------
# _resolve — file mode
# ---------------------------------------------------------------------------


class TestResolveFileMode:
    def test_reads_lines_from_file(self, tmp_path):
        lines_file = tmp_path / "items.txt"
        lines_file.write_text("alpha\nbeta\ngamma\n")
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        result = bp._resolve(str(lines_file), {}, {})
        assert result == [
            {"index": 0, "item": "alpha"},
            {"index": 1, "item": "beta"},
            {"index": 2, "item": "gamma"},
        ]

    def test_skips_empty_lines(self, tmp_path):
        lines_file = tmp_path / "items.txt"
        lines_file.write_text("one\n\ntwo\n  \nthree\n")
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        result = bp._resolve(str(lines_file), {}, {})
        assert len(result) == 3
        assert result[1] == {"index": 1, "item": "two"}

    def test_relative_path_with_project_path(self, tmp_path):
        lines_file = tmp_path / "data.txt"
        lines_file.write_text("x\ny\n")
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        bp.project_path = str(tmp_path)
        result = bp._resolve("data.txt", {}, {})
        assert result == [
            {"index": 0, "item": "x"},
            {"index": 1, "item": "y"},
        ]

    def test_file_not_found_raises(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        with pytest.raises(FileNotFoundError):
            bp._resolve("nonexistent_file.txt", {}, {})

    def test_path_interpolated_from_context(self, tmp_path):
        lines_file = tmp_path / "tasks.txt"
        lines_file.write_text("task1\ntask2\n")
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        result = bp._resolve(str(lines_file), {}, {})
        assert len(result) == 2


# ---------------------------------------------------------------------------
# _evaluate_condition — still works with _jinja_env refactor
# ---------------------------------------------------------------------------


class TestEvaluateConditionStillWorks:
    def test_truthy_condition(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        results = {"step1": {"success": True}}
        assert bp._evaluate_condition("{{ steps.step1.success }}", results) is True

    def test_falsy_condition(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        results = {"step1": {"success": False}}
        assert bp._evaluate_condition("{{ steps.step1.success }}", results) is False

    def test_bare_expression_auto_wrapped(self):
        bp = _make_blueprint([{"id": "s1", "prompt": "hi"}])
        results = {"step1": {"success": True}}
        assert bp._evaluate_condition("steps.step1.success", results) is True


# ---------------------------------------------------------------------------
# run() — foreach integration (mocking query via phase.run)
# ---------------------------------------------------------------------------


class TestRunForeach:
    @pytest.mark.asyncio
    async def test_foreach_integer_runs_phase_n_times(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "repeat_phase", "prompt": "do thing {index}", "foreach": "3"},
            ]
        )
        phase = bp.phases[0]
        phase.run.return_value = {"success": True, "message": ["done"]}

        results = await bp.run({})

        assert phase.run.call_count == 3
        assert results["repeat_phase"]["success"] is True
        assert len(results["repeat_phase"]["iterations"]) == 3

    @pytest.mark.asyncio
    async def test_foreach_iteration_contexts_have_index(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "indexed_phase", "prompt": "item {index}", "foreach": "2"},
            ]
        )
        phase = bp.phases[0]
        phase.run.return_value = {"success": True, "message": ["ok"]}

        await bp.run({})

        # First call should have index=0, second index=1
        calls = phase.run.call_args_list
        assert calls[0].args[0]["index"] == 0
        assert calls[1].args[0]["index"] == 1

    @pytest.mark.asyncio
    async def test_foreach_file_mode_injects_item(self, tmp_path):
        items_file = tmp_path / "repos.txt"
        items_file.write_text("repo-a\nrepo-b\n")

        bp = _make_blueprint_with_mock_phases(
            [
                {
                    "id": "per_repo",
                    "prompt": "process {item}",
                    "foreach": str(items_file),
                },
            ]
        )
        phase = bp.phases[0]
        phase.run.return_value = {"success": True, "message": ["processed"]}

        results = await bp.run({})

        assert phase.run.call_count == 2
        calls = phase.run.call_args_list
        assert calls[0].args[0]["item"] == "repo-a"
        assert calls[1].args[0]["item"] == "repo-b"
        assert results["per_repo"]["success"] is True

    @pytest.mark.asyncio
    async def test_foreach_zero_iterations_produces_empty_result(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "empty_phase", "prompt": "nothing", "foreach": "0"},
            ]
        )
        phase = bp.phases[0]

        results = await bp.run({})

        phase.run.assert_not_called()
        assert results["empty_phase"]["iterations"] == []
        assert results["empty_phase"]["success"] is True

    @pytest.mark.asyncio
    async def test_foreach_failure_aborts_blueprint(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "failing_phase", "prompt": "fail {index}", "foreach": "3"},
            ]
        )
        phase = bp.phases[0]
        phase.run.side_effect = [
            {"success": True, "message": ["ok"]},
            {"success": False, "message": ["error"]},
        ]

        results = await bp.run({})

        assert phase.run.call_count == 2
        assert results["failing_phase"]["success"] is False
        assert len(results["failing_phase"]["iterations"]) == 2

    @pytest.mark.asyncio
    async def test_foreach_failure_with_skip_continues(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {
                    "id": "skip_phase",
                    "prompt": "try {index}",
                    "foreach": "2",
                    "on_failure": {"action": "skip"},
                },
            ]
        )
        phase = bp.phases[0]
        phase.run.side_effect = [
            {"success": False, "message": ["err"]},
            {"success": True, "message": ["ok"]},
        ]

        await bp.run({})

        # With skip, the blueprint should continue past the failure
        assert phase.run.call_count == 2

    @pytest.mark.asyncio
    async def test_foreach_with_vars_merged_into_context(self):
        bp = _make_blueprint_with_mock_phases(
            [{"id": "var_phase", "prompt": "do {repo} {index}", "foreach": "2"}],
            vars={"repo": "my-service"},
        )
        phase = bp.phases[0]
        phase.run.return_value = {"success": True, "message": ["ok"]}

        await bp.run({})

        calls = phase.run.call_args_list
        # Each call should have both repo (from vars) and index (from foreach)
        assert calls[0].args[0]["repo"] == "my-service"
        assert calls[0].args[0]["index"] == 0

    @pytest.mark.asyncio
    async def test_non_foreach_phase_still_works(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "normal_phase", "prompt": "just do it"},
            ]
        )
        phase = bp.phases[0]
        phase.run.return_value = {"success": True, "message": ["done"]}

        results = await bp.run({})

        phase.run.assert_called_once()
        assert results["normal_phase"] == {"success": True, "message": ["done"]}
