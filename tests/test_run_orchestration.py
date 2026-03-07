"""Tests for V1Blueprint.run() orchestration — condition skipping, vars merging,
multi-step sequencing, and on_failure handling (skip, retry, abort, notify)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from goblin.blueprint import V1Blueprint
from goblin.hooks import HOOK_REGISTRY


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


@pytest.fixture(autouse=True)
def _clean_registry():
    saved = dict(HOOK_REGISTRY)
    yield
    HOOK_REGISTRY.clear()
    HOOK_REGISTRY.update(saved)


# ---------------------------------------------------------------------------
# Condition skipping in run()
# ---------------------------------------------------------------------------


class TestRunConditionSkipping:
    @pytest.mark.asyncio
    async def test_step_skipped_when_condition_false(self):
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "first", "prompt": "go", "on_failure": {"action": "skip"}},
                {
                    "id": "second",
                    "prompt": "do",
                    "condition": "{{ steps.first.success }}",
                },
            ]
        )
        bp.steps[0].run.return_value = {"success": False, "message": ["err"]}
        bp.steps[1].run.return_value = {"success": True, "message": ["ok"]}

        results = await bp.run({})

        # first step fails (skip continues), condition evaluates first's success=False
        bp.steps[1].run.assert_not_called()
        assert "second" not in results

    @pytest.mark.asyncio
    async def test_condition_true_allows_step(self):
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "first", "prompt": "go"},
                {
                    "id": "second",
                    "prompt": "do",
                    "condition": "{{ steps.first.success }}",
                },
            ]
        )
        bp.steps[0].run.return_value = {"success": True, "message": ["ok"]}
        bp.steps[1].run.return_value = {"success": True, "message": ["done"]}

        results = await bp.run({})

        bp.steps[0].run.assert_called_once()
        bp.steps[1].run.assert_called_once()
        assert "second" in results

    @pytest.mark.asyncio
    async def test_condition_false_skips_step(self):
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "first", "prompt": "go"},
                {
                    "id": "second",
                    "prompt": "do",
                    "condition": "{{ steps.first.do_thing }}",
                },
                {"id": "third", "prompt": "finish"},
            ]
        )
        bp.steps[0].run.return_value = {"success": True, "do_thing": False}
        bp.steps[1].run.return_value = {"success": True}
        bp.steps[2].run.return_value = {"success": True, "message": ["done"]}

        results = await bp.run({})

        bp.steps[0].run.assert_called_once()
        bp.steps[1].run.assert_not_called()
        bp.steps[2].run.assert_called_once()
        assert "second" not in results
        assert "third" in results


# ---------------------------------------------------------------------------
# Vars merging
# ---------------------------------------------------------------------------


class TestRunVarsMerging:
    @pytest.mark.asyncio
    async def test_blueprint_vars_available_in_context(self):
        bp = _make_blueprint_with_mock_steps(
            [{"id": "s1", "prompt": "use {repo}"}],
            vars={"repo": "my-service"},
        )
        bp.steps[0].run.return_value = {"success": True}

        await bp.run({})

        ctx = bp.steps[0].run.call_args.args[0]
        assert ctx["repo"] == "my-service"

    @pytest.mark.asyncio
    async def test_caller_context_overrides_blueprint_vars(self):
        bp = _make_blueprint_with_mock_steps(
            [{"id": "s1", "prompt": "use {repo}"}],
            vars={"repo": "default-service"},
        )
        bp.steps[0].run.return_value = {"success": True}

        await bp.run({"repo": "override-service"})

        ctx = bp.steps[0].run.call_args.args[0]
        assert ctx["repo"] == "override-service"

    @pytest.mark.asyncio
    async def test_vars_and_caller_context_both_available(self):
        bp = _make_blueprint_with_mock_steps(
            [{"id": "s1", "prompt": "use {repo} on {branch}"}],
            vars={"repo": "my-service"},
        )
        bp.steps[0].run.return_value = {"success": True}

        await bp.run({"branch": "main"})

        ctx = bp.steps[0].run.call_args.args[0]
        assert ctx["repo"] == "my-service"
        assert ctx["branch"] == "main"


# ---------------------------------------------------------------------------
# MCP servers passthrough
# ---------------------------------------------------------------------------


class TestRunMcpServersPassthrough:
    def test_mcp_servers_in_defaults_accessible(self):
        bp = _make_blueprint(
            [{"id": "s1", "prompt": "do stuff"}],
            defaults={"mcp_servers": {"gh": {"command": "npx"}}},
        )
        assert bp.spec["defaults"]["mcp_servers"]["gh"]["command"] == "npx"


# ---------------------------------------------------------------------------
# Multi-step sequencing
# ---------------------------------------------------------------------------


class TestRunMultiStep:
    @pytest.mark.asyncio
    async def test_steps_run_in_order(self):
        call_order = []
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "a", "prompt": "first"},
                {"id": "b", "prompt": "second"},
                {"id": "c", "prompt": "third"},
            ]
        )
        for i, step in enumerate(bp.steps):
            step.run.side_effect = lambda ctx, idx=i: call_order.append(idx) or {"success": True, "message": ["ok"]}

        await bp.run({})

        assert call_order == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_results_accumulate_per_step(self):
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "a", "prompt": "first"},
                {"id": "b", "prompt": "second"},
            ]
        )
        bp.steps[0].run.return_value = {"success": True, "message": ["alpha"]}
        bp.steps[1].run.return_value = {"success": True, "message": ["beta"]}

        results = await bp.run({})

        assert results["a"] == {"success": True, "message": ["alpha"]}
        assert results["b"] == {"success": True, "message": ["beta"]}


# ---------------------------------------------------------------------------
# on_failure — abort (default)
# ---------------------------------------------------------------------------


class TestRunFailureAbort:
    @pytest.mark.asyncio
    async def test_default_action_aborts_on_failure(self):
        bp = _make_blueprint_with_mock_steps(
            [
                {"id": "fail_step", "prompt": "break"},
                {"id": "next_step", "prompt": "never reached"},
            ]
        )
        bp.steps[0].run.return_value = {"success": False, "message": ["error"]}
        bp.steps[1].run.return_value = {"success": True}

        results = await bp.run({})

        bp.steps[1].run.assert_not_called()
        assert "next_step" not in results

    @pytest.mark.asyncio
    async def test_explicit_abort_stops_workflow(self):
        bp = _make_blueprint_with_mock_steps(
            [
                {
                    "id": "fail_step",
                    "prompt": "break",
                    "on_failure": {"action": "abort"},
                },
                {"id": "next_step", "prompt": "never reached"},
            ]
        )
        bp.steps[0].run.return_value = {"success": False, "message": ["error"]}

        results = await bp.run({})

        bp.steps[1].run.assert_not_called()
        assert results["fail_step"]["success"] is False


# ---------------------------------------------------------------------------
# on_failure — skip
# ---------------------------------------------------------------------------


class TestRunFailureSkip:
    @pytest.mark.asyncio
    async def test_skip_continues_to_next_step(self):
        bp = _make_blueprint_with_mock_steps(
            [
                {
                    "id": "fail_step",
                    "prompt": "break",
                    "on_failure": {"action": "skip"},
                },
                {"id": "next_step", "prompt": "continue"},
            ]
        )
        bp.steps[0].run.return_value = {"success": False, "message": ["error"]}
        bp.steps[1].run.return_value = {"success": True, "message": ["ok"]}

        results = await bp.run({})

        bp.steps[1].run.assert_called_once()
        assert "next_step" in results


# ---------------------------------------------------------------------------
# on_failure — retry
# ---------------------------------------------------------------------------


class TestRunFailureRetry:
    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        bp = _make_blueprint_with_mock_steps(
            [
                {
                    "id": "retry_step",
                    "prompt": "try",
                    "on_failure": {"action": "retry"},
                },
                {"id": "next_step", "prompt": "after"},
            ]
        )
        bp.steps[0].run.side_effect = [
            {"success": False, "message": ["fail"]},
            {"success": True, "message": ["ok"]},
        ]
        bp.steps[1].run.return_value = {"success": True, "message": ["done"]}

        _results = await bp.run({})

        assert bp.steps[0].run.call_count == 2
        bp.steps[1].run.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_fails_again_aborts(self):
        bp = _make_blueprint_with_mock_steps(
            [
                {
                    "id": "retry_step",
                    "prompt": "try",
                    "on_failure": {"action": "retry"},
                },
                {"id": "next_step", "prompt": "never"},
            ]
        )
        bp.steps[0].run.side_effect = [
            {"success": False, "message": ["fail"]},
            {"success": False, "message": ["fail again"]},
        ]

        _results = await bp.run({})

        assert bp.steps[0].run.call_count == 2
        bp.steps[1].run.assert_not_called()


# ---------------------------------------------------------------------------
# on_failure — notify
# ---------------------------------------------------------------------------


class TestRunFailureNotify:
    @pytest.mark.asyncio
    async def test_notify_hook_called_on_failure(self):
        notify_mock = MagicMock()
        HOOK_REGISTRY["alert_hook"] = notify_mock

        bp = _make_blueprint_with_mock_steps(
            [
                {
                    "id": "fail_step",
                    "prompt": "break",
                    "on_failure": {"action": "skip", "notify": "alert_hook"},
                },
            ]
        )
        bp.steps[0].run.return_value = {"success": False, "message": ["error"]}

        await bp.run({})

        notify_mock.assert_called_once()
        # The hook receives the step result
        call_arg = notify_mock.call_args.args[0]
        assert call_arg["success"] is False

    @pytest.mark.asyncio
    async def test_notify_with_unregistered_hook_does_not_crash(self):
        bp = _make_blueprint_with_mock_steps(
            [
                {
                    "id": "fail_step",
                    "prompt": "break",
                    "on_failure": {"action": "skip", "notify": "nonexistent_hook"},
                },
            ]
        )
        bp.steps[0].run.return_value = {"success": False, "message": ["error"]}

        # Should not raise
        results = await bp.run({})
        assert "fail_step" in results


# ---------------------------------------------------------------------------
# Mutable default argument regression
# ---------------------------------------------------------------------------


class TestRunNoSharedDefaultContext:
    @pytest.mark.asyncio
    async def test_successive_calls_without_context_do_not_share_state(self):
        """Two consecutive bp.run() calls with no args must produce independent results."""
        bp = _make_blueprint_with_mock_steps(
            [{"id": "s1", "prompt": "do thing"}],
            vars={"key": "value"},
        )
        bp.steps[0].run.return_value = {"success": True, "message": ["ok"]}

        results_first = await bp.run()
        results_second = await bp.run()

        assert results_first == results_second
        assert results_first is not results_second
