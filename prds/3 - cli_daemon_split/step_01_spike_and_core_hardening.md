# Step 1 — Spike: daemon-hosting assumptions + core hardening in `goblin`

**Goal:** Prove that one long-lived process can host multiple sequential/concurrent
`BlueprintRunner` executions without cross-contamination, and land the small **strictly
additive** core changes every later step depends on. No CLI behavior changes; the whole
step ships behind the existing test suite staying green.

**Depends on:** nothing. **Blocks:** steps 2–4.

---

## 1.1 Spike: verify the daemon-hosting assumptions

Do these in a scratch script/tests first; record findings in this file before writing
feature code.

1. **Sequential runs in one loop.** In a single process, run the cheap example blueprints
   (`examples/basic/structured_output_example.yaml`, then `examples/test.yaml` with mocked
   conversations) back-to-back through `BlueprintRunner`. Assert both complete and their
   checkpoints are independent [L3, L17].
2. **Concurrent runs in one loop.** Two `asyncio` tasks, two different run dirs, two
   blueprints with **different `load_project` projects** (different `.claude/settings.json`
   hooks). Assert the cross-contamination predicted in README §3.3: the second
   `load_project` overwrites `HOOK_REGISTRY["project_pre_tool"|"project_post_tool"]` for
   the first [L2, L5]. This confirms the blast radius that §1.3.4 fixes.
3. **One-shot config/logging.** Call `load_config()` + `configure_logging(...)` once at
   process start (as a daemon would), then run blueprints. Assert runs neither re-read
   `.env` nor duplicate log handlers [L9].
4. **uvicorn on a unix socket.** Boot a trivial FastAPI app with
   `uvicorn ... --uds /tmp/spike.sock`; assert `curl --unix-socket` and an `httpx`
   `AsyncHTTPTransport(uds=...)` client both reach `/healthz`, and that a
   `StreamingResponse` SSE endpoint streams incrementally to the httpx client [S4, S7, S8].
5. **Import isolation check.** Confirm `httpx` import does **not** transitively import
   `openhands`/`litellm` (it must not — the CLI budget depends on it), and measure
   `import goblin.cli` + `goblin.protocol` + `goblin.client` combined under 500 ms with
   `scripts/check_import_time.py` (extended in step 4) [L12, S8].
6. **Checkpoint diffing.** During a real run, poll `<run_dir>/.goblin/run-<id>.json` at
   ~250 ms intervals from a second task; confirm phase transitions are observable,
   timestamped, and never torn (FileLock + atomic replace make this safe) [L4]. This is
   the step-5 event source.

**Spike exit criteria:** 1–6 pass and are recorded. If concurrent runs show shared-state
failures beyond the hook registry, escalate: re-scope step 1 before any daemon code.

## 1.2 Non-goals

- No new packages, no protocol, no CLI changes, no FastAPI anywhere yet.
- No behavior change to `goblin run` / `goblin validate` semantics, output, or exit codes.

## 1.3 Core hardening changes (all in `goblin/goblin/`, strictly additive)

### 1.3.1 Public run id — `runner.py`

```python
@property
def run_id(self) -> str:
    return self._run_id
```

Unit test: property equals the id embedded in the checkpoint filename.

### 1.3.2 Cancellation → `interrupted` — `runner.py`, `checkpoint.py`, `enums.py`

- `Checkpoint.mark_interrupted()` mirroring `mark_failed()` (sets
  `status: RunStatus.INTERRUPTED`, `updated_at`, atomic flush under the FileLock) [L4].
- In `BlueprintRunner.run()` (and/or the orchestrator abort path), wrap the blueprint run
  in `try / except asyncio.CancelledError: mark_interrupted(); raise` [L3].
- Unit tests: cancel the run task mid-phase (use a phase stub that blocks) → checkpoint
  JSON has `"status": "interrupted"`; the exception still propagates as `CancelledError`;
  `resume()` from an interrupted checkpoint behaves like resuming a failed one (phases
  marked `running` are reset by `Checkpoint.load()` — existing behavior [L4]).

### 1.3.3 Structured `spend` serialization — `checkpoint.py`, `cost_control.py`

Today phase results embed `SpendTracker` and checkpoint JSON stores its **repr string**
via `json.dumps(default=str)` [L4]. Add `SpendTracker.to_dict() -> {"iterations": int,
"tokens": int, "cost": float}` and serialize `spend` as that dict in checkpoint writes.
Keep reading tolerant: `Checkpoint.load()` accepts both the new dict and the legacy repr
string (leave legacy values untouched on load — read-only tolerance, no repr parsing).
Unit tests: round-trip new format; load a fixture file containing the legacy repr.

### 1.3.4 Per-run hook registry scoping — `hooks.py`, `blueprint.py`, `validation.py`, `orchestrator.py`

The global `HOOK_REGISTRY` is both mutated per run (`load_project` writes fixed keys) and
read at validation and failure-notification time [L2, L5]. Introduce scoping without
breaking externally-registered hooks:

- `hooks.py`: add `HookRegistry` — a thin class wrapping a dict with an optional
  read-through fallback to the process-global `HOOK_REGISTRY`. Keep `register_hook()`
  writing to the global (external registrants unchanged).
- `V1Blueprint` gains a per-instance `hook_registry: HookRegistry` (default: one that
  read-throughs to the global). `load_project()` writes into the **instance** registry,
  not the global [L5].
- `validation.py` `_validate_hook_names` and the orchestrator's `on_failure.notify`
  lookup read from the blueprint's registry (read-through means globally-registered hooks
  — e.g. test doubles — still resolve exactly as today) [L5, L10].
- Unit tests: two blueprints with different projects → isolated hook sets (this is spike
  §1.1.2 turned into a regression test); a hook stuffed in the global registry still
  validates and fires; existing hook tests stay green unmodified.

### 1.3.5 Light validation path — `validation.py`, `hooks.py`

Make `goblin validate` stop paying the litellm tax **even in `--local` mode**:
move `hooks.py`'s `from openhands.sdk.hooks import HookDefinition` behind
`TYPE_CHECKING` + a function-level import (the registry only needs the name for
`isinstance` checks — do them lazily). Re-run the timing table from README §3.1:
`goblin validate` should drop substantially; `goblin run` still pays the import (it must —
it runs agents), which is exactly what the daemon amortizes. Assert
`scripts/check_import_time.py` stays green [L12].

### 1.3.6 Opportunistic doc/deps hygiene (tiny, separate commits)

- Remove the stale Telegram HITL bullet in `docs/index.md` [L19].
- Fix the `docs/advanced/custom-hooks.md` example so the registered notify hook is a
  callable (or document that `HookDefinition` entries don't fire as `notify`) [L19].
- Leave the dead `fastapi` dependency in place — step 3 decides whether it moves to
  `goblin-daemon` or stays [L19].

## 1.4 Tests

All under `goblin/tests/unit/` unless noted:

- `test_runner.py`: public `run_id`; cancellation → interrupted checkpoint (+ resume from
  interrupted).
- `test_checkpoint.py`: `mark_interrupted`; structured `spend` round-trip; legacy repr
  tolerance.
- `test_hooks.py` (new): `HookRegistry` scoping, read-through, isolation between two
  blueprint instances.
- `test_orchestrator.py`: `on_failure.notify` resolves through the scoped registry.
- `test_validation.py`: hook-name validation against scoped registry; validation of a
  hookless blueprint does not import `openhands` (assert `sys.modules`).
- Full suite + `task goblin:validate` + VCR golden path must pass **unmodified** [L16, L17].

## Acceptance criteria

- [ ] Spike §1.1 items 1–6 executed; findings recorded in this file.
- [ ] §1.3.1–1.3.5 landed with unit tests; `task lint typecheck test validate` green with
      **zero changes to existing tests**.
- [ ] Concurrency regression test: two concurrent runs with different projects no longer
      clobber each other's hooks.
- [ ] `goblin validate` timing re-measured and recorded here; `goblin run` behavior,
      output, and exit codes byte-identical to before the step.
