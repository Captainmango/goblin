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


def _make_blueprint(steps: list[dict], vars: dict | None = None, defaults: dict | None = None) -> V1Blueprint:
    spec: dict[str, Any] = {"name": "test_wf", "steps": steps}
    if vars:
        spec["vars"] = vars
    if defaults:
        spec["defaults"] = defaults
    return V1Blueprint(spec)


def _make_blueprint_with_mock_steps(
    steps_raw: list[dict], vars: dict | None = None, defaults: dict | None = None
) -> V1Blueprint:
    bp = _make_blueprint(steps_raw, vars, defaults)
    mock_steps = []
    for raw in steps_raw:
        step = AsyncMock()
        step.id = raw["id"]
        step.condition = raw.get("condition")
        step.foreach = raw.get("foreach")
        step.on_failure = raw.get("on_failure")
        mock_steps.append(step)
    bp.steps = mock_steps
    return bp


# ---------------------------------------------------------------------------
# Resume skips completed steps
# ---------------------------------------------------------------------------


class TestResumeSkipsCompletedSteps:
    @pytest.mark.asyncio
    async def test_completed_steps_are_skipped(self, tmp_path):
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "a", "prompt": "first"},
                {"id": "b", "prompt": "second"},
                {"id": "c", "prompt": "third"},
            ]
        )
        bp.steps[0].run.return_value = {"success": True, "message": ["a done"]}
        bp.steps[1].run.return_value = {"success": True, "message": ["b done"]}
        bp.steps[2].run.return_value = {"success": True, "message": ["c done"]}

        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        cp.step_started("a", {})
        cp.step_completed("a", {"success": True, "message": ["done"]})

        results = await bp.run({}, checkpoint=cp)

        bp.steps[0].run.assert_not_called()
        bp.steps[1].run.assert_called_once()
        bp.steps[2].run.assert_called_once()
        assert results["a"] == {"success": True, "message": ["done"]}

    @pytest.mark.asyncio
    async def test_all_steps_completed_returns_immediately(self, tmp_path):
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "a", "prompt": "first"},
                {"id": "b", "prompt": "second"},
            ]
        )
        bp.steps[0].run.return_value = {"success": True}
        bp.steps[1].run.return_value = {"success": True}

        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        cp.step_started("a", {})
        cp.step_completed("a", {"success": True, "message": ["a"]})
        cp.step_started("b", {})
        cp.step_completed("b", {"success": True, "message": ["b"]})

        results = await bp.run({}, checkpoint=cp)

        bp.steps[0].run.assert_not_called()
        bp.steps[1].run.assert_not_called()
        assert results["a"] == {"success": True, "message": ["a"]}
        assert results["b"] == {"success": True, "message": ["b"]}


# ---------------------------------------------------------------------------
# Resume preloads results for conditions
# ---------------------------------------------------------------------------


class TestResumePreloadsResults:
    @pytest.mark.asyncio
    async def test_condition_evaluates_against_preloaded_results(self, tmp_path):
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "a", "prompt": "first"},
                {"id": "b", "prompt": "second", "condition": "{{ steps.a.success }}"},
            ]
        )
        bp.steps[1].run.return_value = {"success": True, "message": ["ok"]}

        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        cp.step_started("a", {})
        cp.step_completed("a", {"success": True, "message": ["ok"]})

        await bp.run({}, checkpoint=cp)

        bp.steps[0].run.assert_not_called()
        bp.steps[1].run.assert_called_once()

    @pytest.mark.asyncio
    async def test_condition_false_from_preloaded_results_skips_step(self, tmp_path):
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "a", "prompt": "first"},
                {"id": "b", "prompt": "second", "condition": "{{ steps.a.success }}"},
            ]
        )
        bp.steps[1].run.return_value = {"success": True, "message": ["ok"]}

        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        cp.step_started("a", {})
        cp.step_completed("a", {"success": False, "message": ["err"]})

        await bp.run({}, checkpoint=cp)

        bp.steps[0].run.assert_not_called()
        bp.steps[1].run.assert_not_called()


# ---------------------------------------------------------------------------
# Resume checkpoint writes on completion
# ---------------------------------------------------------------------------


class TestResumeCheckpointWritesOnCompletion:
    @pytest.mark.asyncio
    async def test_run_writes_completed_status(self, tmp_path):
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "a", "prompt": "do"},
            ]
        )
        bp.steps[0].run.return_value = {"success": True, "message": ["ok"]}

        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await bp.run({}, checkpoint=cp)

        with open(cp.path) as f:
            data = json.load(f)
        assert data["status"] == "completed"

    @pytest.mark.asyncio
    async def test_failed_run_writes_failed_status(self, tmp_path):
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "a", "prompt": "break"},
            ]
        )
        bp.steps[0].run.return_value = {"success": False, "message": ["error"]}

        cp = Checkpoint(str(tmp_path), "r1", "test.yaml", {})
        await bp.run({}, checkpoint=cp)

        with open(cp.path) as f:
            data = json.load(f)
        assert data["status"] == "failed"
