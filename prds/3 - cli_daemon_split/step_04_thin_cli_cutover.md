# Step 4 — Thin CLI cutover: `goblin` talks to the daemon

**Goal:** Rewire the existing Click surface so `goblin run` and `goblin validate` are wire
clients of the daemon — with byte-identical UX and exit codes — plus daemon lifecycle
commands and a `runs` listing. After this step the startup problem is solved: every
command against a warm daemon answers in well under a second, and `goblin --local` keeps
the exact pre-split path available. **This is the V1 ship gate.**

**Depends on:** step 3. **Blocks:** steps 5–7.

---

## 4.1 Mode selection (per command)

```
goblin run <path>                 # daemon mode (default)
goblin run --local <path>         # in-process mode: today's exact code path, untouched
goblin validate <paths...>        # daemon mode; --local likewise available
```

- Default = daemon. `--local` is the explicit escape hatch (README §5.2: **no silent
  fallback** — a daemon failure must never quietly become an 11 s in-process run).
- Daemon unreachable after auto-start attempts → `DaemonUnavailable` with:
  `goblin: daemon unavailable (<reason>). Start it with 'goblin daemon start', or run
  in-process with --local.` Exit code 2 (usage/environment), distinct from run-failure 1.
- Env override: `GOBLIN_DAEMON=off` forces local mode (CI/containers without the daemon).

## 4.2 Daemon discovery + auto-start — `goblin/goblin/daemon_lifecycle.py`

Light imports only (stdlib + step-2 client):

1. Resolve project root by walking up for `.goblin/` (step-2 discovery).
2. Probe `<root>/.goblin/daemon.sock` with `health()` (250 ms timeout). Live → use it.
3. Dead/absent socket → clean stale socket+pid, spawn
   `goblin-daemon serve --project-root <root>` via `subprocess.Popen(...,
   start_new_session=True, stdout/stderr → <root>/.goblin/daemon.log)`, poll `health()`
   up to **20 s** (covers the ~11 s SDK warm-up [L18]).
4. `goblin daemon start [--wait]` / `stop` (SIGTERM to pid, wait for socket teardown) /
   `status` (health JSON rendered: uptime, active runs, protocol/daemon versions, db
   path). A lazily-loaded Click subgroup — but note `cli.py` currently has **no**
   LazyGroup (removed with the relay [L1, L13]); implement plain lazy imports inside the
   group body, consistent with the current `run`/`validate` pattern [L1].

## 4.3 `goblin run` over the wire

Map today's options 1:1 (`--context`, `--run-dir`, `--resume`, `--quiet`, `--log-*`):

- **Submit:** read blueprint text only to fail fast on missing files; send
  `blueprint_path` (absolute) + `context` + `run_dir` (default: cwd). `--resume <path>`
  resolves the checkpoint to a run via the daemon (step-3 resume-by-checkpoint-path).
- **Wait + render:** poll `get_run` (250 ms backoff, capped 2 s) until terminal; step 5
  replaces polling with the SSE stream. Render the final `RunRecord` into **today's exact
  result output** (phase results dict shape, quiet behavior) — diff-tested against
  `--local` output in §4.6.
- **Exit codes:** completed → 0; failed/interrupted → 1 (with the same error summary the
  local path prints); usage/env errors → 2 via Click.
- **Ctrl-C:** sends `cancel` (not a local kill) on first press; second press detaches and
  exits 130, printing `goblin runs <id>` reattach hint. Document that detach is safe
  because the run lives in the daemon.
- `--log-*` flags apply to the **client's** logging only in daemon mode; daemon logging is
  configured at `serve` time (document in `docs/cli/goblin.md`).

## 4.4 `goblin validate` over the wire

Read each YAML file (client-side, for the same missing-file errors as today), POST each
to `/v1/blueprints/validate`, print via the **existing** `format_errors` (it operates on
finding dicts — the wire `ValidationFinding` dumps to the same shape [L10]), preserving
`valid` / `valid (with warnings)` / `invalid` output and the exit-1-on-error rule.
Against a warm daemon this drops from ~10.7 s to ~0.1 s [L18] — record the measured
number in this file.

## 4.5 `goblin runs` (new read-only command)

```
goblin runs                 # table: run_id (short), status, blueprint, started, duration
goblin runs <run_id>        # detail: phases, results, spend, error
```

Thin rendering over `list_runs`/`get_run`. This is the run-registry surface that never
existed before (README §2.3) and the reattach handle for detached runs.

## 4.6 Compatibility test matrix (the "don't break it" gate)

New `goblin/tests/unit/test_cli_daemon_mode.py` + e2e in `goblin-daemon/tests/`:

- **Golden outputs:** run `examples/basic/structured_output_example.yaml` (mocked LLM at
  the provider boundary [L16]) via `--local` and via daemon; assert identical stdout,
  result JSON, and exit codes.
- `validate` over all `examples/**/*.yaml` both modes → identical output/exit codes
  (`task goblin:validate` extended to exercise the daemon path once).
- `--resume` a failed run in each mode → same checkpoint transitions [L4].
- Missing blueprint file / bad `--context` JSON / unknown flags → identical Click errors.
- Daemon-down paths: no socket + failed auto-start → the §4.1 error, exit 2.
- Ctrl-C cancel → registry + checkpoint both `interrupted`; reattach via `goblin runs`.
- **Existing suites untouched:** `goblin/tests/integration/*` keep driving
  `BlueprintRunner` directly (they test the runtime, which is unchanged) [L16, L17].

## 4.7 Docs + metadata

- `docs/cli/goblin.md`: daemon mode default, `--local`, `daemon` subgroup, `runs`.
- New `docs/daemon.md`: what the daemon is, socket/db/log locations, auto-start, idle
  shutdown, per-project model, security posture (0600 socket, local trust).
- `docs/getting-started/*`: no flow change (auto-start makes the daemon invisible).
- `AGENTS.md`/`CLAUDE.md`: new workspace member, `goblin-daemon:*` tasks, thin-CLI import
  rule (no openhands/litellm/fastapi imports in `goblin.cli`/`goblin.client`/
  `goblin.protocol` — guarded by step 2.3).
- `ROADMAP.md`: tick the agent-server line items that this step effectively delivers
  (remote runtime exists; compose lands in step 7, k8s in `prds/4 - k8s_operator`).

## Acceptance criteria

- [ ] §4.6 matrix green; root `task lint typecheck test validate` green.
- [ ] Measured against a warm daemon (record here): `goblin validate`, `goblin run`
      submission-to-first-phase, `goblin runs`, `goblin daemon status` — all < 1 s;
      `import goblin.cli` still < 500 ms with heavy modules absent (step-2.3 guard).
- [ ] `goblin run --local` behavior demonstrably unchanged (golden-output diff).
- [ ] Docs + AGENTS.md + ROADMAP updates landed.
