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
from goblin.swarm_db import SwarmDB


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


MINIMAL_SWARM_RAW = {
    "id": "test_swarm",
    "swarm": {
        "goal": "Build a calculator",
        "coordinator": {
            "prompt": "Break down the goal into tasks.",
            "allowed_tools": ["Read"],
        },
        "agents": [
            {"name": "coder", "prompt": "Implement assigned tasks."},
            {"name": "tester", "prompt": "Write tests for completed tasks."},
        ],
    },
}


# ---------------------------------------------------------------------------
# 7a: SwarmDB in isolation
# ---------------------------------------------------------------------------


class TestSwarmDBSchema:
    def test_init_schema_creates_tables(self, tmp_path):
        db = SwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        db.close()
        conn.close()
        assert tables >= {"tasks", "artifacts", "signals", "swarm_state", "agent_state"}

    def test_query_returns_dicts(self, tmp_path):
        db = SwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.execute("INSERT INTO tasks (title, description, status) VALUES ('Task 1', 'desc', 'pending')")
        rows = db.query("SELECT * FROM tasks")
        assert len(rows) == 1
        assert rows[0]["title"] == "Task 1"
        assert isinstance(rows[0], dict)
        db.close()

    def test_execute_validates_table(self, tmp_path):
        db = SwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        with pytest.raises(ValueError, match="cannot write to"):
            db.execute("INSERT INTO swarm_state (id, state, round) VALUES (2, 'setup', 0)")
        db.close()

    def test_execute_validates_statement_type(self, tmp_path):
        db = SwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        with pytest.raises(ValueError, match="only accepts INSERT, UPDATE, or DELETE"):
            db.execute("DROP TABLE tasks")
        db.close()

    def test_execute_rejects_agent_state_write(self, tmp_path):
        db = SwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        with pytest.raises(ValueError, match="cannot write to"):
            db.execute("UPDATE agent_state SET status = 'done' WHERE name = 'x'")
        db.close()


class TestSwarmDBState:
    def test_get_state_returns_initial(self, tmp_path):
        db = SwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        assert db.get_state() == SwarmState.SETUP
        db.close()

    def test_transition_updates_state(self, tmp_path):
        db = SwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.transition(SwarmState.COORDINATOR)
        assert db.get_state() == SwarmState.COORDINATOR
        db.close()

    def test_increment_round(self, tmp_path):
        db = SwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        assert db.increment_round() == 1
        assert db.increment_round() == 2
        assert db.increment_round() == 3
        db.close()

    def test_update_agent_status(self, tmp_path):
        db = SwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.register_agent("coder")
        db.update_agent_status("coder", AgentStatus.RUNNING)
        rows = db.query("SELECT * FROM agent_state WHERE name = 'coder'")
        assert rows[0]["status"] == "running"
        db.close()

    def test_update_agent_status_with_iterations(self, tmp_path):
        db = SwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.register_agent("coder")
        db.update_agent_status("coder", AgentStatus.DONE, iterations=5)
        rows = db.query("SELECT * FROM agent_state WHERE name = 'coder'")
        assert rows[0]["iterations"] == 5
        db.close()


class TestSwarmDBQueries:
    def test_get_tasks(self, tmp_path):
        db = SwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.execute("INSERT INTO tasks (title, description, status) VALUES ('T1', 'd1', 'pending')")
        db.execute("INSERT INTO tasks (title, description, status) VALUES ('T2', 'd2', 'done')")
        tasks = db.get_tasks()
        assert len(tasks) == 2
        db.close()

    def test_get_artifacts(self, tmp_path):
        db = SwarmDB(str(tmp_path / "test.db"))
        db.init_schema()
        db.execute("INSERT INTO artifacts (agent, name, path) VALUES ('coder', 'main.py', '/shared/main.py')")
        artifacts = db.get_artifacts()
        assert len(artifacts) == 1
        assert artifacts[0]["agent"] == "coder"
        db.close()

    def test_query_rejects_non_select(self, tmp_path):
        db = SwarmDB(str(tmp_path / "test.db"))
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

    def test_setup_deploys_cli_script(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws")},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        script_path = os.path.join(phase._workspace, "swarmdb")
        assert os.path.isfile(script_path)
        assert os.access(script_path, os.X_OK)
        phase._db.close()

    def test_cli_script_query(self, tmp_path):
        """The deployed CLI script can query the DB via subprocess."""
        import subprocess

        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws")},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        # Insert a task via the DB directly
        phase._db.execute("INSERT INTO tasks (title, description, status) VALUES ('T1', 'desc', 'pending')")
        # Query via the CLI script
        script = os.path.join(phase._workspace, "swarmdb")
        db_path = os.path.join(phase._workspace, "swarm.db")
        result = subprocess.run(
            [script, "query", "SELECT * FROM tasks"],
            capture_output=True,
            text=True,
            env={**os.environ, "SWARMDB_PATH": db_path},
        )
        assert result.returncode == 0
        import json

        rows = json.loads(result.stdout)
        assert len(rows) == 1
        assert rows[0]["title"] == "T1"
        phase._db.close()

    def test_cli_script_execute(self, tmp_path):
        """The deployed CLI script can write to the DB via subprocess."""
        import subprocess

        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws")},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        script = os.path.join(phase._workspace, "swarmdb")
        db_path = os.path.join(phase._workspace, "swarm.db")
        result = subprocess.run(
            [script, "execute", "INSERT INTO tasks (title, description, status) VALUES ('T2', 'test', 'pending')"],
            capture_output=True,
            text=True,
            env={**os.environ, "SWARMDB_PATH": db_path},
        )
        assert result.returncode == 0
        assert "1 row(s) affected" in result.stdout
        # Verify via direct DB query
        rows = phase._db.get_tasks()
        assert len(rows) == 1
        assert rows[0]["title"] == "T2"
        phase._db.close()

    def test_cli_script_rejects_protected_table(self, tmp_path):
        """The deployed CLI script rejects writes to protected tables."""
        import subprocess

        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws")},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        script = os.path.join(phase._workspace, "swarmdb")
        db_path = os.path.join(phase._workspace, "swarm.db")
        result = subprocess.run(
            [script, "execute", "UPDATE swarm_state SET state='teardown'"],
            capture_output=True,
            text=True,
            env={**os.environ, "SWARMDB_PATH": db_path},
        )
        assert result.returncode != 0
        assert "cannot write to" in result.stderr
        phase._db.close()


# ---------------------------------------------------------------------------
# 7d: State machine transitions
# ---------------------------------------------------------------------------


class TestSwarmDBStateTransitions:
    def test_full_state_flow(self, tmp_path):
        db = SwarmDB(str(tmp_path / "test.db"))
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
        db = SwarmDB(str(tmp_path / "test.db"))
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
    def test_teardown_collects_results(self, tmp_path):
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

        result = phase._teardown()
        assert result["success"] is True
        assert len(result["tasks"]) == 1
        assert len(result["artifacts"]) == 1

    def test_teardown_reports_failure_when_tasks_incomplete(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws"), "cleanup": "never"},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, description, status) VALUES ('T1', 'd1', 'pending')")

        result = phase._teardown()
        assert result["success"] is False

    def test_cleanup_on_success_deletes_workspace(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws"), "cleanup": "on_success"},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, description, status) VALUES ('T1', 'd1', 'done')")
        result = phase._teardown()
        assert result["success"] is True
        assert not os.path.exists(phase._workspace)

    def test_cleanup_on_success_preserves_on_failure(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws"), "cleanup": "on_success"},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, description, status) VALUES ('T1', 'd1', 'failed')")
        result = phase._teardown()
        assert result["success"] is False
        assert os.path.exists(phase._workspace)

    def test_cleanup_never_preserves_workspace(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws"), "cleanup": "never"},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, description, status) VALUES ('T1', 'd1', 'done')")
        phase._teardown()
        assert os.path.exists(phase._workspace)

    def test_cleanup_always_deletes_on_failure(self, tmp_path):
        raw = {
            **MINIMAL_SWARM_RAW,
            "swarm": {**MINIMAL_SWARM_RAW["swarm"], "workspace": str(tmp_path / "ws"), "cleanup": "always"},
        }
        bp = _make_blueprint([raw])
        phase = bp.phases[0]
        assert isinstance(phase, SwarmPhase)
        phase._setup({})
        phase._db.execute("INSERT INTO tasks (title, description, status) VALUES ('T1', 'd1', 'failed')")
        phase._teardown()
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
