# Step 2 — Run service core (registry, orchestration, cancellation, events)

**Goal:** The server-side engine that runs blueprints: a persistent run registry, background
execution via `BlueprintRunner`, resume/cancel, and a run-event pub/sub. No HTTP yet.

**Depends on:** step 1. **Blocks:** steps 3, 4.

---

## 2.1 Small additive changes to `goblin` (do these first)

1. **Public run id** — `goblin/goblin/runner.py`: add
   ```python
   @property
   def run_id(self) -> str: return self._run_id
   ```
2. **Cancellation → interrupted checkpoint** — `RunStatus.INTERRUPTED` exists but is never
   written [L4]. In `goblin/goblin/runner.py` (and/or `orchestrator.py` where the abort event
   lives), catch `asyncio.CancelledError` around the blueprint run, call
   `checkpoint.mark_interrupted()` (new method on `Checkpoint`, mirroring `mark_failed`), and
   re-raise. Unit test: cancel a run mid-phase → checkpoint JSON has `status: "interrupted"`.
3. Keep both changes strictly additive; the CLI behavior must be unchanged
   (`task goblin:test` stays green).

## 2.2 Run registry store — `goblin_server/store.py`

Use an aiosqlite store with `_migrate()` and dict rows, following the repo's database
conventions [L11]:

```sql
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    blueprint_name TEXT NOT NULL,
    blueprint_source TEXT NOT NULL,        -- 'inline' | 'path'
    blueprint_path TEXT NOT NULL,          -- materialized path inside the run dir
    context_json TEXT NOT NULL,
    status TEXT NOT NULL,                  -- pending|running|completed|failed|interrupted
    run_dir TEXT NOT NULL,
    checkpoint_path TEXT,
    error TEXT,
    created_at TEXT NOT NULL,              -- ISO 8601 UTC
    started_at TEXT, completed_at TEXT
);
```

Status values deliberately reuse `RunStatus` plus `pending` (queued behind the semaphore).

## 2.3 RunService — `goblin_server/core/runs.py`

Responsibilities:

- **Submit** (`submit_run(spec: RunSubmission) -> RunRecord`):
  - Validate inline YAML early via `validate_blueprint(path, raw=...)`; reject inline
    blueprints declaring `imports:` with a clear error (file-path imports can't resolve for
    over-the-wire submissions [L1]). Server-side `blueprint_path` submissions must live under
    `GOBLIN_SERVER_BLUEPRINTS_DIR` (path traversal guard).
  - Create run dir `<workspace_root>/<run_id>/`, materialize inline YAML to
    `blueprint.yaml`, insert registry row (`pending`), enqueue.
- **Execute**: an `asyncio` worker loop bounded by
  `asyncio.Semaphore(config.max_concurrent_runs)`; per run:
  `runner = BlueprintRunner(materialized_path, run_dir=run_dir)`; record
  `checkpoint_path = runner checkpoint path` (`.goblin/run-<run_id>.json` under run_dir);
  `await runner.run(context)` inside a named `asyncio.Task` stored in an in-memory
  `dict[str, Task]` for cancellation; final status from the checkpoint / exception.
- **Cancel** (`cancel_run(run_id)`): `task.cancel()`; the §2.1 change records `interrupted`;
  unknown-or-terminal runs → domain error.
- **Resume** (`resume_run(run_id)`): loads the stored `checkpoint_path`, re-enters the queue
  with `BlueprintRunner.resume(checkpoint_path)` [L1].
- **Events**: a small in-process pub/sub (per-run `set[asyncio.Queue]`) fed by registry
  transitions and by polling the checkpoint file between phase boundaries (cheap: file is
  small, rewritten atomically per phase [L4]). Event envelope:
  `{event: "run.started"|"run.phase_started"|"run.phase_completed"|"run.phase_failed"|"run.completed"|"run.failed"|"run.interrupted", run_id, phase_id?, ts, payload?}`
  with dot-notation names per repo logging conventions [L11].
- **Concurrency safety**: runs are independent (own run_dir/checkpoint); shared global state
  is read-only at run time (`HOOK_REGISTRY` registered once at startup; config singletons are
  env-driven) [L1]. Document that hook registration happens in `main.py` lifespan, not per
  request.

## 2.4 Tests (`goblin-server/tests/`)

- Store CRUD + migration on `tmp_path`.
- Submit inline blueprint → row created, YAML materialized, idempotent run_id handling.
- Registry transitions pending→running→completed with a **fake runner** (mock at the
  `BlueprintRunner` boundary per repo testing rules [L11]).
- Cancel a running task → status `interrupted`; cancel on terminal run → error.
- Resume enqueues with the stored checkpoint path.
- Semaphore: with max=1, second run stays `pending` until first finishes.

## Acceptance criteria

- [ ] `goblin` package changes merged with tests; `task goblin:test` green.
- [ ] RunService unit tests green (`task goblin-server:test`).
- [ ] A real (cheap, single-prompt) blueprint can be driven end-to-end in-process:
      submit → running → completed, checkpoint present under the run dir.
