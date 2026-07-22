"""Tests for the Turso/libSQL SwarmDB backend and factory routing."""

from __future__ import annotations

import sqlite3
from unittest import mock

import pytest

from goblin.enums import AgentStatus, SwarmState
from goblin.swarm_db import SQLiteSwarmDB, SwarmDB, TursoSwarmDB

# ---------------------------------------------------------------------------
# Local-mode parity with SQLiteSwarmDB
# ---------------------------------------------------------------------------


class TestTursoSwarmDBLocal:
    def test_init_schema_creates_tables(self, tmp_path):
        db = TursoSwarmDB(mode="local", url=str(tmp_path / "test.db"))
        db.init_schema()
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        db.close()
        conn.close()
        assert tables >= {"tasks", "artifacts", "signals", "swarm_state", "agent_state"}

    def test_query_returns_dicts(self, tmp_path):
        db = TursoSwarmDB(mode="local", url=str(tmp_path / "test.db"))
        db.init_schema()
        db.execute("INSERT INTO tasks (title, description, status) VALUES ('Task 1', 'desc', 'pending')")
        rows = db.query("SELECT * FROM tasks")
        assert len(rows) == 1
        assert rows[0]["title"] == "Task 1"
        assert isinstance(rows[0], dict)
        db.close()

    def test_execute_validates_table(self, tmp_path):
        db = TursoSwarmDB(mode="local", url=str(tmp_path / "test.db"))
        db.init_schema()
        with pytest.raises(ValueError, match="cannot write to"):
            db.execute("INSERT INTO swarm_state (id, state, round) VALUES (2, 'setup', 0)")
        db.close()

    def test_execute_validates_statement_type(self, tmp_path):
        db = TursoSwarmDB(mode="local", url=str(tmp_path / "test.db"))
        db.init_schema()
        with pytest.raises(ValueError, match="only accepts INSERT, UPDATE, or DELETE"):
            db.execute("DROP TABLE tasks")
        db.close()

    def test_get_state_returns_initial(self, tmp_path):
        db = TursoSwarmDB(mode="local", url=str(tmp_path / "test.db"))
        db.init_schema()
        assert db.get_state() == SwarmState.SETUP
        db.close()

    def test_transition_updates_state(self, tmp_path):
        db = TursoSwarmDB(mode="local", url=str(tmp_path / "test.db"))
        db.init_schema()
        db.transition(SwarmState.COORDINATOR)
        assert db.get_state() == SwarmState.COORDINATOR
        db.close()

    def test_increment_round(self, tmp_path):
        db = TursoSwarmDB(mode="local", url=str(tmp_path / "test.db"))
        db.init_schema()
        assert db.increment_round() == 1
        assert db.increment_round() == 2
        assert db.increment_round() == 3
        db.close()

    def test_update_agent_status(self, tmp_path):
        db = TursoSwarmDB(mode="local", url=str(tmp_path / "test.db"))
        db.init_schema()
        db.register_agent("coder")
        db.update_agent_status("coder", AgentStatus.RUNNING)
        rows = db.query("SELECT * FROM agent_state WHERE name = 'coder'")
        assert rows[0]["status"] == "running"
        db.close()

    def test_update_agent_status_with_iterations(self, tmp_path):
        db = TursoSwarmDB(mode="local", url=str(tmp_path / "test.db"))
        db.init_schema()
        db.register_agent("coder")
        db.update_agent_status("coder", AgentStatus.DONE, iterations=5)
        rows = db.query("SELECT * FROM agent_state WHERE name = 'coder'")
        assert rows[0]["iterations"] == 5
        db.close()

    def test_get_tasks(self, tmp_path):
        db = TursoSwarmDB(mode="local", url=str(tmp_path / "test.db"))
        db.init_schema()
        db.execute("INSERT INTO tasks (title, description, status) VALUES ('T1', 'd1', 'pending')")
        db.execute("INSERT INTO tasks (title, description, status) VALUES ('T2', 'd2', 'done')")
        tasks = db.get_tasks()
        assert len(tasks) == 2
        db.close()

    def test_get_pending_tasks(self, tmp_path):
        db = TursoSwarmDB(mode="local", url=str(tmp_path / "test.db"))
        db.init_schema()
        db.execute("INSERT INTO tasks (title, status) VALUES ('T1', 'pending')")
        db.execute("INSERT INTO tasks (title, status) VALUES ('T2', 'done')")
        db.execute("INSERT INTO tasks (title, status) VALUES ('T3', 'pending')")
        pending = db.get_pending_tasks()
        assert len(pending) == 2
        assert {t["title"] for t in pending} == {"T1", "T3"}
        db.close()

    def test_get_artifacts(self, tmp_path):
        db = TursoSwarmDB(mode="local", url=str(tmp_path / "test.db"))
        db.init_schema()
        db.execute("INSERT INTO artifacts (agent, name, path) VALUES ('coder', 'main.py', '/shared/main.py')")
        artifacts = db.get_artifacts()
        assert len(artifacts) == 1
        assert artifacts[0]["agent"] == "coder"
        db.close()

    def test_create_task_inserts_pending_row(self, tmp_path):
        db = TursoSwarmDB(mode="local", url=str(tmp_path / "test.db"))
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
        db = TursoSwarmDB(mode="local", url=str(tmp_path / "test.db"))
        db.init_schema()
        first = db.create_task("T1")
        second = db.create_task("T2")
        assert second > first
        db.close()

    def test_has_outstanding_tasks(self, tmp_path):
        db = TursoSwarmDB(mode="local", url=str(tmp_path / "test.db"))
        db.init_schema()
        assert db.has_outstanding_tasks() is False
        db.execute("INSERT INTO tasks (title, status) VALUES ('T1', 'pending')")
        assert db.has_outstanding_tasks() is True
        db.execute("UPDATE tasks SET status = 'done'")
        assert db.has_outstanding_tasks() is False
        db.close()

    def test_memory_mode(self):
        db = TursoSwarmDB(mode="local", url=":memory:")
        db.init_schema()
        db.create_task("In-memory task")
        assert len(db.get_tasks()) == 1
        db.close()


class TestTursoSwarmDBRemote:
    def test_remote_mode_requires_auth_token(self):
        with pytest.raises(ValueError, match="auth_token is required"):
            TursoSwarmDB(mode="remote", url="libsql://my-db.turso.io")

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid Turso mode"):
            TursoSwarmDB(mode="invalid", url=":memory:")

    def test_remote_mode_calls_sync_connect(self, tmp_path):
        mock_turso = mock.Mock()
        with mock.patch("goblin.swarm_db.turso", mock_turso):
            db = TursoSwarmDB(
                mode="remote",
                url="libsql://my-db.turso.io",
                auth_token="my-token",
                local_path=str(tmp_path / "replica.db"),
            )
            mock_turso.sync.connect.assert_called_once_with(
                str(tmp_path / "replica.db"),
                remote_url="libsql://my-db.turso.io",
                auth_token="my-token",
            )
            db.close()

    def test_remote_mode_defaults_local_path_to_url(self):
        mock_turso = mock.Mock()
        with mock.patch("goblin.swarm_db.turso", mock_turso):
            db = TursoSwarmDB(
                mode="remote",
                url="/tmp/replica.db",
                auth_token="my-token",
            )
            mock_turso.sync.connect.assert_called_once_with(
                "/tmp/replica.db",
                remote_url="/tmp/replica.db",
                auth_token="my-token",
            )
            db.close()


# ---------------------------------------------------------------------------
# SwarmDB.from_config routing
# ---------------------------------------------------------------------------


class TestSwarmDBFromConfig:
    def test_defaults_to_turso_local(self):
        db = SwarmDB.from_config({"db_path": "/tmp/swarm.db"})
        assert isinstance(db._backend, TursoSwarmDB)
        assert db._backend.mode == "local"
        assert db._backend.url == "/tmp/swarm.db"
        db.close()

    def test_sqlite_opt_in_via_backend(self):
        db = SwarmDB.from_config({"db_path": "/tmp/swarm.db", "backend": "sqlite"})
        assert isinstance(db._backend, SQLiteSwarmDB)
        db.close()

    def test_sqlite_opt_in_via_env(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_BACKEND", "sqlite")
        db = SwarmDB.from_config({"db_path": "/tmp/swarm.db"})
        assert isinstance(db._backend, SQLiteSwarmDB)
        db.close()

    def test_sqlite_opt_in_via_flag(self):
        db = SwarmDB.from_config({"db_path": "/tmp/swarm.db", "sqlite": True})
        assert isinstance(db._backend, SQLiteSwarmDB)
        db.close()

    def test_turso_block_dict(self):
        db = SwarmDB.from_config(
            {
                "db_path": "/tmp/swarm.db",
                "turso": {"mode": "local", "url": ":memory:"},
            }
        )
        assert isinstance(db._backend, TursoSwarmDB)
        assert db._backend.mode == "local"
        assert db._backend.url == ":memory:"
        db.close()

    def test_turso_block_remote(self):
        mock_turso = mock.Mock()
        with mock.patch("goblin.swarm_db.turso", mock_turso):
            db = SwarmDB.from_config(
                {
                    "db_path": "/tmp/swarm.db",
                    "turso": {
                        "mode": "remote",
                        "url": "libsql://my-db.turso.io",
                        "auth_token": "token",
                        "local_path": "/tmp/replica.db",
                    },
                }
            )
            assert isinstance(db._backend, TursoSwarmDB)
            assert db._backend.mode == "remote"
            mock_turso.sync.connect.assert_called_once_with(
                "/tmp/replica.db",
                remote_url="libsql://my-db.turso.io",
                auth_token="token",
            )
            db.close()

    def test_turso_url_top_level_local(self):
        db = SwarmDB.from_config({"db_path": "/tmp/swarm.db", "turso_url": ":memory:"})
        assert isinstance(db._backend, TursoSwarmDB)
        assert db._backend.mode == "local"
        db.close()

    def test_turso_url_top_level_remote(self):
        mock_turso = mock.Mock()
        with mock.patch("goblin.swarm_db.turso", mock_turso):
            db = SwarmDB.from_config(
                {
                    "db_path": "/tmp/swarm.db",
                    "turso_url": "libsql://my-db.turso.io",
                    "turso_auth_token": "token",
                }
            )
            assert isinstance(db._backend, TursoSwarmDB)
            assert db._backend.mode == "remote"
            db.close()

    def test_env_vars_turso_url(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_TURSO_URL", ":memory:")
        db = SwarmDB.from_config({"db_path": "/tmp/swarm.db"})
        assert isinstance(db._backend, TursoSwarmDB)
        assert db._backend.url == ":memory:"
        db.close()

    def test_env_vars_turso_remote(self, monkeypatch):
        monkeypatch.setenv("GOBLIN_TURSO_URL", "libsql://my-db.turso.io")
        monkeypatch.setenv("GOBLIN_TURSO_AUTH_TOKEN", "token")
        monkeypatch.setenv("GOBLIN_TURSO_LOCAL_PATH", "/tmp/replica.db")
        mock_turso = mock.Mock()
        with mock.patch("goblin.swarm_db.turso", mock_turso):
            db = SwarmDB.from_config({"db_path": "/tmp/swarm.db"})
            assert isinstance(db._backend, TursoSwarmDB)
            assert db._backend.mode == "remote"
            mock_turso.sync.connect.assert_called_once_with(
                "/tmp/replica.db",
                remote_url="libsql://my-db.turso.io",
                auth_token="token",
            )
            db.close()


# ---------------------------------------------------------------------------
# Init schema on the wrapper
# ---------------------------------------------------------------------------


class TestSwarmDBInitSchema:
    def test_init_schema_works_for_turso(self):
        db = SwarmDB.from_config({"db_path": ":memory:"})
        db.init_schema()
        assert len(db.query("SELECT name FROM sqlite_master WHERE type='table'")) >= 5
        db.close()

    def test_init_schema_works_for_sqlite(self, tmp_path):
        db = SwarmDB.from_config({"db_path": str(tmp_path / "test.db"), "backend": "sqlite"})
        db.init_schema()
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        db.close()
        assert tables >= {"tasks", "artifacts", "signals", "swarm_state", "agent_state"}
