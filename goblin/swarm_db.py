from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Protocol

from goblin.enums import AgentStatus, SwarmDBTable, SwarmState, TaskStatus

_ALLOWED_WRITE_TABLES = {t.value for t in SwarmDBTable}
_WRITE_RE = re.compile(r"^\s*(INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
_TABLE_RE = re.compile(
    r"(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(\w+)",
    re.IGNORECASE,
)


def get_write_validation_rules() -> tuple[frozenset[str], str, str]:
    """Return the write-validation rules as plain data (a table-name set and
    two regex pattern strings) so callers outside this module — notably any
    generated helper scripts — can embed them without depending on this module
    being importable at the point of use.
    """
    return frozenset(_ALLOWED_WRITE_TABLES), _WRITE_RE.pattern, _TABLE_RE.pattern


class SwarmDBBackend(Protocol):
    """Async-capable backend for SwarmDB operations.

    Implementations hide whether the underlying store is local SQLite or a
    remote libSQL/Turso database. Callers should go through `SwarmDB`, which
    delegates every method to the configured backend.
    """

    def query(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict]: ...
    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> int: ...
    def create_task(
        self,
        title: str,
        description: str | None = None,
        assigned_to: str | None = None,
        required_role: str | None = None,
    ) -> int: ...
    def get_tasks(self) -> list[dict]: ...
    def get_pending_tasks(self) -> list[dict]: ...
    def get_artifacts(self) -> list[dict]: ...
    def get_signals(self, to_agent: str | None = None) -> list[dict]: ...
    def has_outstanding_tasks(self) -> bool: ...
    def get_state(self) -> SwarmState: ...
    def transition(self, new_state: SwarmState) -> None: ...
    def increment_round(self) -> int: ...
    def update_agent_status(self, name: str, status: AgentStatus, iterations: int | None = None) -> None: ...
    def register_agent(self, name: str, roles: list[str] | None = None) -> None: ...
    def close(self) -> None: ...


class SQLiteSwarmDB:
    """Local SQLite implementation of the SwarmDB backend."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        # check_same_thread=False: the OpenHands SDK dispatches tool calls
        # (e.g. the swarm_tasks tool's executor) from a worker thread pool,
        # and successive calls to the same tool can land on different
        # threads even though the SDK already serializes them with a
        # tool-wide mutex (see ToolDefinition.declared_resources). SQLite's
        # same-thread check is about thread identity, not concurrency, so
        # this is safe given that existing serialization.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")

    def init_schema(self):
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT,
                assigned_to TEXT,
                required_role TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                result TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER,
                agent TEXT,
                name TEXT,
                path TEXT,
                content TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_agent TEXT,
                to_agent TEXT,
                type TEXT,
                payload TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS swarm_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state TEXT NOT NULL DEFAULT 'setup',
                round INTEGER NOT NULL DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS agent_state (
                name TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                roles TEXT,
                iterations INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            INSERT OR IGNORE INTO swarm_state (id, state, round) VALUES (1, 'setup', 0);
            """
        )
        self._conn.commit()

    def query(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict]:
        stripped = sql.strip()
        if not stripped.upper().startswith("SELECT"):
            raise ValueError("query() only accepts SELECT statements")
        cursor = self._conn.execute(stripped, params or ())
        return [dict(row) for row in cursor.fetchall()]

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> int:
        stripped = sql.strip()
        if not _WRITE_RE.match(stripped):
            raise ValueError("execute() only accepts INSERT, UPDATE, or DELETE statements")
        match = _TABLE_RE.search(stripped)
        if not match:
            raise ValueError("Could not determine target table from SQL statement")
        table = match.group(1).lower()
        if table not in _ALLOWED_WRITE_TABLES:
            raise ValueError(f"Agents cannot write to '{table}'. Allowed tables: {sorted(_ALLOWED_WRITE_TABLES)}")
        cursor = self._conn.execute(stripped, params or ())
        self._conn.commit()
        return cursor.rowcount

    def get_state(self) -> SwarmState:
        cursor = self._conn.execute("SELECT state FROM swarm_state WHERE id = 1")
        row = cursor.fetchone()
        return SwarmState(row["state"])

    def transition(self, new_state: SwarmState):
        self._conn.execute(
            "UPDATE swarm_state SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
            (new_state.value,),
        )
        self._conn.commit()

    def increment_round(self) -> int:
        self._conn.execute(
            "UPDATE swarm_state SET round = round + 1, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
        )
        self._conn.commit()
        cursor = self._conn.execute("SELECT round FROM swarm_state WHERE id = 1")
        row = cursor.fetchone()
        return row["round"]

    def update_agent_status(self, name: str, status: AgentStatus, iterations: int | None = None):
        if iterations is not None:
            self._conn.execute(
                "UPDATE agent_state SET status = ?, iterations = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                (status.value, iterations, name),
            )
        else:
            self._conn.execute(
                "UPDATE agent_state SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE name = ?",
                (status.value, name),
            )
        self._conn.commit()

    def register_agent(self, name: str, roles: list[str] | None = None):
        self._conn.execute(
            "INSERT OR IGNORE INTO agent_state (name, status, roles) VALUES (?, ?, ?)",
            (name, AgentStatus.PENDING.value, json.dumps(roles) if roles else None),
        )
        self._conn.commit()

    def create_task(
        self,
        title: str,
        description: str | None = None,
        assigned_to: str | None = None,
        required_role: str | None = None,
    ) -> int:
        """INSERT a new pending task; returns its id.

        This is the only write path the coordinator is given (via the
        swarm_tasks tool) -- unlike execute(), there is no way to reach
        UPDATE/DELETE through this method, so the coordinator cannot mark
        its own tasks done.
        """
        cursor = self._conn.execute(
            "INSERT INTO tasks (title, description, assigned_to, required_role, status) VALUES (?, ?, ?, ?, ?)",
            (title, description, assigned_to, required_role, TaskStatus.PENDING.value),
        )
        self._conn.commit()
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def get_tasks(self) -> list[dict]:
        cursor = self._conn.execute("SELECT * FROM tasks")
        return [dict(row) for row in cursor.fetchall()]

    def get_pending_tasks(self) -> list[dict]:
        cursor = self._conn.execute("SELECT * FROM tasks WHERE status = 'pending'")
        return [dict(row) for row in cursor.fetchall()]

    def has_outstanding_tasks(self) -> bool:
        """Whether any task is still pending or in_progress.

        Used by SwarmPhase.run() to independently confirm a coordinator's
        `done: true` claim instead of trusting it outright -- the coordinator
        has no UPDATE capability on this table, so this check cannot be forged.
        """
        cursor = self._conn.execute(
            "SELECT 1 FROM tasks WHERE status IN (?, ?) LIMIT 1",
            (TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value),
        )
        return cursor.fetchone() is not None

    def get_artifacts(self) -> list[dict]:
        cursor = self._conn.execute("SELECT * FROM artifacts")
        return [dict(row) for row in cursor.fetchall()]

    def get_signals(self, to_agent: str | None = None) -> list[dict]:
        if to_agent is not None:
            cursor = self._conn.execute(
                "SELECT * FROM signals WHERE to_agent = ? OR to_agent IS NULL",
                (to_agent,),
            )
        else:
            cursor = self._conn.execute("SELECT * FROM signals")
        return [dict(row) for row in cursor.fetchall()]

    def close(self):
        self._conn.close()


class TursoSwarmDB:
    """Placeholder backend for remote Turso/libSQL databases.

    This stub exists so the `SwarmDB.from_config` factory can wire up a Turso
    backend once credentials and schema are ready; the actual libSQL client
    implementation belongs to Phase 5.
    """

    def __init__(self, url: str, auth_token: str | None = None):
        self.url = url
        self.auth_token = auth_token

    def query(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict]:
        raise NotImplementedError("TursoSwarmDB is not implemented yet")

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> int:
        raise NotImplementedError("TursoSwarmDB is not implemented yet")

    def create_task(
        self,
        title: str,
        description: str | None = None,
        assigned_to: str | None = None,
        required_role: str | None = None,
    ) -> int:
        raise NotImplementedError("TursoSwarmDB is not implemented yet")

    def get_tasks(self) -> list[dict]:
        raise NotImplementedError("TursoSwarmDB is not implemented yet")

    def get_pending_tasks(self) -> list[dict]:
        raise NotImplementedError("TursoSwarmDB is not implemented yet")

    def get_artifacts(self) -> list[dict]:
        raise NotImplementedError("TursoSwarmDB is not implemented yet")

    def get_signals(self, to_agent: str | None = None) -> list[dict]:
        raise NotImplementedError("TursoSwarmDB is not implemented yet")

    def has_outstanding_tasks(self) -> bool:
        raise NotImplementedError("TursoSwarmDB is not implemented yet")

    def get_state(self) -> SwarmState:
        raise NotImplementedError("TursoSwarmDB is not implemented yet")

    def transition(self, new_state: SwarmState) -> None:
        raise NotImplementedError("TursoSwarmDB is not implemented yet")

    def increment_round(self) -> int:
        raise NotImplementedError("TursoSwarmDB is not implemented yet")

    def update_agent_status(self, name: str, status: AgentStatus, iterations: int | None = None) -> None:
        raise NotImplementedError("TursoSwarmDB is not implemented yet")

    def register_agent(self, name: str, roles: list[str] | None = None) -> None:
        raise NotImplementedError("TursoSwarmDB is not implemented yet")

    def close(self) -> None:
        pass


class SwarmDB:
    """Thin factory wrapper around a SwarmDBBackend implementation.

    This wrapper lets callers use the same `SwarmDB` facade regardless of
    whether the underlying store is local SQLite or remote Turso. All public
    methods are delegated directly to the configured backend.
    """

    def __init__(self, backend: SwarmDBBackend):
        self._backend = backend

    @classmethod
    def from_config(cls, config: dict) -> SwarmDB:
        """Factory that picks SQLite or Turso based on config."""
        if config.get("turso_url"):
            backend: SwarmDBBackend = TursoSwarmDB(
                config["turso_url"],
                config.get("turso_auth_token"),
            )
        elif config.get("turso"):
            # Backward-compatible shape from the original migration plan.
            turso_cfg = config["turso"]
            if isinstance(turso_cfg, dict):
                backend = TursoSwarmDB(turso_cfg["url"], turso_cfg.get("auth_token"))
            else:
                backend = TursoSwarmDB(config["url"], config.get("auth_token"))
        else:
            backend = SQLiteSwarmDB(config["db_path"])
        return cls(backend)

    def init_schema(self):
        """Initialise schema on the backend.

        SQLite needs local table creation; remote Turso schema is managed
        server-side, so this is a no-op for TursoSwarmDB.
        """
        if isinstance(self._backend, SQLiteSwarmDB):
            self._backend.init_schema()

    def query(self, sql: str, params: tuple[Any, ...] | None = None) -> list[dict]:
        return self._backend.query(sql, params)

    def execute(self, sql: str, params: tuple[Any, ...] | None = None) -> int:
        return self._backend.execute(sql, params)

    def create_task(
        self,
        title: str,
        description: str | None = None,
        assigned_to: str | None = None,
        required_role: str | None = None,
    ) -> int:
        return self._backend.create_task(title, description, assigned_to, required_role)

    def get_tasks(self) -> list[dict]:
        return self._backend.get_tasks()

    def get_pending_tasks(self) -> list[dict]:
        return self._backend.get_pending_tasks()

    def get_artifacts(self) -> list[dict]:
        return self._backend.get_artifacts()

    def get_signals(self, to_agent: str | None = None) -> list[dict]:
        return self._backend.get_signals(to_agent)

    def has_outstanding_tasks(self) -> bool:
        return self._backend.has_outstanding_tasks()

    def get_state(self) -> SwarmState:
        return self._backend.get_state()

    def transition(self, new_state: SwarmState):
        self._backend.transition(new_state)

    def increment_round(self) -> int:
        return self._backend.increment_round()

    def update_agent_status(self, name: str, status: AgentStatus, iterations: int | None = None):
        self._backend.update_agent_status(name, status, iterations)

    def register_agent(self, name: str, roles: list[str] | None = None):
        self._backend.register_agent(name, roles)

    def close(self):
        self._backend.close()
