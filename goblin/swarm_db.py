from __future__ import annotations

import re
import sqlite3

from goblin.enums import AgentStatus, SwarmDBTable, SwarmState

_ALLOWED_WRITE_TABLES = {t.value for t in SwarmDBTable}
_WRITE_RE = re.compile(r"^\s*(INSERT|UPDATE|DELETE)\b", re.IGNORECASE)
_TABLE_RE = re.compile(
    r"(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(\w+)",
    re.IGNORECASE,
)


def get_write_validation_rules() -> tuple[frozenset[str], str, str]:
    """Return the write-validation rules as plain data (a table-name set and
    two regex pattern strings) so callers outside this module — notably the
    generated swarmdb CLI script in goblin/swarm.py — can embed them without
    depending on this module being importable at the point of use.
    """
    return frozenset(_ALLOWED_WRITE_TABLES), _WRITE_RE.pattern, _TABLE_RE.pattern


class SwarmDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn = sqlite3.connect(db_path)
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

    def query(self, sql: str) -> list[dict]:
        stripped = sql.strip()
        if not stripped.upper().startswith("SELECT"):
            raise ValueError("query() only accepts SELECT statements")
        cursor = self._conn.execute(stripped)
        return [dict(row) for row in cursor.fetchall()]

    def execute(self, sql: str) -> int:
        stripped = sql.strip()
        if not _WRITE_RE.match(stripped):
            raise ValueError("execute() only accepts INSERT, UPDATE, or DELETE statements")
        match = _TABLE_RE.search(stripped)
        if not match:
            raise ValueError("Could not determine target table from SQL statement")
        table = match.group(1).lower()
        if table not in _ALLOWED_WRITE_TABLES:
            raise ValueError(f"Agents cannot write to '{table}'. Allowed tables: {sorted(_ALLOWED_WRITE_TABLES)}")
        cursor = self._conn.execute(stripped)
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
        import json

        self._conn.execute(
            "INSERT OR IGNORE INTO agent_state (name, status, roles) VALUES (?, ?, ?)",
            (name, AgentStatus.PENDING.value, json.dumps(roles) if roles else None),
        )
        self._conn.commit()

    def get_tasks(self) -> list[dict]:
        cursor = self._conn.execute("SELECT * FROM tasks")
        return [dict(row) for row in cursor.fetchall()]

    def get_pending_tasks(self) -> list[dict]:
        cursor = self._conn.execute("SELECT * FROM tasks WHERE status = 'pending'")
        return [dict(row) for row in cursor.fetchall()]

    def get_artifacts(self) -> list[dict]:
        cursor = self._conn.execute("SELECT * FROM artifacts")
        return [dict(row) for row in cursor.fetchall()]

    def close(self):
        self._conn.close()
