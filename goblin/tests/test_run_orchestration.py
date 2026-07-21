"""Tests for V1Blueprint.run() orchestration — condition skipping, vars merging,
multi-phase sequencing, and on_failure handling (skip, retry, abort, notify)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from goblin.blueprint import V1Blueprint
from goblin.hooks import HOOK_REGISTRY

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
    async def test_phase_skipped_when_condition_false(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "first", "prompt": "go", "on_failure": {"action": "skip"}},
                {
                    "id": "second",
                    "prompt": "do",
                    "condition": "{{ steps.first.success }}",
                },
            ]
        )
        bp.phases[0].run.return_value = {"success": False, "message": ["err"]}
        bp.phases[1].run.return_value = {"success": True, "message": ["ok"]}

        results = await bp.run({})

        # first phase fails (skip continues), condition evaluates first's success=False
        bp.phases[1].run.assert_not_called()
        assert "second" not in results

    @pytest.mark.asyncio
    async def test_condition_true_allows_phase(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "first", "prompt": "go"},
                {
                    "id": "second",
                    "prompt": "do",
                    "condition": "{{ steps.first.success }}",
                },
            ]
        )
        bp.phases[0].run.return_value = {"success": True, "message": ["ok"]}
        bp.phases[1].run.return_value = {"success": True, "message": ["done"]}

        results = await bp.run({})

        bp.phases[0].run.assert_called_once()
        bp.phases[1].run.assert_called_once()
        assert "second" in results

    @pytest.mark.asyncio
    async def test_condition_false_skips_phase(self):
        bp = _make_blueprint_with_mock_phases(
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
        bp.phases[0].run.return_value = {"success": True, "do_thing": False}
        bp.phases[1].run.return_value = {"success": True}
        bp.phases[2].run.return_value = {"success": True, "message": ["done"]}

        results = await bp.run({})

        bp.phases[0].run.assert_called_once()
        bp.phases[1].run.assert_not_called()
        bp.phases[2].run.assert_called_once()
        assert "second" not in results
        assert "third" in results


# ---------------------------------------------------------------------------
# Vars merging
# ---------------------------------------------------------------------------


class TestRunVarsMerging:
    @pytest.mark.asyncio
    async def test_blueprint_vars_available_in_context(self):
        bp = _make_blueprint_with_mock_phases(
            [{"id": "s1", "prompt": "use {repo}"}],
            vars={"repo": "my-service"},
        )
        bp.phases[0].run.return_value = {"success": True}

        await bp.run({})

        ctx = bp.phases[0].run.call_args.args[0]
        assert ctx["repo"] == "my-service"

    @pytest.mark.asyncio
    async def test_caller_context_overrides_blueprint_vars(self):
        bp = _make_blueprint_with_mock_phases(
            [{"id": "s1", "prompt": "use {repo}"}],
            vars={"repo": "default-service"},
        )
        bp.phases[0].run.return_value = {"success": True}

        await bp.run({"repo": "override-service"})

        ctx = bp.phases[0].run.call_args.args[0]
        assert ctx["repo"] == "override-service"

    @pytest.mark.asyncio
    async def test_vars_and_caller_context_both_available(self):
        bp = _make_blueprint_with_mock_phases(
            [{"id": "s1", "prompt": "use {repo} on {branch}"}],
            vars={"repo": "my-service"},
        )
        bp.phases[0].run.return_value = {"success": True}

        await bp.run({"branch": "main"})

        ctx = bp.phases[0].run.call_args.args[0]
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
# Multi-phase sequencing
# ---------------------------------------------------------------------------


class TestRunMultiPhase:
    @pytest.mark.asyncio
    async def test_phases_run_in_order(self):
        call_order = []
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "a", "prompt": "first"},
                {"id": "b", "prompt": "second"},
                {"id": "c", "prompt": "third"},
            ]
        )
        for i, phase in enumerate(bp.phases):
            phase.run.side_effect = lambda ctx, idx=i: call_order.append(idx) or {"success": True, "message": ["ok"]}

        await bp.run({})

        assert call_order == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_results_accumulate_per_phase(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "a", "prompt": "first"},
                {"id": "b", "prompt": "second"},
            ]
        )
        bp.phases[0].run.return_value = {"success": True, "message": ["alpha"]}
        bp.phases[1].run.return_value = {"success": True, "message": ["beta"]}

        results = await bp.run({})

        assert results["a"] == {"success": True, "message": ["alpha"]}
        assert results["b"] == {"success": True, "message": ["beta"]}


# ---------------------------------------------------------------------------
# on_failure — abort (default)
# ---------------------------------------------------------------------------


class TestRunFailureAbort:
    @pytest.mark.asyncio
    async def test_default_action_aborts_on_failure(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "fail_phase", "prompt": "break"},
                {"id": "next_phase", "prompt": "never reached"},
            ]
        )
        bp.phases[0].run.return_value = {"success": False, "message": ["error"]}
        bp.phases[1].run.return_value = {"success": True}

        results = await bp.run({})

        bp.phases[1].run.assert_not_called()
        assert "next_phase" not in results

    @pytest.mark.asyncio
    async def test_explicit_abort_stops_blueprint(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {
                    "id": "fail_phase",
                    "prompt": "break",
                    "on_failure": {"action": "abort"},
                },
                {"id": "next_phase", "prompt": "never reached"},
            ]
        )
        bp.phases[0].run.return_value = {"success": False, "message": ["error"]}

        results = await bp.run({})

        bp.phases[1].run.assert_not_called()
        assert results["fail_phase"]["success"] is False


# ---------------------------------------------------------------------------
# on_failure — skip
# ---------------------------------------------------------------------------


class TestRunFailureSkip:
    @pytest.mark.asyncio
    async def test_skip_continues_to_next_phase(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {
                    "id": "fail_phase",
                    "prompt": "break",
                    "on_failure": {"action": "skip"},
                },
                {"id": "next_phase", "prompt": "continue"},
            ]
        )
        bp.phases[0].run.return_value = {"success": False, "message": ["error"]}
        bp.phases[1].run.return_value = {"success": True, "message": ["ok"]}

        results = await bp.run({})

        bp.phases[1].run.assert_called_once()
        assert "next_phase" in results


# ---------------------------------------------------------------------------
# on_failure — retry
# ---------------------------------------------------------------------------


class TestRunFailureRetry:
    @pytest.mark.asyncio
    async def test_retry_succeeds_on_second_attempt(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {
                    "id": "retry_phase",
                    "prompt": "try",
                    "on_failure": {"action": "retry"},
                },
                {"id": "next_phase", "prompt": "after"},
            ]
        )
        bp.phases[0].run.side_effect = [
            {"success": False, "message": ["fail"]},
            {"success": True, "message": ["ok"]},
        ]
        bp.phases[1].run.return_value = {"success": True, "message": ["done"]}

        _results = await bp.run({})

        assert bp.phases[0].run.call_count == 2
        bp.phases[1].run.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_fails_again_aborts(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {
                    "id": "retry_phase",
                    "prompt": "try",
                    "on_failure": {"action": "retry"},
                },
                {"id": "next_phase", "prompt": "never"},
            ]
        )
        bp.phases[0].run.side_effect = [
            {"success": False, "message": ["fail"]},
            {"success": False, "message": ["fail again"]},
        ]

        _results = await bp.run({})

        assert bp.phases[0].run.call_count == 2
        bp.phases[1].run.assert_not_called()


# ---------------------------------------------------------------------------
# on_failure — notify
# ---------------------------------------------------------------------------


class TestRunFailureNotify:
    @pytest.mark.asyncio
    async def test_notify_hook_called_on_failure(self):
        notify_mock = MagicMock()
        HOOK_REGISTRY["alert_hook"] = notify_mock

        bp = _make_blueprint_with_mock_phases(
            [
                {
                    "id": "fail_phase",
                    "prompt": "break",
                    "on_failure": {"action": "skip", "notify": "alert_hook"},
                },
            ]
        )
        bp.phases[0].run.return_value = {"success": False, "message": ["error"]}

        await bp.run({})

        notify_mock.assert_called_once()
        # The hook receives the phase result
        call_arg = notify_mock.call_args.args[0]
        assert call_arg["success"] is False

    @pytest.mark.asyncio
    async def test_notify_with_unregistered_hook_does_not_crash(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {
                    "id": "fail_phase",
                    "prompt": "break",
                    "on_failure": {"action": "skip", "notify": "nonexistent_hook"},
                },
            ]
        )
        bp.phases[0].run.return_value = {"success": False, "message": ["error"]}

        # Should not raise
        results = await bp.run({})
        assert "fail_phase" in results


# ---------------------------------------------------------------------------
# on_failure — max_retries
# ---------------------------------------------------------------------------


class TestRunFailureMaxRetries:
    @pytest.mark.asyncio
    async def test_default_max_retries_is_one(self):
        """No max_retries set: phase fails twice; blueprint aborts after the one retry."""
        bp = _make_blueprint_with_mock_phases(
            [
                {
                    "id": "retry_phase",
                    "prompt": "try",
                    "on_failure": {"action": "retry"},
                },
                {"id": "next_phase", "prompt": "never"},
            ]
        )
        bp.phases[0].run.side_effect = [
            {"success": False, "message": ["fail"]},
            {"success": False, "message": ["fail again"]},
        ]

        results = await bp.run({})

        assert bp.phases[0].run.call_count == 2
        bp.phases[1].run.assert_not_called()
        assert results["retry_phase"]["success"] is False

    @pytest.mark.asyncio
    async def test_custom_max_retries_succeeds_on_last_attempt(self):
        """max_retries: 3 — phase fails 3 times then succeeds on the 4th call; blueprint continues."""
        bp = _make_blueprint_with_mock_phases(
            [
                {
                    "id": "retry_phase",
                    "prompt": "try",
                    "on_failure": {"action": "retry", "max_retries": 3},
                },
                {"id": "next_phase", "prompt": "after"},
            ]
        )
        bp.phases[0].run.side_effect = [
            {"success": False},
            {"success": False},
            {"success": False},
            {"success": True, "message": ["ok"]},
        ]
        bp.phases[1].run.return_value = {"success": True}

        results = await bp.run({})

        assert bp.phases[0].run.call_count == 4
        bp.phases[1].run.assert_called_once()
        assert results["retry_phase"]["success"] is True

    @pytest.mark.asyncio
    async def test_exhausted_retries_aborts(self):
        """max_retries: 2 — phase fails all attempts; run called 3 times (original + 2), blueprint aborts."""
        bp = _make_blueprint_with_mock_phases(
            [
                {
                    "id": "retry_phase",
                    "prompt": "try",
                    "on_failure": {"action": "retry", "max_retries": 2},
                },
                {"id": "next_phase", "prompt": "never"},
            ]
        )
        bp.phases[0].run.side_effect = [
            {"success": False},
            {"success": False},
            {"success": False},
        ]

        results = await bp.run({})

        assert bp.phases[0].run.call_count == 3
        bp.phases[1].run.assert_not_called()
        assert results["retry_phase"]["success"] is False

    @pytest.mark.asyncio
    async def test_explicit_max_retries_one_same_as_default(self):
        """max_retries: 1 explicit — same behavior as omitting the field."""
        bp = _make_blueprint_with_mock_phases(
            [
                {
                    "id": "retry_phase",
                    "prompt": "try",
                    "on_failure": {"action": "retry", "max_retries": 1},
                },
                {"id": "next_phase", "prompt": "after"},
            ]
        )
        bp.phases[0].run.side_effect = [
            {"success": False},
            {"success": True},
        ]
        bp.phases[1].run.return_value = {"success": True}

        await bp.run({})

        assert bp.phases[0].run.call_count == 2
        bp.phases[1].run.assert_called_once()


# ---------------------------------------------------------------------------
# Mutable default argument regression
# ---------------------------------------------------------------------------


class TestRunNoSharedDefaultContext:
    @pytest.mark.asyncio
    async def test_successive_calls_without_context_do_not_share_state(self):
        """Two consecutive bp.run() calls with no args must produce independent results."""
        bp = _make_blueprint_with_mock_phases(
            [{"id": "s1", "prompt": "do thing"}],
            vars={"key": "value"},
        )
        bp.phases[0].run.return_value = {"success": True, "message": ["ok"]}

        results_first = await bp.run()
        results_second = await bp.run()

        assert results_first == results_second
        assert results_first is not results_second


# ---------------------------------------------------------------------------
# DAG validation
# ---------------------------------------------------------------------------


class TestValidateDependencies:
    def test_cycle_detection_raises(self):
        with pytest.raises(ValueError, match="Cycle detected"):
            _make_blueprint(
                [
                    {"id": "a", "prompt": "go", "depends_on": ["b"]},
                    {"id": "b", "prompt": "go", "depends_on": ["a"]},
                ]
            )

    def test_missing_dependency_raises(self):
        with pytest.raises(ValueError, match="dependency 'missing' does not exist"):
            _make_blueprint(
                [
                    {"id": "a", "prompt": "go", "depends_on": ["missing"]},
                ]
            )

    def test_self_dependency_raises(self):
        with pytest.raises(ValueError, match="Cycle detected"):
            _make_blueprint(
                [
                    {"id": "a", "prompt": "go", "depends_on": ["a"]},
                ]
            )


# ---------------------------------------------------------------------------
# Parallel execution
# ---------------------------------------------------------------------------


class TestRunParallelExecution:
    @pytest.mark.asyncio
    async def test_two_phases_run_concurrently(self):
        started = [asyncio.Event(), asyncio.Event()]

        async def run_a(ctx):
            started[0].set()
            await started[1].wait()
            return {"success": True, "message": ["a done"]}

        async def run_b(ctx):
            started[1].set()
            await started[0].wait()
            return {"success": True, "message": ["b done"]}

        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "parent", "prompt": "go"},
                {"id": "child_a", "prompt": "a", "depends_on": ["parent"]},
                {"id": "child_b", "prompt": "b", "depends_on": ["parent"]},
            ]
        )
        bp.phases[0].run.return_value = {"success": True}
        bp.phases[1].run.side_effect = run_a
        bp.phases[2].run.side_effect = run_b

        results = await bp.run({})

        assert bp.phases[1].run.called
        assert bp.phases[2].run.called
        assert results["child_a"]["success"] is True
        assert results["child_b"]["success"] is True

    @pytest.mark.asyncio
    async def test_dependency_chaining_waits_for_multiple_deps(self):
        call_order = []

        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "a", "prompt": "first"},
                {"id": "b", "prompt": "second", "depends_on": ["a"]},
                {"id": "c", "prompt": "third", "depends_on": ["a", "b"]},
            ]
        )

        for i, phase in enumerate(bp.phases):
            phase.run.side_effect = lambda ctx, idx=i: call_order.append(idx) or {"success": True, "message": ["ok"]}

        await bp.run({})

        assert call_order == [0, 1, 2]


# ---------------------------------------------------------------------------
# Failure propagation in parallel
# ---------------------------------------------------------------------------


class TestRunParallelFailurePropagation:
    @pytest.mark.asyncio
    async def test_abort_stops_dependents_but_lets_siblings_finish(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "parent", "prompt": "go"},
                {"id": "child_a", "prompt": "a", "depends_on": ["parent"]},
                {"id": "child_b", "prompt": "b", "depends_on": ["parent"]},
                {"id": "grandchild", "prompt": "c", "depends_on": ["child_a"]},
            ]
        )
        bp.phases[0].run.return_value = {"success": True}

        async def fail_after_yield(ctx):
            await asyncio.sleep(0)
            return {"success": False, "message": ["err"]}

        bp.phases[1].run.side_effect = fail_after_yield
        bp.phases[2].run.return_value = {"success": True}
        bp.phases[3].run.return_value = {"success": True}

        results = await bp.run({})

        bp.phases[2].run.assert_called_once()
        bp.phases[3].run.assert_not_called()
        assert results["child_a"]["success"] is False
        assert results["child_b"]["success"] is True

    @pytest.mark.asyncio
    async def test_abort_event_stops_foreach_between_iterations(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "parent", "prompt": "go"},
                {"id": "b", "prompt": "b {index}", "foreach": "5", "depends_on": ["parent"]},
                {"id": "c", "prompt": "c", "depends_on": ["parent"]},
            ]
        )
        bp.phases[0].run.return_value = {"success": True}

        call_count = [0]

        async def run_b(ctx):
            call_count[0] += 1
            await asyncio.sleep(0)  # yield so c can set abort between iterations
            return {"success": True}

        async def run_c(ctx):
            # Fail immediately to set abort while b might be iterating
            return {"success": False, "message": ["err"]}

        bp.phases[1].run.side_effect = run_b
        bp.phases[2].run.side_effect = run_c

        await bp.run({})

        # b should have started but stopped when abort was set
        assert call_count[0] >= 1
        assert call_count[0] < 5


# ---------------------------------------------------------------------------
# Immutable result safety
# ---------------------------------------------------------------------------


class TestRunImmutableResults:
    @pytest.mark.asyncio
    async def test_mutating_original_result_does_not_affect_results(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "parent", "prompt": "go"},
                {"id": "child", "prompt": "do", "depends_on": ["parent"]},
            ]
        )
        mutable = {"success": True, "items": [1]}
        bp.phases[0].run.return_value = mutable
        bp.phases[1].run.return_value = {"success": True}

        results = await bp.run({})

        assert results["parent"]["items"] == [1]
        mutable["items"].append(2)
        assert results["parent"]["items"] == [1]
