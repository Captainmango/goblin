# Plan: Implement Turso SwarmDB Backend and Swap Over

## Goal

Replace the current `TursoSwarmDB` stub in `goblin/swarm_db.py` with a fully-functional backend built on `pyturso`, and update the wiring so that **Turso is the default SwarmDB backend**. SQLite is retained but becomes an explicit opt-in (`backend: sqlite` or `GOBLIN_BACKEND=sqlite`). Users can still opt into either a **local** Turso/libSQL database or a **remote cloud** Turso database via the `turso` configuration block. The implementation should mirror `SQLiteSwarmDB` as closely as possible because `pyturso` exposes a DB-API 2.0 surface that is nearly identical to `sqlite3`.

## Current State

- `goblin/swarm_db.py` defines:
  - `SwarmDBBackend` protocol.
  - `SQLiteSwarmDB` — complete, local-file implementation.
  - `TursoSwarmDB` — stub that raises `NotImplementedError` for every method.
  - `SwarmDB` — facade with a `from_config()` factory that already routes to `TursoSwarmDB` when `turso_url` or `turso` config keys are present.
- `pyturso>=0.6.1` is already a project dependency.
- `goblin/swarm.py`, `goblin/utils/swarmdb_tool.py`, and `goblin/utils/swarm_agent_tool.py` all build `db_config = {"db_path": ...}` and pass it to tools; none of them currently propagate Turso config.
- The blueprint JSON schema (`spec/v1.json`) has no `turso` block under `swarm`, so users cannot declare a Turso backend from YAML yet.
- `ROADMAP.md` explicitly lists "Swap to Turso" as a near-term goal.
- This plan flips the current default: today SQLite is used unless Turso is explicitly configured; after implementation Turso will be used unless SQLite is explicitly opted into.

## Why This Is a Drop-In Replacement

`pyturso`'s synchronous API is intentionally SQLite-compatible:

- `conn = turso.connect(database_path)` opens a database.
- `conn.row_factory = turso.Row` enables dict-style column access.
- `conn.executescript(sql)`, `conn.execute(sql, params)`, `conn.commit()`, and `conn.close()` work as expected.
- Cursors expose `fetchone()`, `fetchall()`, `lastrowid`, and `rowcount`.
- No `check_same_thread=False` workaround is required.

The only material differences discovered during spiking are minor and handled below.

## Implementation Plan

### 1. Implement `TursoSwarmDB` in `goblin/swarm_db.py`

Translate `SQLiteSwarmDB` method-for-method into `pyturso` calls. Keep the same public signature and the same validation rules (`_ALLOWED_WRITE_TABLES`, `_WRITE_RE`, `_TABLE_RE`).

Specific mapping:

- Constructor: `def __init__(self, url: str, mode: str = "local", auth_token: str | None = None, local_path: str | None = None)`.
  - `mode` must be `"local"` or `"remote"`. Raise `ValueError` for any other value.
  - **Local mode** (`mode="local"`): open with `turso.connect(url)`. `url` may be a file path, `:memory:`, or any identifier accepted by `turso.connect`. `auth_token` and `local_path` are ignored.
  - **Remote mode** (`mode="remote"`): open with `turso.sync.connect(local_path, remote_url=url, auth_token=auth_token)`. `auth_token` is required in this mode. `local_path` defaults to the workspace `swarm.db` path so the on-disk replica is predictable; callers may override it.
  - Auto-detection convenience: if `mode` is omitted and `auth_token` is supplied, default to `"remote"`; otherwise default to `"local"`.
- `init_schema(self)`: reuse the same `executescript()` DDL used by `SQLiteSwarmDB`. For remote Turso the schema is created locally and synced; for local Turso it is created on disk/memory just like SQLite.
- `query()`: keep the `SELECT` guard, execute with `turso.connect`-style cursor, return `[dict(row) for row in cursor.fetchall()]`.
- `execute()`: keep the write-statement and table-name guards, commit, return `cursor.rowcount`.
- `get_state()`, `transition()`, `increment_round()`, `update_agent_status()`, `register_agent()`, `create_task()`, `get_tasks()`, `get_pending_tasks()`, `get_artifacts()`, `get_signals()`, `has_outstanding_tasks()`, `close()`: port each SQLite call to the equivalent `pyturso` call with identical SQL.

Caveat: after `executescript()`, `cursor.lastrowid` may be `None`. The implementation should obtain the inserted ID via a follow-up `SELECT last_insert_rowid()` only when `cursor.lastrowid` is not available, ensuring `create_task()` still returns a stable integer ID.

### 2. Update `SwarmDB.from_config()` Default Behavior

Make Turso the default backend. SQLite is only selected when explicitly opted into.

Priority order:

1. Explicit SQLite opt-in: `config["backend"] == "sqlite"`, `config["sqlite"] is True`, or env var `GOBLIN_BACKEND=sqlite`.
2. `config["turso"]` dict with optional `mode`, optional `url`, optional `auth_token`, and optional `local_path`.
3. `config["turso_url"]` (top-level explicit; implies local mode unless `turso_auth_token` is also provided).
4. Environment variables `GOBLIN_TURSO_URL` / `GOBLIN_TURSO_MODE` / `GOBLIN_TURSO_AUTH_TOKEN` / `GOBLIN_TURSO_LOCAL_PATH`.
5. Default: Turso local mode using `config["db_path"]` as the URL.

When no explicit Turso URL is provided, `SwarmDB.from_config()` defaults to `TursoSwarmDB(mode="local", url=db_path)`, preserving the existing on-disk file location (`<workspace>/swarm.db`).

Change `SwarmDB.init_schema()` so that it initializes schema for `TursoSwarmDB` as well as `SQLiteSwarmDB` (currently it only initializes SQLite).

### 3. Propagate Turso Config Through `SwarmPhase`

`SwarmPhase._setup()` currently builds only `{"db_path": ...}`. Update it to build a `db_config` dict that defaults to Turso local mode:

- Compute the default `db_path = os.path.join(self._workspace, "swarm.db")`.
- Read the swarm-level blueprint config for an optional `turso` block and an optional `backend` key.
- If `backend == "sqlite"` (or `GOBLIN_BACKEND=sqlite`), pass `{"db_path": db_path, "backend": "sqlite"}` to `SwarmDB.from_config()`.
- Otherwise, build a Turso config. If a `turso` block or `GOBLIN_TURSO_*` env vars are present, use those values; if not, default to `{"turso_url": db_path, "turso_mode": "local"}`.
- Pass the resulting dict to `SwarmDB.from_config()`.
- Store this same `db_config` on the phase instance so `_run_coordinator()` and `_run_single_agent()` can pass it to `resolve_tools()` instead of reconstructing `{"db_path": ...}`.

This ensures the coordinator's `SwarmTasks` tool and each agent's `SwarmAgent` tool open connections to the same backend as the orchestrator.

### 4. Update Blueprint JSON Schema (`spec/v1.json`)

Add a `backend` enum and an optional `turso` object under the `swarm` definition:

```json
"backend": {
  "type": "string",
  "enum": ["turso", "sqlite"],
  "description": "Swarm database backend. Defaults to 'turso'. Set to 'sqlite' to opt into the legacy SQLite backend.",
  "examples": ["turso", "sqlite"]
},
"turso": {
  "type": "object",
  "description": "Optional Turso/libSQL backend configuration. When omitted, the swarm defaults to local Turso using the workspace swarm.db file. Use this block to opt into remote cloud mode or to override the local database path.",
  "additionalProperties": false,
  "properties": {
    "mode": {
      "type": "string",
      "enum": ["local", "remote"],
      "description": "Turso operating mode. 'local' uses an on-disk or in-memory libSQL database. 'remote' uses a cloud Turso database with a local replica. Defaults to 'local'.",
      "examples": ["local", "remote"]
    },
    "url": {
      "type": "string",
      "description": "Turso database URL or local libSQL path. In remote mode this is the cloud endpoint. In local mode this is a file path or :memory:. Defaults to the workspace swarm.db path. Supports $VAR env var references and {variable} interpolation.",
      "examples": ["libsql://my-db.turso.io", "${TURSO_URL}", ".goblin/swarms/{phase_id}/swarm.db", ":memory:"]
    },
    "auth_token": {
      "type": "string",
      "description": "Authentication token for remote Turso databases. Required when mode is 'remote'. Supports $VAR env var references.",
      "examples": ["${TURSO_AUTH_TOKEN}"]
    },
    "local_path": {
      "type": "string",
      "description": "Path to the local replica file when mode is 'remote'. Defaults to the swarm workspace's swarm.db. Ignored when mode is 'local'.",
      "examples": [".goblin/swarms/{phase_id}/swarm.db"]
    }
  }
}
```

Also update the `swarm` description to mention that Turso is the default backend and SQLite is opt-in.

### 5. Environment Variable Resolution

Use the existing `goblin.utils.env.resolve_env_vars()` helper to resolve `$VAR` / `${VAR}` references inside the `turso` block before passing values to `SwarmDB.from_config()`. This matches how MCP server config is handled elsewhere in the codebase.

Also resolve the `backend` key from blueprint config or `GOBLIN_BACKEND` env var before selecting the backend implementation.

### 6. Add Tests

Create or extend tests in `tests/test_swarm.py` (or a new `tests/test_turso_swarm_db.py`):

- `TursoSwarmDB` in **local mode** with a file path or `:memory:` behaves identically to `SQLiteSwarmDB` for:
  - schema creation,
  - `query()` returning dicts,
  - `execute()` table validation,
  - state transitions,
  - round incrementing,
  - agent registration/status,
  - task creation and outstanding-task checks.
- `TursoSwarmDB` in **remote mode** rejects missing `auth_token` and calls `turso.sync.connect` with the correct arguments.
- `SwarmDB.from_config()` defaults to `TursoSwarmDB(mode="local")` when no backend is specified.
- `SwarmDB.from_config()` selects `SQLiteSwarmDB` only when `backend: sqlite` or `GOBLIN_BACKEND=sqlite` is set.
- `SwarmPhase._setup()` passes a Turso local-mode `db_config` by default and only falls back to SQLite when opted in.
- `SwarmPhase._setup()` passes the full Turso config (`mode`, `url`, `auth_token`, `local_path`) into `db_config` when a blueprint `turso` block is present.
- Env-var fallback (`GOBLIN_BACKEND`, `GOBLIN_TURSO_URL`, `GOBLIN_TURSO_MODE`, `GOBLIN_TURSO_AUTH_TOKEN`, `GOBLIN_TURSO_LOCAL_PATH`) is honored.

Where possible, parameterize the existing `SQLiteSwarmDB` tests so they run against both backends, ensuring parity.

### 7. Update Tooling and Documentation

- Add a `task test-turso` or extend `task test` in `Taskfile.yml` to run the new backend tests.
- Update `CLAUDE.md` / `README.md` to document that Turso is now the default backend, the `backend: sqlite` opt-in, the `turso` swarm option, the local/remote mode choice, and the `GOBLIN_BACKEND`, `GOBLIN_TURSO_URL`, `GOBLIN_TURSO_MODE`, `GOBLIN_TURSO_AUTH_TOKEN`, and `GOBLIN_TURSO_LOCAL_PATH` env vars.
- Update `ROADMAP.md` to mark "Swap to Turso" as in-progress or complete once implemented.

### 8. Migration / Rollback Strategy

- This is a default-behavior change: new swarms use Turso local mode (`<workspace>/swarm.db`) unless SQLite is explicitly opted into.
- Existing blueprints with no `turso` block continue to use the same `swarm.db` file location, but now opened through the Turso engine instead of SQLite.
- To opt into the legacy SQLite backend, set `backend: sqlite` in the swarm config or `GOBLIN_BACKEND=sqlite`.
- To opt into remote cloud Turso, set `mode: remote`, provide the cloud `url`, and provide an `auth_token`.
- To roll back the default, set `GOBLIN_BACKEND=sqlite` globally or per-blueprint.
- For remote Turso, the local replica file can be kept as a cache; deleting it forces a fresh bootstrap on the next run.
- Because local Turso is file-format-compatible with SQLite, existing `swarm.db` files created by SQLite should continue to work when opened by Turso local mode.

## Acceptance Criteria

- [ ] `TursoSwarmDB` no longer raises `NotImplementedError`; all protocol methods are implemented.
- [ ] `TursoSwarmDB` in local mode passes the same core behavior tests as `SQLiteSwarmDB`.
- [ ] `TursoSwarmDB` in remote mode validates required `auth_token` and wires up `turso.sync.connect` correctly.
- [ ] `SwarmDB.from_config()` defaults to `TursoSwarmDB(mode="local")` and only routes to `SQLiteSwarmDB` when `backend: sqlite` or `GOBLIN_BACKEND=sqlite` is set.
- [ ] `SwarmPhase` propagates Turso config to both coordinator and agent tools.
- [ ] `spec/v1.json` allows an optional `turso` block under `swarm`.
- [ ] `task lint`, `task format`, `task typecheck`, and `task test` all pass.
- [ ] Documentation is updated to describe the Turso option.

## Suggested Order of Work

1. Spike `TursoSwarmDB` implementation locally against `:memory:` and a temp file to confirm parity.
2. Implement `TursoSwarmDB` in `goblin/swarm_db.py` and update `SwarmDB.from_config()` / `init_schema()`.
3. Update `SwarmPhase` to build and propagate `db_config`.
4. Add the `turso` block to `spec/v1.json` and run `task validate`.
5. Write/extend tests for the new backend.
6. Update docs, Taskfile, and ROADMAP.
7. Run full test suite and merge.
