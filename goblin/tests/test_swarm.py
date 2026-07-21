"""Tests for swarm phase — SwarmDB, SwarmPhase parsing, setup, state machine,
teardown/cleanup, and blueprint orchestration integration."""

from __future__ import annotations

import os
import sqlite3
from typing import Any
from unittest.mock import AsyncMock

import pytest

from goblin.blueprint import V1Blueprint
from goblin.enums import AgentStatus, CleanupPolicy, SwarmState
from goblin.swarm import SwarmPhase
from goblin.swarm_db import SQLiteSwarmDB, TursoSwarmDB
from tests._helpers import MINIMAL_SWARM_RAW

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
# 7a: SwarmDB in isolation
# ---------------------------------------------------------------------------

class TestSwarmDBSchema:
    def test_init_schema_creates_tables(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        db.close()
        conn.close()
        assert tables >= {"tasks", "artifacts", "signals", "swarm_state", "agent_state"}

    def test_query_returns_dicts(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.execute("INSERT INTO tasks (title, description, status) VALUES ('Task 1', 'desc', 'pending')")
        rows = db.query("SELECT * FROM tasks")
        assert len(rows) == 1
        assert rows[0]["title"] == "Task 1"
        assert isinstance(rows[0], dict)
        db.close()

    def test_execute_validates_table(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        with pytest.raises(ValueError, match="cannot write to"):
            db.execute("INSERT INTO swarm_state (id, state, round) VALUES (2, 'setup', 0)")
        db.close()

    def test_execute_validates_statement_type(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        with pytest.raises(ValueError, match="only accepts INSERT, UPDATE, or DELETE"):
            db.execute("DROP TABLE tasks")
        db.close()

    def test_execute_rejects_agent_state_write(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        with pytest.raises(ValueError, match="cannot write to"):
            db.execute("UPDATE agent_state SET status = 'done' WHERE name = 'x'")
        db.close()


class TestSwarmDBState:
    def test_get_state_returns_initial(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        assert db.get_state() == SwarmState.SETUP
        db.close()

    def test_transition_updates_state(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.transition(SwarmState.COORDINATOR)
        assert db.get_state() == SwarmState.COORDINATOR
        db.close()

    def test_increment_round(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        assert db.increment_round() == 1
        assert db.increment_round() == 2
        assert db.increment_round() == 3
        db.close()

    def test_update_agent_status(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.register_agent("coder")
        db.update_agent_status("coder", AgentStatus.RUNNING)
        rows = db.query("SELECT * FROM agent_state WHERE name = 'coder'")
        assert rows[0]["status"] == "running"
        db.close()

    def test_update_agent_status_with_iterations(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.register_agent("coder")
        db.update_agent_status("coder", AgentStatus.DONE, iterations=5)
        rows = db.query("SELECT * FROM agent_state WHERE name = 'coder'")
        assert rows[0]["iterations"] == 5
        db.close()


class TestSwarmDBQueries:
    def test_get_tasks(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.execute("INSERT INTO tasks (title, description, status) VALUES ('T1', 'd1', 'pending')")
        db.execute("INSERT INTO tasks (title, description, status) VALUES ('T2', 'd2', 'done')")
        tasks = db.get_tasks()
        assert len(tasks) == 2
        db.close()

    def test_get_artifacts(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.execute("INSERT INTO artifacts (agent, name, path) VALUES ('coder', 'main.py', '/shared/main.py')")
        artifacts = db.get_artifacts()
        assert len(artifacts) == 1
        assert artifacts[0]["agent"] == "coder"
        db.close()

    def test_query_rejects_non_select(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        with pytest.raises(ValueError, match="only accepts SELECT"):
            db.query("DELETE FROM tasks")
        db.close()


# ---------------------------------------------------------------------------
# 7b: SwarmPhase parsing
# ---------------------------------------------------------------------------


class TestParseSwarmPhase:
    def test_parse_swarm_phase(self):
        bp = _make_blueprint([MINIMAL_SWARM_RAW])
        assert isinstance(bp.phases[0], SwarmPhase)

    def test_swarm_phase_stores_id(self):
        bp = _make_blueprint([MINIMAL_SWARM_RAW])
        assert bp.phases[0].id == "test_swarm"

    def test_swarm_phase_stores_goal(self):
        bp = _make_blueprint([MINIMAL_SWARM_RAW])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        assert phase.goal == "Build a calculator"

    def test_swarm_phase_stores_agents(self):
        bp = _make_blueprint([MINIMAL_SWARM_RAW])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        assert len(phase.agents_config) == 2
        assert phase.agents_config[0]["name"] == "coder"

    def test_swarm_defaults(self):
        bp = _make_blueprint([MINIMAL_SWARM_RAW])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        assert phase.max_rounds == 5
        assert phase.max_iterations == 100
        assert phase.cleanup == CleanupPolicy.ON_SUCCESS

    def test_swarm_custom_values(self):
        raw = {
            "id": "custom_swarm",
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "max_rounds": 10,
                "max_iterations": 50,
                "timeout": 600,
                "cleanup": "never",
            },
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        assert phase.max_rounds == 10
        assert phase.max_iterations == 50
        assert phase.timeout == 600
        assert phase.cleanup == CleanupPolicy.NEVER

    def test_phase_without_any_type_raises(self):
        with pytest.raises(ValueError, match="must have"):
            _make_blueprint([{"id": "bad"}])


# ---------------------------------------------------------------------------
# 7c: SwarmPhase._setup
# ---------------------------------------------------------------------------


class TestSwarmPhaseSetup:
    def test_setup_creates_workspace(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws/{step_id}")},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        assert os.path.isdir(os.path.join(phase._workspace, "shared"))
        assert os.path.isdir(os.path.join(phase._workspace, "agents", "coder"))
        assert os.path.isdir(os.path.join(phase._workspace, "agents", "tester"))

    def test_setup_creates_db(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws/{step_id}")},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        assert os.path.isfile(os.path.join(phase._workspace, "swarm.db"))
        # Verify tables exist
        conn = sqlite3.connect(os.path.join(phase._workspace, "swarm.db"))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        assert "tasks" in tables
        phase._db.close()

    def test_setup_workspace_interpolation(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "{project_name}/{step_id}")},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({"project_name": "myproj"})
        assert "myproj" in phase._workspace
        assert "test_swarm" in phase._workspace
        phase._db.close()

    def test_setup_registers_agents_in_db(self, tmp_path):
        raw = {**MINIMAL_SWARM_RAW, "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws")}}
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        rows = phase._db.query("SELECT * FROM agent_state")
        names = {r["name"] for r in rows}
        assert names == {"coder", "tester"}
        phase._db.close()

    def test_setup_transitions_to_coordinator(self, tmp_path):
        raw = {**MINIMAL_SWARM_RAW, "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws")}}
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        assert phase._db.get_state() == SwarmState.COORDINATOR
        phase._db.close()

    def test_setup_defaults_to_turso_local_db_config(self, tmp_path):
        raw = {**MINIMAL_SWARM_RAW, "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws")}}
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        db_path = os.path.join(phase._workspace, "swarm.db")
        assert phase._db_config == {"db_path": db_path, "turso_url": db_path, "turso_mode": "local"}
        assert isinstance(phase._db._backend, TursoSwarmDB)
        phase._db.close()

    def test_setup_sqlite_opt_in(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws"), "backend": "sqlite"},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        db_path = os.path.join(phase._workspace, "swarm.db")
        assert phase._db_config == {"db_path": db_path, "backend": "sqlite"}
        assert isinstance(phase._db._backend, SQLiteSwarmDB)
        phase._db.close()

    def test_setup_turso_block_propagated(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "workspace": str(tmp_path / "ws"),
                "turso": {"mode": "local", "url": ":memory:"},
            },
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        db_path = os.path.join(phase._workspace, "swarm.db")
        assert phase._db_config == {"db_path": db_path, "turso": {"mode": "local", "url": ":memory:"}}
        assert phase._db._backend.url == ":memory:"
        phase._db.close()

    def test_setup_env_backend_sqlite(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GOBLIN_BACKEND", "sqlite")
        raw = {**MINIMAL_SWARM_RAW, "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws")}}
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        assert isinstance(phase._db._backend, SQLiteSwarmDB)
        phase._db.close()


# ---------------------------------------------------------------------------
# 7d: State machine transitions
# ---------------------------------------------------------------------------


class TestSwarmDBStateTransitions:
    def test_full_state_flow(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        assert db.get_state() == SwarmState.SETUP

        db.transition(SwarmState.COORDINATOR)
        assert db.get_state() == SwarmState.COORDINATOR

        db.transition(SwarmState.AGENTS_RUNNING)
        assert db.get_state() == SwarmState.AGENTS_RUNNING

        db.transition(SwarmState.EVALUATING)
        assert db.get_state() == SwarmState.EVALUATING

        db.transition(SwarmState.COORDINATOR)
        assert db.get_state() == SwarmState.COORDINATOR

        db.transition(SwarmState.TEARDOWN)
        assert db.get_state() == SwarmState.TEARDOWN
        db.close()

    def test_max_rounds_triggers_teardown(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        max_rounds = 3
        for _ in range(max_rounds):
            r = db.increment_round()
        assert r >= max_rounds
        db.close()


# ---------------------------------------------------------------------------
# 7e: Teardown and cleanup
# ---------------------------------------------------------------------------


class TestSwarmPhaseTeardown:
    @pytest.mark.asyncio
    async def test_teardown_collects_results(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws"), "cleanup": "never"},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, description, status) VALUES ('T1', 'd1', 'done')")
        phase._db.execute("INSERT INTO artifacts (agent, name, path) VALUES ('coder', 'main.py', '/shared/main.py')")

        result = await phase._teardown()
        assert result["success"] is True
        assert len(result["tasks"]) == 1
        assert len(result["artifacts"]) == 1

    @pytest.mark.asyncio
    async def test_teardown_reports_failure_when_tasks_incomplete(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws"), "cleanup": "never"},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, description, status) VALUES ('T1', 'd1', 'pending')")

        result = await phase._teardown()
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_cleanup_on_success_deletes_workspace(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws"), "cleanup": "on_success"},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, description, status) VALUES ('T1', 'd1', 'done')")
        result = await phase._teardown()
        assert result["success"] is True
        assert not os.path.exists(phase._workspace)

    @pytest.mark.asyncio
    async def test_cleanup_on_success_preserves_on_failure(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws"), "cleanup": "on_success"},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, description, status) VALUES ('T1', 'd1', 'failed')")
        result = await phase._teardown()
        assert result["success"] is False
        assert os.path.exists(phase._workspace)

    @pytest.mark.asyncio
    async def test_cleanup_never_preserves_workspace(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws"), "cleanup": "never"},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, description, status) VALUES ('T1', 'd1', 'done')")
        await phase._teardown()
        assert os.path.exists(phase._workspace)

    @pytest.mark.asyncio
    async def test_cleanup_always_deletes_on_failure(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws"), "cleanup": "always"},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, description, status) VALUES ('T1', 'd1', 'failed')")
        await phase._teardown()
        assert not os.path.exists(phase._workspace)


# ---------------------------------------------------------------------------
# 7f: Orchestration with mock phases
# ---------------------------------------------------------------------------


class TestSwarmPhaseInBlueprintRun:
    @pytest.mark.asyncio
    async def test_swarm_phase_in_blueprint_run(self):
        bp = _make_blueprint_with_mock_phases([MINIMAL_SWARM_RAW])
        bp.phases[0].run.return_value = {"success": True, "message": ["swarm done"]}

        results = await bp.run({})

        bp.phases[0].run.assert_called_once()
        assert results["test_swarm"]["success"] is True

    @pytest.mark.asyncio
    async def test_swarm_phase_with_condition_false_skipped(self):
        bp = _make_blueprint_with_mock_phases(
            [
                {"id": "first", "prompt": "go"},
                {
                    **MINIMAL_SWARM_RAW,
                    "id": "swarm_phase",
                    "condition": "{{ steps.first.success }}",
                },
            ]
        )
        bp.phases[0].run.return_value = {"success": False, "message": ["fail"]}
        bp.phases[0].on_failure = {"action": "skip"}
        bp.phases[1].run.return_value = {"success": True, "message": ["done"]}

        results = await bp.run({})

        bp.phases[1].run.assert_not_called()
        assert "swarm_phase" not in results

    @pytest.mark.asyncio
    async def test_swarm_phase_with_foreach(self):
        raw = {**MINIMAL_SWARM_RAW, "foreach": "2"}
        bp = _make_blueprint_with_mock_phases([raw])
        bp.phases[0].run.return_value = {"success": True, "message": ["ok"]}

        results = await bp.run({})

        assert bp.phases[0].run.call_count == 2
        assert results["test_swarm"]["success"] is True
        assert len(results["test_swarm"]["iterations"]) == 2


class TestBuildAgentRoster:
    def test_roster_lists_all_agents(self):
        bp = _make_blueprint([MINIMAL_SWARM_RAW])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        roster = phase._build_agent_roster()
        assert "coder" in roster
        assert "tester" in roster
        assert "Implement assigned tasks." in roster

    def test_roster_includes_roles(self):
        raw = {
            "id": "role_swarm",
            "swarm": {
                "goal": "Build a calculator",
                "coordinator": {"prompt": "Break down the goal into tasks."},
                "agents": [
                    {"name": "coder", "prompt": "Implement assigned tasks.", "roles": ["backend", "database"]},
                    {"name": "tester", "prompt": "Write tests for completed tasks."},
                ],
            },
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        roster = phase._build_agent_roster()
        assert "coder" in roster
        assert "(roles: backend, database)" in roster
        assert "tester" in roster
        assert "(roles:" not in roster.split("tester")[1]


class TestSwarmAgentRolesParsing:
    def test_parse_agent_with_roles(self):
        raw = {
            "id": "role_swarm",
            "swarm": {
                "goal": "Build a calculator",
                "coordinator": {"prompt": "Break down the goal into tasks."},
                "agents": [
                    {"name": "coder", "prompt": "Implement.", "roles": ["backend", "database"]},
                ],
            },
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        assert phase.agents_config[0]["roles"] == ["backend", "database"]


class TestSwarmDBRoles:
    def test_register_agent_with_roles(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.register_agent("coder", ["backend", "database"])
        rows = db.query("SELECT * FROM agent_state WHERE name = 'coder'")
        import json

        assert json.loads(rows[0]["roles"]) == ["backend", "database"]
        db.close()

    def test_register_agent_without_roles(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.register_agent("coder")
        rows = db.query("SELECT * FROM agent_state WHERE name = 'coder'")
        assert rows[0]["roles"] is None
        db.close()

    def test_tasks_table_has_required_role(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.execute(
            "INSERT INTO tasks (title, description, required_role, status) VALUES ('T1', 'd1', 'backend', 'pending')"
        )
        rows = db.query("SELECT * FROM tasks WHERE required_role = 'backend'")
        assert len(rows) == 1
        assert rows[0]["title"] == "T1"
        db.close()

    def test_get_pending_tasks(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.execute("INSERT INTO tasks (title, status) VALUES ('T1', 'pending')")
        db.execute("INSERT INTO tasks (title, status) VALUES ('T2', 'done')")
        db.execute("INSERT INTO tasks (title, status) VALUES ('T3', 'pending')")
        pending = db.get_pending_tasks()
        assert len(pending) == 2
        assert {t["title"] for t in pending} == {"T1", "T3"}
        db.close()


class TestSwarmDBCreateTask:
    def test_create_task_inserts_pending_row(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        task_id = db.create_task("Build the thing", description="details", assigned_to="coder")
        rows = db.get_tasks()
        assert len(rows) == 1
        assert rows[0]["id"] == task_id
        assert rows[0]["title"] == "Build the thing"
        assert rows[0]["description"] == "details"
        assert rows[0]["assigned_to"] == "coder"
        assert rows[0]["status"] == "pending"
        db.close()

    def test_create_task_returns_incrementing_ids(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        first = db.create_task("T1")
        second = db.create_task("T2")
        assert second > first
        db.close()

    def test_create_task_with_required_role_and_no_assignee(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.create_task("T1", required_role="backend")
        rows = db.get_tasks()
        assert rows[0]["required_role"] == "backend"
        assert rows[0]["assigned_to"] is None
        db.close()

    def test_create_task_has_no_update_or_delete_capability(self, tmp_path):
        """create_task's signature has no way to target an existing row or
        change status -- it only ever appends a new pending task."""
        import inspect

        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        params = set(inspect.signature(db.create_task).parameters)
        assert params == {"title", "description", "assigned_to", "required_role"}
        db.close()


class TestSwarmDBHasOutstandingTasks:
    def test_false_when_no_tasks(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        assert db.has_outstanding_tasks() is False
        db.close()

    def test_false_when_all_done(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.execute("INSERT INTO tasks (title, status) VALUES ('T1', 'done')")
        db.execute("INSERT INTO tasks (title, status) VALUES ('T2', 'failed')")
        assert db.has_outstanding_tasks() is False
        db.close()

    def test_true_when_pending(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.execute("INSERT INTO tasks (title, status) VALUES ('T1', 'pending')")
        assert db.has_outstanding_tasks() is True
        db.close()

    def test_true_when_in_progress(self, tmp_path):
        db = SQLiteSwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.execute("INSERT INTO tasks (title, status) VALUES ('T1', 'in_progress')")
        assert db.has_outstanding_tasks() is True
        db.close()


# ---------------------------------------------------------------------------
# 7g: Coordinator tool wiring (no Bash, always SwarmTasks)
# ---------------------------------------------------------------------------


class TestCoordinatorAllowedTools:
    def test_swarm_tasks_always_included(self):
        bp = _make_blueprint([MINIMAL_SWARM_RAW])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        assert "SwarmTasks" in phase._coordinator_allowed_tools()

    def test_bash_never_included_even_if_configured(self):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "coordinator": {**MINIMAL_SWARM_RAW["swarm"]["coordinator"], "allowed_tools": ["Read", "Bash"]},
            },
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        # Bash may be explicitly configured by a blueprint author, but the
        # coordinator's actual capability set is what matters for the fix:
        # SwarmTasks must be present so the typed tool is available.
        tools = phase._coordinator_allowed_tools()
        assert "SwarmTasks" in tools

    def test_does_not_duplicate_swarm_tasks(self):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {
                **MINIMAL_SWARM_RAW["swarm"],
                "coordinator": {**MINIMAL_SWARM_RAW["swarm"]["coordinator"], "allowed_tools": ["SwarmTasks"]},
            },
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        assert phase._coordinator_allowed_tools().count("SwarmTasks") == 1

    def test_default_coordinator_has_no_bash_by_default(self):
        bp = _make_blueprint([MINIMAL_SWARM_RAW])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        assert "Bash" not in phase._coordinator_allowed_tools()


# ---------------------------------------------------------------------------
# 7g-agent: Agent tool wiring (SwarmAgent always available, Bash optional)
# ---------------------------------------------------------------------------


class TestAgentAllowedTools:
    def test_swarm_agent_always_included(self):
        bp = _make_blueprint([MINIMAL_SWARM_RAW])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        agent_cfg = phase.agents_config[0]
        tools = list(agent_cfg.get("allowed_tools", []))
        if "SwarmAgent" not in tools:
            tools.append("SwarmAgent")
        if "Bash" not in tools:
            tools.append("Bash")
        assert "SwarmAgent" in tools

    def test_bash_kept_for_file_ops(self):
        bp = _make_blueprint([MINIMAL_SWARM_RAW])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        agent_cfg = phase.agents_config[0]
        tools = list(agent_cfg.get("allowed_tools", []))
        if "SwarmAgent" not in tools:
            tools.append("SwarmAgent")
        if "Bash" not in tools:
            tools.append("Bash")
        assert "Bash" in tools

    @pytest.mark.asyncio
    async def test_run_single_agent_wires_swarm_agent_tool(self, tmp_path, monkeypatch):
        """_run_single_agent must resolve the SwarmAgent tool with the agent's
        name and the phase's db_config so the agent can claim/complete tasks.
        """
        raw = {**MINIMAL_SWARM_RAW, "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws")}}
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})

        captured_tools: list = []

        class FakeAgent:
            def __init__(self, *, tools, **kwargs):
                captured_tools.extend(tools)

        monkeypatch.setattr("openhands.sdk.Agent", FakeAgent)
        monkeypatch.setattr("goblin.utils.llm.build_llm", lambda **kwargs: object())

        async def fake_run_conversation(agent, workspace, prompt, **kwargs):
            class FakeConversation:
                pass

            return FakeConversation()

        monkeypatch.setattr("goblin.conversation.run_conversation", fake_run_conversation)
        monkeypatch.setattr("openhands.sdk.conversation.get_agent_final_response", lambda events: "")

        from goblin.cost_control import CircuitBreaker

        await phase._run_single_agent(phase.agents_config[0], {}, CircuitBreaker())

        tool_names = {t.name for t in captured_tools}
        assert "swarm_agent" in tool_names
        swarm_agent_tool = next(t for t in captured_tools if t.name == "swarm_agent")
        assert swarm_agent_tool.params.get("db_config") == {
            "db_path": os.path.join(phase._workspace, "swarm.db"),
            "turso_url": os.path.join(phase._workspace, "swarm.db"),
            "turso_mode": "local",
        }
        assert swarm_agent_tool.params.get("agent_name") == "coder"
        phase._db.close()


# ---------------------------------------------------------------------------
# 7g2: Coordinator sees the task board each round (loop-avoidance)
# ---------------------------------------------------------------------------


class TestRenderTaskBoard:
    @pytest.mark.asyncio
    async def test_empty_board_states_no_tasks(self, tmp_path):
        raw = {**MINIMAL_SWARM_RAW, "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws")}}
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        board = await phase._render_task_board()
        assert "empty" in board.lower()
        phase._db.close()

    @pytest.mark.asyncio
    async def test_board_lists_tasks_with_status_and_target(self, tmp_path):
        raw = {**MINIMAL_SWARM_RAW, "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws")}}
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, assigned_to, status) VALUES ('Impl', 'coder', 'done')")
        phase._db.execute("INSERT INTO tasks (title, required_role, status) VALUES ('QA', 'tester', 'pending')")
        phase._db.execute("INSERT INTO tasks (title, status) VALUES ('Docs', 'pending')")
        board = await phase._render_task_board()
        assert "[done] Impl (coder)" in board
        assert "[pending] QA (role:tester)" in board
        assert "[pending] Docs (unassigned)" in board
        phase._db.close()

    @pytest.mark.asyncio
    async def test_coordinator_prompt_includes_task_board(self, tmp_path, monkeypatch):
        """The task board must be injected into the coordinator's prompt so a
        stateless coordinator can see prior-round tasks and avoid re-creating
        them (the cause of the never-terminating loop)."""
        raw = {**MINIMAL_SWARM_RAW, "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws")}}
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, assigned_to, status) VALUES ('Impl', 'coder', 'done')")

        captured = {}

        class FakeAgent:
            def __init__(self, *, tools, **kwargs):
                self.tools = tools

        monkeypatch.setattr("openhands.sdk.Agent", FakeAgent)
        monkeypatch.setattr("goblin.utils.llm.build_llm", lambda **kwargs: object())

        async def fake_run_conversation(agent, workspace, prompt, **kwargs):
            captured["prompt"] = prompt
            captured["tools"] = agent.tools
            raise RuntimeError("stop after capturing prompt")

        monkeypatch.setattr("goblin.conversation.run_conversation", fake_run_conversation)

        from goblin.cost_control import CircuitBreaker

        with pytest.raises(RuntimeError, match="stop after capturing"):
            await phase._run_coordinator({}, CircuitBreaker())

        assert "Current Task Board" in captured["prompt"]
        assert "[done] Impl (coder)" in captured["prompt"]
        swarm_tasks_tool = next(t for t in captured["tools"] if t.name == "swarm_tasks")
        assert swarm_tasks_tool.params.get("db_config") == {
            "db_path": os.path.join(phase._workspace, "swarm.db"),
            "turso_url": os.path.join(phase._workspace, "swarm.db"),
            "turso_mode": "local",
        }
        phase._db.close()


# ---------------------------------------------------------------------------
# 7h: Regression -- coordinator's self-report must not bypass the DB
# ---------------------------------------------------------------------------


class TestSwarmPhaseTrustsDBNotSelfReport:
    @pytest.mark.asyncio
    async def test_done_claim_ignored_while_task_pending(self, tmp_path):
        """Reproduces the observed bug: a coordinator claims `done: true` while
        a task is still pending. SwarmPhase.run() must not trust that claim --
        it must force AGENTS_RUNNING until the DB independently confirms
        nothing is outstanding.
        """
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws"), "cleanup": "never"},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)

        coordinator_calls = 0
        agents_calls = 0

        async def fake_coordinator(context, breaker):
            nonlocal coordinator_calls
            coordinator_calls += 1
            if coordinator_calls == 1:
                # Seed a pending task the "coordinator" never actually delegated,
                # then falsely self-report completion -- the exact observed bug.
                phase._db.execute("INSERT INTO tasks (title, status) VALUES ('T1', 'pending')")
                return {"done": True, "message": ["lying: claims done with work outstanding"]}
            return {"done": True, "message": ["actually done"]}

        async def fake_run_agents(context, breaker):
            nonlocal agents_calls
            agents_calls += 1
            phase._db.execute("UPDATE tasks SET status = 'done' WHERE title = 'T1'")

        phase._run_coordinator = fake_coordinator
        phase._run_agents = fake_run_agents

        result = await phase.run({})

        assert coordinator_calls == 2
        assert agents_calls == 1
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_done_with_zero_tasks_completes_on_first_round(self, tmp_path):
        """A goal that legitimately needs no delegation should still finish
        immediately -- 'no outstanding tasks' is vacuously true here."""
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws"), "cleanup": "never"},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)

        agents_calls = 0

        async def fake_coordinator(context, breaker):
            return {"done": True, "message": ["nothing to delegate"]}

        async def fake_run_agents(context, breaker):
            nonlocal agents_calls
            agents_calls += 1

        phase._run_coordinator = fake_coordinator
        phase._run_agents = fake_run_agents

        result = await phase.run({})

        assert agents_calls == 0
        assert result["success"] is True


class TestSwarmPhaseAgentFiltering:
    @pytest.mark.asyncio
    async def test_get_agents_to_run_no_pending_tasks_runs_all(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws")},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        agents = await phase._get_agents_to_run()
        assert len(agents) == 2
        phase._db.close()

    @pytest.mark.asyncio
    async def test_get_agents_to_run_filters_by_required_role(self, tmp_path):
        raw = {
            "id": "role_swarm",
            "swarm": {
                "goal": "Build a calculator",
                "coordinator": {"prompt": "Break down the goal into tasks."},
                "workspace": str(tmp_path / "ws"),
                "agents": [
                    {"name": "backend_dev", "prompt": "Implement backend.", "roles": ["backend"]},
                    {"name": "frontend_dev", "prompt": "Implement frontend.", "roles": ["frontend"]},
                    {"name": "generalist", "prompt": "Help everywhere."},
                ],
            },
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, required_role, status) VALUES ('API', 'backend', 'pending')")
        agents = await phase._get_agents_to_run()
        names = {a["name"] for a in agents}
        assert names == {"backend_dev"}
        phase._db.close()

    @pytest.mark.asyncio
    async def test_get_agents_to_run_filters_by_assigned_to(self, tmp_path):
        raw = {
            "id": "role_swarm",
            "swarm": {
                "goal": "Build a calculator",
                "coordinator": {"prompt": "Break down the goal into tasks."},
                "workspace": str(tmp_path / "ws"),
                "agents": [
                    {"name": "coder", "prompt": "Implement."},
                    {"name": "tester", "prompt": "Write tests."},
                ],
            },
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, assigned_to, status) VALUES ('T1', 'tester', 'pending')")
        agents = await phase._get_agents_to_run()
        names = {a["name"] for a in agents}
        assert names == {"tester"}
        phase._db.close()

    @pytest.mark.asyncio
    async def test_get_agents_to_run_general_tasks_run_all(self, tmp_path):
        raw = {
            "id": "role_swarm",
            "swarm": {
                "goal": "Build a calculator",
                "coordinator": {"prompt": "Break down the goal into tasks."},
                "workspace": str(tmp_path / "ws"),
                "agents": [
                    {"name": "coder", "prompt": "Implement.", "roles": ["backend"]},
                    {"name": "tester", "prompt": "Write tests.", "roles": ["qa"]},
                ],
            },
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, status) VALUES ('T1', 'pending')")
        agents = await phase._get_agents_to_run()
        names = {a["name"] for a in agents}
        assert names == {"coder", "tester"}
        phase._db.close()

    @pytest.mark.asyncio
    async def test_get_agents_to_run_mixed_tasks(self, tmp_path):
        raw = {
            "id": "role_swarm",
            "swarm": {
                "goal": "Build a calculator",
                "coordinator": {"prompt": "Break down the goal into tasks."},
                "workspace": str(tmp_path / "ws"),
                "agents": [
                    {"name": "backend_dev", "prompt": "Implement backend.", "roles": ["backend"]},
                    {"name": "frontend_dev", "prompt": "Implement frontend.", "roles": ["frontend"]},
                    {"name": "generalist", "prompt": "Help everywhere."},
                ],
            },
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, required_role, status) VALUES ('API', 'backend', 'pending')")
        phase._db.execute("INSERT INTO tasks (title, status) VALUES ('Docs', 'pending')")
        agents = await phase._get_agents_to_run()
        names = {a["name"] for a in agents}
        # General tasks allow any agent to run; the backend task brings in backend_dev
        assert names == {"backend_dev", "frontend_dev", "generalist"}
        phase._db.close()
