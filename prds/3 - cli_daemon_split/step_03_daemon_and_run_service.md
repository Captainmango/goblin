# Step 3 — `goblin-daemon` workspace member: run service + FastAPI app on a unix socket

**Goal:** The long-running process itself: a new `goblin-daemon/` workspace member that
imports the goblin runtime once (paying the ~11 s litellm cost a single time), hosts a
durable run registry + `RunService`, and serves the step-2 protocol over a unix domain
socket. Thin routers delegating to `core/` per repo conventions [L16].

**Depends on:** steps 1, 2. **Blocks:** steps 4–6.

---

## 3.1 Scaffold

```
goblin-daemon/
├── pyproject.toml            # name "goblin-daemon"; deps: goblin, goblin-common,
│                             # fastapi, uvicorn[standard], aiosqlite, pydantic
├── README.md
├── goblin_daemon/
│   ├── __init__.py
│   ├── cli.py                # Click group; `serve` command; global logging options [L16]
│   ├── config.py             # frozen dataclass singleton: load_config/get_config/reset_config
│   ├── main.py               # create_app(config) -> FastAPI, lifespan, app.state
│   ├── api/
│   │   ├── deps.py           # service getters; optional token check
│   │   ├── health.py         # GET /healthz (public)
│   │   ├── blueprints.py     # POST /v1/blueprints/validate
│   │   └── runs.py           # /v1/runs + resume/cancel (events router added in step 5)
│   └── core/
│       ├── store.py          # RunRegistry (aiosqlite, _migrate())
│       ├── runs.py           # RunService
│       └── runner_host.py    # BlueprintRunner construction/execution helpers
└── tests/
    ├── conftest.py           # create_app(config with tmp_path) + ASGITransport client
    ├── test_store.py
    ├── test_runs_api.py
    └── test_run_service.py
```

Config (env-driven via `goblin_common.config.env`/`secret_env` [L9, L16]):

| Variable | Default | Purpose |
|---|---|---|
| `GOBLIN_DAEMON_SOCKET` | `<project>/.goblin/daemon.sock` | UDS path |
| `GOBLIN_DAEMON_DB_PATH` | `<project>/.goblin/daemon.db` | Run registry sqlite |
| `GOBLIN_DAEMON_PID_FILE` | `<project>/.goblin/daemon.pid` | Lifecycle |
| `GOBLIN_DAEMON_MAX_CONCURRENT_RUNS` | `2` | Run semaphore |
| `GOBLIN_DAEMON_IDLE_TIMEOUT` | `1800` (s) | Exit after this long with no active runs/requests |
| `GOBLIN_DAEMON_TOKEN` | unset (local trust) | Shared-secret header token; **required** in remote mode (step 7) |
| `GOBLIN_DAEMON_HOST` / `GOBLIN_DAEMON_PORT` | unset | TCP bind = remote mode (step 7); unset = UDS |

`goblin-daemon serve --project-root <path>`: chdirs to the project root **before any
config/logging/dotenv initialization**, so `load_dotenv_from_cwd()`, the config
singleton, logging root discovery, and every cwd-default in the runtime behave exactly as
a CLI invoked in that directory [L9]. Then: `load_config()`, `configure_logging()` —
**once**, never per request. The ~11 s SDK import happens lazily on first run submission
(first `runner_host` use), keeping `serve` startup fast enough for the client's 20 s
health budget; alternatively warm it in the lifespan — decide via measurement in the
spike, default to warming in lifespan so the first `run` is also fast.

Wire-in: root `pyproject.toml` workspace members; root `Taskfile.yml`
`goblin-daemon:lint/test/typecheck` tasks; `infra/docker/base/Dockerfile` dep-layer copy;
decide the `fastapi` dep's home (move from `goblin` to `goblin-daemon` if nothing else in
`goblin` uses it — verify by grep first [L19]).

## 3.2 RunRegistry — `core/store.py`

aiosqlite store, `_migrate()`, `aiosqlite.Row` dict rows, WAL, `0o600` file perms [L16]:

```sql
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    blueprint_name TEXT,
    blueprint_source TEXT NOT NULL,        -- 'path' | 'inline'
    blueprint_path TEXT NOT NULL,          -- original path, or materialized path for inline
    context_json TEXT NOT NULL,
    status TEXT NOT NULL,                  -- pending|in_progress|completed|failed|interrupted
    run_dir TEXT NOT NULL,
    checkpoint_path TEXT,
    error TEXT,
    idempotency_key TEXT UNIQUE,           -- NULL allowed; dedupes client retries
    created_at TEXT NOT NULL,              -- ISO 8601 UTC
    started_at TEXT, completed_at TEXT
);
```

Typed methods only (`insert_run`, `set_status`, `attach_checkpoint`, `get_run`,
`list_runs`, `find_by_idempotency_key`). Reconciliation on startup: rows in
`in_progress`/`pending` with no live task (daemon died) → `interrupted` with
`error: "daemon restarted"` — checkpoints remain resumable [L4].

## 3.3 RunService — `core/runs.py`

- **Submit** (`submit(req: SubmitRunRequest, idempotency_key: str | None) -> RunRecord`):
  - `blueprint_path` submissions: must exist and be a file; used as-is so `imports:` and
    disk resume keep working [L3]. `run_dir` defaults to the blueprint's parent.
  - `blueprint_yaml` submissions: validate early
    (`validate_blueprint("<inline>", raw=yaml.safe_load(...))` [L10]); **reject `imports:`**
    with a 422 explaining file-path imports can't resolve for inline submissions; create
    run dir `<project>/.goblin/daemon-runs/<run_id>/`, materialize `blueprint.yaml` there
    (atomic write), `run_dir` = request value or that dir.
  - Idempotency: a repeated `Idempotency-Key` returns the existing record (client retries
    are safe).
  - Insert row (`pending`), enqueue, return 202-shaped record.
- **Execute:** worker tasks bounded by `asyncio.Semaphore(config.max_concurrent_runs)`;
  per run: `runner = BlueprintRunner(path, run_dir=run_dir)`; store `runner.run_id` (step
  1.3.1 — the runner's id **is** the registry id) and checkpoint path; `await
  runner.run(context)` in a named `asyncio.Task` kept in an in-memory `dict[str, Task]`;
  final status from checkpoint + exception mapping (`LimitExceeded`/validation → `failed`
  with message; `CancelledError` → `interrupted` via step 1.3.2).
- **Cancel:** `task.cancel()`; unknown or terminal → domain error → 409.
- **Resume:** by run_id → stored `checkpoint_path` → `BlueprintRunner.resume(...)` [L3];
  also accept a bare checkpoint path (the `--resume` CLI compat path): locate-or-import
  the registry row by checkpoint path, else create one.
- **Normalization:** registry rows + checkpoint JSON merge into the `RunRecord` wire model
  with `phases` normalized per `PhaseRecord` (spend dicts, ISO timestamps — no repr
  strings; step 1.3.3 makes the source data clean) [L4].
- **Concurrency posture:** document and assert via test that runs never share
  `HookRegistry` writes (step 1.3.4); config/logging initialized once at lifespan.

## 3.4 HTTP layer — `api/`

Thin routers, `HTTPException` boundaries, pydantic in/out, `app.state` services [L16]:

- `GET /healthz` → `HealthResponse` (public; includes `protocol_version`).
- `POST /v1/blueprints/validate` → parse YAML (400 on syntax), `validate_blueprint`
  (inline `raw=` path [L10]), return findings. This endpoint forces the SDK import (hook
  registry) on first call — fine, it's daemon-side.
- `POST /v1/runs` (202), `GET /v1/runs`, `GET /v1/runs/{id}` (404 unknown),
  `POST /v1/runs/{id}/resume` (202/409), `POST /v1/runs/{id}/cancel` (200/409).
- Token check dependency: if `GOBLIN_DAEMON_TOKEN` set, require
  `Authorization: Bearer <token>` on all non-health routes [L16].
- Socket setup: parent dir `0700`, socket `0600`, stale socket unlinked on bind, pid file
  written with `0o600` and removed on clean shutdown (lifespan exit).

## 3.5 Idle shutdown + lifecycle

- Track last-activity (requests + active runs); a watchdog task exits the server when
  idle beyond `GOBLIN_DAEMON_IDLE_TIMEOUT` (default 30 min) with **zero** active runs —
  never kill a run. Log `daemon.idle_shutdown` [L16].
- `SIGTERM`/`SIGINT`: stop accepting, cancel nothing — active runs are **detached-safe**:
  registry reconciliation (§3.2) marks them `interrupted` on next start, checkpoints stay
  resumable. (Graceful run-drain is a step-7 refinement; document the semantics now.)

## 3.6 Tests

- Store CRUD + migration + startup reconciliation on `tmp_path`.
- RunService with a **fake runner** mocked at the `BlueprintRunner` boundary per repo
  testing rules [L16]: submit → pending → in_progress → completed; semaphore=1 queues the
  second run; cancel → `interrupted`; resume enqueues with stored checkpoint; idempotency
  key dedupes.
- API tests via `httpx.ASGITransport`: every endpoint's status codes, token enforcement,
  inline-`imports:` 422, unknown-run 404s.
- One real (cheap, mocked-LLM at provider boundary) single-prompt blueprint end-to-end
  in-process: submit → completed, checkpoint present, `RunRecord.phases` normalized.
- One **live-socket** test: uvicorn on a tmp-path UDS + step-2 `DaemonClient` — the two
  sides of the contract meet for the first time.

## Acceptance criteria

- [ ] `task goblin-daemon:lint goblin-daemon:typecheck goblin-daemon:test` green; root
      `task lint typecheck test` green.
- [ ] `uv run goblin-daemon serve --project-root <tmp>` boots; `curl --unix-socket
      .../daemon.sock http://localhost/healthz` returns the protocol version; socket perms
      `0600`.
- [ ] A blueprint submitted over the socket runs to completion with the daemon doing all
      heavy imports; measured daemon-start-to-first-run and warm-submit latencies recorded
      in this file.
- [ ] Idle shutdown and stale-socket/pid cleanup verified by test.
