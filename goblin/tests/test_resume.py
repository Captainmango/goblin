"""Tests for checkpoint/resume integration with V1Blueprint.run()."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from goblin.blueprint import V1Blueprint
from goblin.checkpoint import Checkpoint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_blueprint(phases: list[dict], vars: dict | None = None, defaults: dict | None = None) -> V1Blueprint:
    spec: dict[str, Any] = {"name": "test_bp", "phases": phases}
    if vars:
        spec["vars"] = vars
    if defaults:
        spec["defaults"] = defaults
    return V1Blueprint(spec)


def _make_blueprint_with_mock_phases(
    phases_raw: list[dict], vars: dict | None = None, defaults: dict | None = None
) -> V1Blueprint:
    bp = _make_blueprint(phases_raw, vars, defaults)
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
# Resume skips completed phases
# ---------------------------------------------------------------------------


class TestResumeSkipsCompletedPhases:
    @pytest.mark.asyncio
    async def test_completed_phases_are_skipped(self, tmp_path):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "a", "prompt": "first"},
                {"id": "b", "prompt": "second"},
                {"id": "c", "prompt": "third"},
            ]
        )
        bp.phases[0].run.return_value = {"success": True, "message": ["a done"]}
        bp.phases[1].run.return_value = {"success": True, "message": ["b done"]}
        bp.phases[2].run.return_value = {"success": True, "message": ["c done"]}

        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.phase_started("a", {})
        await cp.phase_completed("a", {"success": True, "message": ["done"]})

        results = await bp.run({}, checkpoint=cp)

        bp.phases[0].run.assert_not_called()
        bp.phases[1].run.assert_called_once()
        bp.phases[2].run.assert_called_once()
        assert results["a"] == {"success": True, "message": ["done"]}

    @pytest.mark.asyncio
    async def test_all_phases_completed_returns_immediately(self, tmp_path):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "a", "prompt": "first"},
                {"id": "b", "prompt": "second"},
            ]
        )
        bp.phases[0].run.return_value = {"success": True}
        bp.phases[1].run.return_value = {"success": True}

        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.phase_started("a", {})
        await cp.phase_completed("a", {"success": True, "message": ["a"]})
        await cp.phase_started("b", {})
        await cp.phase_completed("b", {"success": True, "message": ["b"]})

        results = await bp.run({}, checkpoint=cp)

        bp.phases[0].run.assert_not_called()
        bp.phases[1].run.assert_not_called()
        assert results["a"] == {"success": True, "message": ["a"]}
        assert results["b"] == {"success": True, "message": ["b"]}


# ---------------------------------------------------------------------------
# Resume preloads results for conditions
# ---------------------------------------------------------------------------


class TestResumePreloadsResults:
    @pytest.mark.asyncio
    async def test_condition_evaluates_against_preloaded_results(self, tmp_path):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "a", "prompt": "first"},
                {"id": "b", "prompt": "second", "condition": "{{ steps.a.success }}"},
            ]
        )
        bp.phases[1].run.return_value = {"success": True, "message": ["ok"]}

        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.phase_started("a", {})
        await cp.phase_completed("a", {"success": True, "message": ["ok"]})

        await bp.run({}, checkpoint=cp)

        bp.phases[0].run.assert_not_called()
        bp.phases[1].run.assert_called_once()

    @pytest.mark.asyncio
    async def test_condition_false_from_preloaded_results_skips_phase(self, tmp_path):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "a", "prompt": "first"},
                {"id": "b", "prompt": "second", "condition": "{{ steps.a.success }}"},
            ]
        )
        bp.phases[1].run.return_value = {"success": True, "message": ["ok"]}

        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.phase_started("a", {})
        await cp.phase_completed("a", {"success": False, "message": ["err"]})

        await bp.run({}, checkpoint=cp)

        bp.phases[0].run.assert_not_called()
        bp.phases[1].run.assert_not_called()


# ---------------------------------------------------------------------------
# Resume checkpoint writes on completion
# ---------------------------------------------------------------------------


class TestResumeCheckpointWritesOnCompletion:
    @pytest.mark.asyncio
    async def test_run_writes_completed_status(self, tmp_path):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "a", "prompt": "do"},
            ]
        )
        bp.phases[0].run.return_value = {"success": True, "message": ["ok"]}

        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await bp.run({}, checkpoint=cp)

        with open(cp.path) as f:
            data = json.load(f)
        assert data["status"] == "completed"

    @pytest.mark.asyncio
    async def test_failed_run_writes_failed_status(self, tmp_path):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "a", "prompt": "break"},
            ]
        )
        bp.phases[0].run.return_value = {"success": False, "message": ["error"]}

        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await bp.run({}, checkpoint=cp)

        with open(cp.path) as f:
            data = json.load(f)
        assert data["status"] == "failed"


# ---------------------------------------------------------------------------
# Resume with parallel phases
# ---------------------------------------------------------------------------


class TestResumeParallelPhases:
    @pytest.mark.asyncio
    async def test_skips_finished_phases_and_re_runs_interrupted(self, tmp_path):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "a", "prompt": "first"},
                {"id": "b", "prompt": "second", "depends_on": ["a"]},
                {"id": "c", "prompt": "third", "depends_on": ["a"]},
            ]
        )
        bp.phases[0].run.return_value = {"success": True, "message": ["a done"]}
        bp.phases[1].run.return_value = {"success": True, "message": ["b done"]}
        bp.phases[2].run.return_value = {"success": True, "message": ["c done"]}

        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.phase_started("a", {})
        await cp.phase_completed("a", {"success": True, "message": ["a done"]})
        await cp.phase_started("b", {})
        await cp.phase_completed("b", {"success": True, "message": ["b done"]})
        # c was never started

        results = await bp.run({}, checkpoint=cp)

        bp.phases[0].run.assert_not_called()
        bp.phases[1].run.assert_not_called()
        bp.phases[2].run.assert_called_once()
        assert results["a"]["message"] == ["a done"]
        assert results["b"]["message"] == ["b done"]
        assert results["c"]["message"] == ["c done"]

    @pytest.mark.asyncio
    async def test_failed_phase_is_re_run_on_resume(self, tmp_path):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "a", "prompt": "first"},
                {"id": "b", "prompt": "second", "depends_on": ["a"]},
            ]
        )
        bp.phases[0].run.return_value = {"success": True}
        bp.phases[1].run.return_value = {"success": True}

        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.phase_started("a", {})
        await cp.phase_failed("a", {"success": False, "message": ["err"]})

        results = await bp.run({}, checkpoint=cp)

        bp.phases[0].run.assert_called_once()
        bp.phases[1].run.assert_called_once()
        assert results["a"]["success"] is True
        assert results["b"]["success"] is True

    @pytest.mark.asyncio
    async def test_failed_phase_re_run_fails_again(self, tmp_path):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "a", "prompt": "first"},
                {"id": "b", "prompt": "second", "depends_on": ["a"]},
            ]
        )
        bp.phases[0].run.return_value = {"success": False, "message": ["err again"]}
        bp.phases[1].run.return_value = {"success": True}

        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await cp.phase_started("a", {})
        await cp.phase_failed("a", {"success": False, "message": ["err"]})

        results = await bp.run({}, checkpoint=cp)

        bp.phases[0].run.assert_called_once()
        bp.phases[1].run.assert_not_called()
        assert results["a"]["success"] is False
