# PRD: CLI/Daemon Split — a Thin `goblin` Client over a Long-Running Daemon

**Status:** Proposed
**Author:** Investigation + planning (AI-assisted)
**Date:** 2026-07-27
**Roadmap ref:** `ROADMAP.md` — "Agent Server (Remote runtime...)", "Set up remote box on K8s",
"Do we need an eventbus?" [L14]
**Related PRDs:** `prds/agent_server_plan` (run-service core absorbed here — see §3.8),
`prds/2 - crew_mode` (unaffected; its CLI surface becomes another thin client later),
`prds/4 - k8s_operator` (the Kubernetes endgame, split out as its own PRD — see §6.4)
[L14, L15]

---

## 1. Summary

Split goblin into two runtime pieces:

1. **A thin `goblin` CLI client** — the existing Click surface, but with **zero heavy imports
   on any code path**: no `openhands-sdk`, no `litellm`, no agent machinery. It reads
   blueprint YAML, packages requests, and talks to the daemon over a **versioned JSON/HTTP
   wire protocol on a unix domain socket** (SSE for event streaming), using pydantic wire
   models shared through the `goblin` package itself.
2. **A long-running `goblin-daemon`** — a new workspace member that owns every heavy import
   (litellm pays its ~11 s import **once**, at daemon start), the blueprint runtime
   (`BlueprintRunner` and everything below it), a persistent **run registry**, an **event
   bus**, and a **notification dispatcher** — the things a long-lived process is simply
   better at than a per-invocation CLI.

The key decision of this PRD: **the wire protocol is plain HTTP/JSON + SSE over a unix
socket (FastAPI/uvicorn server, httpx client) — not gRPC, and not msgpack-RPC.** The
investigation (§3.6) measured the alternatives: gRPC costs an 18 MB C-extension client plus
a protoc toolchain for cross-language benefits this project does not have, and msgpack-RPC
has no maintained Python library at all (the canonical one died in 2018). HTTP-over-UDS is
the Docker precedent: boring, debuggable with `curl`, streamable, and — critically — the
pydantic request/response models survive the k8s transition **verbatim as CRD schemas**, so
the protocol itself is the disposable part.

The daemon is **per-project** (socket at `<project>/.goblin/daemon.sock`, cwd = project
root), which preserves every existing cwd/`.env`/config-singleton semantic exactly — the
runtime code path is wrapped, not rewritten. A `--local` escape hatch keeps today's
in-process behavior (and all integration tests) working unchanged.

Endgame (specified separately in `prds/4 - k8s_operator`): the daemon containerizes and
moves into a cluster, and a thin operator translates `BlueprintRun` CRDs into
wire-protocol calls against it — "resources sending requests via the wire protocol to
spin up workflows and agents", with the daemon staying a plain service rather than
becoming a controller itself.

Deliverables: core hardening in `goblin/` (public `run_id`, cancellation → `interrupted`,
structured `spend` serialization, per-run hook scoping), `goblin/goblin/protocol.py` +
`goblin/goblin/client.py`, the `goblin-daemon/` workspace member, CLI cutover with daemon
auto-start, SSE event streaming, a daemon-side notification service, and container/remote
deployment. Kubernetes is **deliberately out of this PRD** — it is specified in
`prds/4 - k8s_operator`, which builds on step 7's remote mode.

---

## 2. Problem statement

### 2.1 The startup pauses are real, measured, and irreducible per-process

The CLI bootstrap itself is already disciplined — `import goblin.cli` is **273 ms**, guarded
at 500 ms by `scripts/check_import_time.py` [L12]. The pauses happen **inside command
execution**, on this machine (warm cache, Python 3.13) [L18]:

| Command | Wall time | Why |
|---|---|---|
| `goblin --help` | 0.47 s | no heavy imports (lazy-import discipline [L1]) |
| `python -c "import litellm"` | **7.81 s** | ~140 provider trees statically imported [L13] |
| `python -c "import openhands.sdk"` | **11.07 s** | SDK `__init__` → `llm/llm.py` → litellm |
| `goblin validate examples/test.yaml` | **10.6–10.8 s** | `validation.py:82` → `goblin.hooks` → openhands → litellm |
| `goblin run <blueprint>` | ~11 s before the first phase starts | `runner.py` → `validate_blueprint` → `goblin.hooks`; then orchestrator/phase/conversation imports |

The root cause is one module-level line: `goblin/goblin/hooks.py:5`
(`from openhands.sdk.hooks import HookDefinition`), reached by **every** `run` and
**every** `validate` invocation [L2]. The Nuitka binary-compilation project that tried to
attack this from the packaging side was abandoned ("pull the plug on binary compilation")
[L13]. Per-process, ~11 s is irreducible: somebody has to import litellm before any agent
work. The only way to make it disappear from the user's path is to pay it **once**, in a
process that stays alive.

### 2.2 Notifications want a long-running server — and used to have one

The `notifications-relay` workspace member (FastAPI + Telegram bot, pairing, inbox,
ask-user human-in-the-loop) was removed in commit `f41b91e`; the `goblin notifications` CLI
group went with it [L13]. What remains is exactly one mechanism: `on_failure.notify`, a
**synchronous, in-process callable** looked up in the global `HOOK_REGISTRY` during a run
[L5]. It cannot outlive the run, cannot wait for a reply, cannot retry delivery, and has
quirks (`HookDefinition` entries registered by `load_project` are not callable, so the
documented Slack example silently never fires) [L5, L19]. The acronym's "N" is
"Notifications" [L19]; the docs still advertise Telegram HITL that no longer exists
[L19]. A daemon is the natural home for the replacement: it can dispatch webhooks/ntfy on
run lifecycle, hold reply-waiting state, and (V2) rebuild human-in-the-loop.

### 2.3 A run is an opaque, unaddressable process

- No run registry: a run's UUID is private (`BlueprintRunner._run_id`) and discoverable only
  from log lines or by globbing `.goblin/run-*.json` [L3, L4].
- No cancellation: `RunStatus.INTERRUPTED` exists and is **never written**; nothing catches
  `asyncio.CancelledError` [L4].
- No event stream: there is no emitter/callback/pub-sub anywhere in the runner,
  orchestrator, or phases; observability is JSONL logs and checkpoint file transitions
  [L5, L7].
- Checkpoint JSON embeds non-structured values (`spend` is persisted as a Python `repr`
  string of `SpendTracker`, `default=str` leaks into `context`) — fine on disk, embarrassing
  on a wire [L4].

### 2.4 The k8s-operator future has nothing to talk to

The roadmap wants a remote runtime, k8s hosting, and trigger-driven runs [L14]. The sibling
`agent_server_plan` PRD designed an HTTP run service for this — and is **0 % landed** (no
`goblin-server/` member, no FastAPI app; even its two-line core prerequisites are unlanded)
[L14]. Whatever the eventual deployment shape, there must exist a long-running process that
accepts "spin up a workflow" requests over a defined protocol. Today there isn't one.

### 2.5 Requirements, restated

1. Eliminate the per-command startup pause: after a one-time daemon start, every `goblin`
   command — including `run` and `validate` — responds in well under a second.
2. Do not break the working application: blueprint semantics, checkpoint layout, resume,
   `run`/`validate` CLI behavior and exit codes, env vars, and the integration-test path
   (`BlueprintRunner` driven directly, VCR-recorded) all survive [L16, L17].
3. Give notifications (and future long-running concerns) a proper home.
4. Define a wire protocol now that a k8s deployment can reuse later, when cluster resources
   trigger workflows.
5. Stage the work so each stage is independently mergeable and the CLI keeps working at
   every boundary.

---

## 3. Investigation

### 3.1 Where the time goes (and what a daemon amortizes)

Import chain, verified line-by-line [L1, L2]:

```
goblin validate / goblin run
  → goblin/validation.py:82            from goblin.hooks import HOOK_REGISTRY   (function-level, but always reached)
  → goblin/hooks.py:5                  from openhands.sdk.hooks import HookDefinition   (MODULE level — the culprit)
  → openhands/sdk/__init__.py          (runs on any submodule import)
  → openhands/sdk/llm/llm.py:45-80     module-level litellm imports
  → litellm                            ~140 provider trees, ~7.8 s alone
```

A daemon flips the economics: the ~11 s is paid once at daemon start (or lazily on first
run); every subsequent CLI command is a socket round-trip. Measured client-side costs for
the protocol candidates (§3.6) are 3–175 ms.

Secondary effect worth stating: `goblin validate` against a warm daemon drops from ~10.7 s
to ~50–100 ms, because hook-name and `output_model` checks run against an already-imported
runtime [L10].

### 3.2 The runtime is already daemon-shaped (verified in-repo)

| Concern | Finding | Location |
|---|---|---|
| Run from serializable inputs | `BlueprintRunner(path, run_dir=...)` → `await runner.run(context: dict) -> dict`; path + JSON dict is the whole input surface | `goblin/goblin/runner.py` [L3] |
| In-memory validation | `validate_blueprint(path, raw=...)` with `raw=` reads no file — wire-friendly; findings are clean JSON `{path, message, validator, severity}` | `goblin/goblin/validation.py` [L10] |
| Resume | `BlueprintRunner.resume(checkpoint_path)` — re-reads the blueprint from disk; checkpoint `load()` resets `running` phases and re-runs failed ones | `runner.py`, `checkpoint.py` [L3, L4] |
| Concurrent runs | `BlueprintRunner`/`V1Blueprint`/`PhaseOrchestrator`/`Checkpoint` state is per-instance; phases already fan out with `asyncio.gather`; multiple run tasks in one loop are architecturally fine | `runner.py`, `orchestrator.py` [L3, L5] |
| Remote execution | `workspace: {type: remote, host, api_key}` → `RemoteConversation` — the existing escape hatch that keeps working under a daemon | `goblin/goblin/workspace.py`, `conversation.py` [L7, L8] |
| Schema is package data | `spec/v1.json` via `importlib.resources` — daemon validates without a repo checkout | `validation.py`, `goblin/pyproject.toml` [L10, L17] |
| Config is env-driven | frozen `GoblinConfig` singleton; all secrets env-based — daemon owns them, client needs none | `goblin/goblin/utils/config.py` [L9] |

### 3.3 What fights a long-running, multi-run process (must fix in step 1)

| State | Location | Risk |
|---|---|---|
| `HOOK_REGISTRY` global | `goblin/goblin/hooks.py`; mutated by `V1Blueprint.load_project()` with **fixed keys** `project_pre_tool`/`project_post_tool` | **Real race**: two concurrent runs with different projects clobber each other's hooks mid-flight; also read by validation [L2, L5] |
| Config singleton + `.env` | `utils/config.py`; `load_dotenv_from_cwd()` | Per-project daemon (cwd = project root) preserves semantics exactly; configure once at daemon startup [L9] |
| Logging setup | `goblin_common.logging.configure_logging` clears/replaces handlers | CLI calls it per-invocation today; daemon must configure exactly once [L9, L16] |
| cwd defaults everywhere | `run_dir=None → Path.cwd()`; `LocalWorkspace(working_dir=".")`; swarm workspace base `Path.cwd()`; worktree `repo_root=os.getcwd()`; logs root fallback | Per-project daemon with cwd = project root keeps all of them true [L3, L6, L8, L11] |
| Private `_run_id` | `runner.py:42` | Registry needs a public accessor [L3] |
| No cancellation path | `runner.py`/`orchestrator.py`; `INTERRUPTED` never written | `task.cancel()` would orphan in-flight work; add `CancelledError → mark_interrupted()` [L4] |
| `spend` repr strings | `checkpoint.py` (`json.dumps(default=str)`) | Normalize before serving over the wire; ideally fix serialization at the source [L4] |
| `output_model` dotted imports | `validation.py` (`importlib.import_module` of arbitrary paths) | Code-execution surface — same as today locally, but flag for any multi-tenant/remote future [L10] |
| ACP/MCP subprocesses | `phase.py` (`npx`, provider keys from `os.environ`) | Daemon owns `PATH`/Node and provider keys; per-run isolation already exists [L6] |

### 3.4 Events: nothing exists, but the seams are cheap

No emitter, callback, or pub/sub exists in `runner`/`orchestrator`/`phase`/`conversation`
(grep-verified) [L5, L7]. Two seams, in order of increasing fidelity:

1. **Checkpoint diffing (zero core changes).** Phase transitions are timestamped rows in a
   small JSON file, rewritten atomically and lock-protected per phase [L4]. A daemon-side
   poller/differ produces `run.phase_started/completed/failed` events safely today.
2. **SDK conversation callbacks (one passthrough).** The pinned SDK's conversation
   constructor accepts `callbacks: list[Callable[[Event], None]]`; goblin's
   `run_conversation()` simply doesn't pass any [L7]. Threading an optional `callbacks=`
   parameter through `run_conversation` (and the two phase call sites) turns every agent
   event into a daemon-publishable stream — an additive change to working code.

Step 5 builds (1) first, then (2).

### 3.5 Notifications, past and present

- **Removed** (`f41b91e`): the `notifications-relay` FastAPI service (pairing, inbox,
  Telegram bot, ask-user tool), `goblin/goblin/notifications/{client,cli}.py`, the
  `goblin notifications` CLI group, relay env vars (`RELAY_*`), and its docs [L13].
- **Remaining**: `on_failure.notify` → in-process callable from `HOOK_REGISTRY` (see §2.2
  for its quirks) [L5]; a dead `fastapi>=0.140.0` dependency in `goblin/pyproject.toml`
  imported nowhere [L19]; stale docs promising Telegram HITL [L19].
- **Consequence for this PRD:** notifications are greenfield inside the daemon, not a
  migration. Step 6 designs a dispatcher (log/webhook/ntfy providers) fed by the step-5
  event bus, and makes `on_failure.notify` actually work by letting the daemon register
  **real callables** that publish to the dispatcher.

### 3.6 Wire protocol evaluation (measured)

Requirement profile: same-machine IPC (unix socket), request/response + **server→client
streaming** (run events), a client footprint small enough to keep `goblin` thin and the
500 ms import guard honest, schema discipline, and a clean path to the k8s endgame (§3.7).

| Candidate | Client footprint (measured) | Streaming | Maintenance | Verdict |
|---|---|---|---|---|
| **gRPC** (`grpcio` 1.83) | **18 MB installed, ~132 ms import**; needs `grpcio-tools`+protoc codegen toolchain | Native server-streaming, excellent | Very active; py3.13 wheels (no free-threaded wheels yet) | Rejected: right tool for cross-language datacenter RPC; goblin is Python↔Python on one machine [S1] |
| **msgpack-RPC** | `msgpack` itself: 1.4 MB, **3.4 ms import** — but there is **no maintained msgpack-RPC library** (`msgpack-rpc-python` last release 2018, Tornado-based) | Hand-roll framing/routing/cancellation forever | Serializer alive; RPC layer dead | Rejected: we'd own an RPC framework to save bytes that don't matter on a socket [S2] |
| **JSON-RPC 2.0** | stdlib-only possible | No streaming semantics; notifications must be emulated (LSP pattern) | `jsonrpcserver` stale (2022), `jsonrpcclient` stale (2023) | Rejected: viable but strictly subsumed by the selected option [S3] |
| **HTTP/JSON + SSE over UDS** ✅ | `httpx`: **2.3 MB, ~173 ms import** (stdlib `http.client` fallback = 0 deps) | SSE/NDJSON over one persistent connection; Docker streams logs/events this way at scale | FastAPI/uvicorn/httpx all very active; uvicorn supports `--uds` natively | **Selected** [S4, S5, S7, S8] |

Deciding factors:

1. **The protocol is disposable; the models are not.** In the k8s phase, work is triggered by
   CRDs watched through the API server — the operator pattern consumes resources, not RPC
   connections (§3.7). What survives that transition is the daemon's **internal service
   layer** and the **pydantic wire models** (a `SubmitRunRequest` maps 1:1 onto a
   `BlueprintRun` CRD spec). Optimizing the transport for permanence is wasted effort.
2. **Repo alignment.** AGENTS.md already prescribes FastAPI + pydantic + `app.state`
   lifespan wiring; the sibling agent-server PRD designed the same shape; `fastapi` is even
   already a (currently dead) dependency of `goblin` [L16, L14, L19]. gRPC would add a
   second API paradigm and a codegen toolchain for zero cross-language benefit.
3. **Debuggability.** `curl --unix-socket .goblin/daemon.sock http://localhost/v1/runs` —
   the Docker precedent [S5]. No generated stubs, no reflection tooling.
4. **Escape valve.** Plain JSON over HTTP means a zero-dependency stdlib client is always
   possible if the 173 ms httpx import ever threatens the CLI budget [S8].

Auth on the local socket is Docker-style filesystem trust (socket dir `0700`, socket
`0600`, same uid) plus an optional shared token; remote mode (step 7) adds Bearer auth,
matching the repo's `require_api_key` convention [L16].

### 3.7 The k8s endgame (shape only — fully specified in `prds/4 - k8s_operator`)

- **Operators don't speak RPC; they watch resources.** A Python operator (e.g. kopf)
  opens long-lived HTTPS watch streams against the API server and reconciles CRUD events
  on CRDs; status goes back to the CR's `status` subresource + k8s Events [S6].
- **Design consequence for *this* PRD: the wire protocol is disposable; the models are
  not.** Whatever transport the CLI uses now is partially retired in the k8s phase —
  "submit a run" becomes `kubectl apply -f BlueprintRun.yaml`. What survives is the
  daemon's internal service layer and the **pydantic wire models** (a
  `SubmitRunRequest` maps 1:1 onto a `BlueprintRun` CRD spec). So this PRD optimizes
  the protocol for the local phase and clean service boundaries, keeps the daemon
  k8s-agnostic, and leaves all operator mechanics — framework selection, CRD
  generation, admission, HA — to the sibling PRD.

### 3.8 Sibling PRDs: status and relationship

| PRD | Landed | Relationship to this one |
|---|---|---|
| `1 - acp_for_alt_harnesses` | ~100 % (schema, `_run_acp`, validation, docs, tests) | Untouched. ACP subprocesses become daemon-hosted automatically [L20] |
| `2 - crew_mode` | 0 % (no `crew/` package) | Its CLI-only surface would later become another thin client of the daemon; no conflict. One stale citation noted (it references a `LazyGroup` pattern removed with the relay) [L15] |
| `agent_server_plan` | 0 % (no `goblin-server/`, no FastAPI app, prerequisites unlanded) | **This PRD absorbs its run-service core**: run registry, 202-accepted background runs, semaphore, resume/cancel, and its §6.3 core changes (public `run_id`, cancel→`interrupted`) — retargeted from "remote FastAPI + Bearer" to "local-first wire protocol on UDS". Its unique contribution — **Agent Canvas composition** via the embedded stock OpenHands agent server — is deliberately **not** in this PRD's scope and remains valid as a follow-up, rebased onto `goblin-daemon` instead of a separate `goblin-server` (§6.4) [L14] |

There should be **one** goblin server artifact, not two: if this PRD proceeds,
`agent_server_plan` steps 1–3 are superseded by steps 1–4 here; its steps 4–8 (Canvas,
compose, k8s, scheduling) compose on top of the daemon.

---

## 4. Options analysis

### Option A — gRPC

- Pros: first-class streaming; strict schemas; mature async support (`grpc.aio`); UDS
  support [S1].
- Cons: 18 MB C-extension + protobuf runtime **in the CLI**; protoc/`grpcio-tools` codegen
  toolchain new to this repo; cross-language stubs solve a problem goblin doesn't have;
  the whole transport is retired by the CRD endgame anyway (§3.7).
- **Verdict: rejected.** Runner-up if non-Python clients ever become a requirement.

### Option B — msgpack-RPC

- Pros: `msgpack` serializer is tiny and fast (1.4 MB, 3.4 ms) [S2].
- Cons: **no maintained Python msgpack-RPC library exists** (canonical impl dead since
  2018); choosing it means hand-rolling framing, routing, streaming, and cancellation —
  indefinitely — to save bytes on a transport where bytes are free [S2].
- **Verdict: rejected.**

### Option C — Versioned HTTP/JSON + SSE over a unix domain socket ✅

- FastAPI + uvicorn (`--uds`) daemon-side; `httpx` UDS transport client-side; pydantic
  models in `goblin/goblin/protocol.py` shared by both (the daemon depends on `goblin`
  anyway — it hosts `BlueprintRunner`) [S4, S8].
- Pros: smallest viable client (2.3 MB / ~173 ms, with a stdlib zero-dep fallback); native
  streaming via SSE; repo-conventional stack; curl-debuggable; models survive as CRD
  schemas; remote mode (step 7) is the same code on TCP+Bearer instead of UDS.
- Cons: SSE is one-directional (fine: commands are request/response, events are
  server→client); no schema codegen discipline beyond CI-tested models (mitigated: models
  live in one module consumed by both sides).
- **Verdict: selected.**

### Option D — No split; only harder lazy-loading

Move `hooks.py:5` and friends behind function-level imports and shave `validate` down.

- Pros: zero new architecture.
- Cons: the ~11 s is **irreducible per process** — `run` must import the SDK every time;
  solves nothing for notifications, run registry, cancellation, streaming, or k8s;
  Nuitka already died trying to package around this [L13].
- **Verdict: rejected** as the whole answer. (Its cheap half — deferring the `goblin.hooks`
  import out of `validate`'s hot path — is still worth doing as a step-1 micro-fix.)

### 4.5 Daemon topology: per-project vs. global

| | Per-project daemon ✅ | Global per-user daemon |
|---|---|---|
| cwd / `.env` / `.goblin` semantics | **Identical to today** (daemon cwd = project root; `load_dotenv_from_cwd`, config singleton, logging root, worktree/swarm defaults all unchanged) [L9] | Every one of those needs redesign (per-run env injection, per-run config replacing the frozen singleton) |
| `HOOK_REGISTRY` race (§3.3) | Same blast radius as today's one-process-per-run | Cross-project clobbering added |
| Memory | N × runtime RSS across projects; bounded by idle shutdown | One runtime |
| Failure isolation | One project's wedge kills only its daemon | Global blast radius |

**Verdict: per-project for V1** — it is the topology that lets us wrap the runtime without
rewriting it. A global daemon becomes feasible only after config-per-run refactoring, which
is explicitly out of scope (§6.4). In k8s, the analogous unit is per-cluster/namespace.

---

## 5. Target architecture (V1: stages 0–2)

```
   user shell (project root)                          same machine, per project
 ┌───────────────────────────┐      unix socket      ┌────────────────────────────────────┐
 │ goblin CLI (thin)         │  .goblin/daemon.sock  │ goblin-daemon (long-running)       │
 │  click + client.py        │ ◀──────────────────▶  │  uvicorn --uds, FastAPI app        │
 │  httpx (UDS transport)    │   JSON / SSE          │                                    │
 │  goblin/protocol.py       │                       │  api/    thin routers (HTTPExcept.) │
 │  (pydantic wire models)   │                       │  core/   RunService ── semaphore   │
 │                           │                       │        ├─ RunRegistry (aiosqlite)  │
 │  imports: click, httpx,   │                       │        ├─ EventBus (pub/sub)       │
 │  pydantic, goblin-common  │                       │        └─ Notifier (step 6)        │
 │  ── NO openhands/litellm ─┤                       │                                    │
 └───────────────────────────┘                       │  wraps, unchanged:                 │
        goblin run --local ──▶ in-process fallback   │   BlueprintRunner → V1Blueprint →  │
        (today's exact path, integration tests)      │   PhaseOrchestrator → phases →     │
                                                    │   run_conversation / ACP / swarm   │
                                                    │                                    │
                                                    │  owns: .env, config singleton,     │
                                                    │  logging (once), checkpoints,      │
                                                    │  swarm DBs, MCP/ACP subprocesses   │
                                                    └────────────────────────────────────┘
```

- **The runtime is wrapped, not rewritten.** `BlueprintRunner` and everything below it stay
  in the `goblin` package with today's semantics; the daemon imports them (paying litellm
  once), the CLI never does. Step 1's core changes are strictly additive.
- **Wire models live in `goblin/goblin/protocol.py`** — pure pydantic, no heavy imports,
  covered by the existing import-time guard. The daemon depends on `goblin`, so one module
  serves both sides; no new shared package needed.
- **Socket layout (per project):** `.goblin/daemon.sock`, `.goblin/daemon.pid`,
  `.goblin/daemon.db` (registry), socket dir `0700`. Discovery = walk-up for `.goblin` from
  cwd, same intuition as today.
- **Auto-start:** any daemon-backed command that finds no live socket spawns
  `goblin-daemon serve --project-root <root>` detached, waits on `/healthz` (budget ~20 s,
  covering the 11 s import), then proceeds. Idle shutdown (default 30 min) bounds memory.
  `goblin daemon start|stop|status` for explicit control.

### 5.1 Wire API (`/v1`)

| Endpoint | Request → Response | CLI consumer |
|---|---|---|
| `GET /healthz` | → `{status, protocol_version, daemon_version, project_root, uptime_s, active_runs}` | auto-start probe, `daemon status` |
| `POST /v1/blueprints/validate` | `{yaml: str}` → `{findings: [...]}` (today's exact finding dicts [L10]) | `goblin validate` |
| `POST /v1/runs` | `{blueprint_path | blueprint_yaml, context, run_dir?}` → **202** `{run_id, status}` | `goblin run` |
| `GET /v1/runs?status=&limit=` | → `{runs: [RunRecord]}` | `goblin runs` (new, step 4) |
| `GET /v1/runs/{id}` | → `RunRecord` (status, phases, results, spend — normalized, no repr strings) | `goblin run` final render |
| `POST /v1/runs/{id}/resume` | → 202 `{run_id, status}` | `goblin run --resume` |
| `POST /v1/runs/{id}/cancel` | → `{run_id, status: "interrupted"}` | new capability |
| `GET /v1/runs/{id}/events` | SSE stream of run/phase lifecycle (step 5) | `goblin run --follow` |

Submit semantics preserve today's filesystem reality on a local daemon: `blueprint_path`
(absolute) keeps `imports:` and disk-based resume working byte-for-byte [L3];
`blueprint_yaml` inline is materialized under a daemon-controlled run dir and **rejects
`imports:`** (unresolvable without a base dir — same rule the agent-server PRD reached)
[L14]. `run_dir` defaults to the client's cwd, transmitted explicitly.

### 5.2 Behavior compatibility contract

- `goblin run <path>` against a warm daemon: submits, streams/renders, exits 0/non-zero
  exactly as today; `--context`, `--run-dir`, `--resume`, `--quiet`, `--log-*` honored.
- `goblin run --local <path>`: today's exact in-process path — unchanged code, unchanged
  timing; also the automatic fallback when the daemon can't start and `--no-daemon` isn't
  set... no: **fallback must be explicit** (`--local`), because silently paying 11 s and
  running in-process hides daemon failures. Daemon unreachable → clear error suggesting
  `--local`.
- `goblin validate` via daemon: same output format and exit codes as today.
- Integration tests (`tests/integration`, VCR) keep driving `BlueprintRunner` directly —
  they test the runtime, which is untouched [L16].
- Checkpoint layout `<run_dir>/.goblin/run-<id>.json` (+`.lock`, atomic writes) unchanged —
  checkpoints remain resumable by `--local` runs and vice versa.

---

## 6. Requirements

### 6.1 Functional

| ID | Requirement |
|---|---|
| F1 | Daemon hosts blueprint execution: submit (path or inline YAML) + JSON context → run executes fully daemon-side; client may disconnect and re-attach. |
| F2 | Validate over the wire, byte-identical findings and CLI output/exit codes. |
| F3 | Run registry: list/status/results per run, durable across daemon restarts (aiosqlite, `_migrate()` [L16]). |
| F4 | Resume a run (checkpoint-path or run-id addressed). |
| F5 | Cancel a running run; checkpoint recorded `interrupted` (new core handling, step 1). |
| F6 | SSE event stream per run: phase lifecycle from checkpoint diffs (step 5a), fine-grained agent events via SDK `callbacks` passthrough (step 5b). |
| F7 | CLI parity + speed: `run`/`validate` over the wire with identical UX/exit codes; every command < 1 s against a warm daemon; `--local` escape hatch preserved. |
| F8 | Daemon lifecycle: auto-start on first use, `goblin daemon start|stop|status`, idle shutdown; per-project sockets. |
| F9 | Protocol versioning: `protocol_version` in `/healthz`; client refuses to talk to an incompatible daemon with an actionable error. |
| F10 | Notification service (step 6): daemon-side dispatcher (log/webhook/ntfy providers) fired on run/phase lifecycle; `on_failure.notify` works for daemon-registered hooks. |
| F11 | Remote deployment (step 7): same daemon over TCP+Bearer behind a container image; CLI `--server`/env config points at remote daemons. |

### 6.2 Non-functional

- **Do no harm.** Runtime semantics, blueprint schema, checkpoint format, env vars, and
  integration-test path unchanged; every stage independently mergeable with
  `task lint typecheck test validate` green throughout [L16].
- CLI stays thin: `import goblin.cli` + `goblin.client` + `goblin.protocol` must keep
  passing `scripts/check_import_time.py` (500 ms budget; httpx ~173 ms fits) [L12, S8].
  New guard: assert `openhands`/`litellm` absent from `sys.modules` after importing the CLI
  client path.
- Repo conventions per AGENTS.md: `from __future__ import annotations`, pydantic models,
  thin routers delegating to `core/`, `app.state` lifespan, `goblin_common` config/logging,
  aiosqlite `_migrate()`, atomic writes, structured logging with dot-notation events
  (`daemon.started`, `run.submitted`, `run.completed`, `notify.dispatched`) [L16].
- Concurrency: default 2 concurrent runs per daemon (`GOBLIN_DAEMON_MAX_CONCURRENT_RUNS`),
  rest queued `pending`; runs are asyncio tasks, never request/response blocking.
- Secrets never cross the wire and are never logged: all API keys stay daemon-side env
  [L9, L16]; socket dir `0700`, socket `0600`; remote mode requires a token.
- Unit tests mock at the `BlueprintRunner`/provider boundary; integration tests remain
  VCR-based and MCP-free [L16].

### 6.3 Changes by file/package

**`goblin/` (core — strictly additive, step 1):**

| File | Change |
|---|---|
| `goblin/runner.py` | Public `run_id` property; `CancelledError → checkpoint.mark_interrupted()` + re-raise. |
| `goblin/checkpoint.py` | `mark_interrupted()`; structured `spend` serialization (dict, not repr). |
| `goblin/hooks.py` | Per-run hook registry scoping (registry passed/carried per run instead of mutated global); keep global as read-through default for compatibility. |
| `goblin/validation.py` | Read hooks from the scoped registry; defer the `goblin.hooks` import behind the hook-check so schema-only validation stays light. |
| `goblin/conversation.py` | Optional `callbacks=` passthrough to the SDK conversation constructors (step 5b). |
| `goblin/protocol.py` | **New.** Versioned pydantic wire models; zero heavy imports. |
| `goblin/client.py` | **New.** `DaemonClient` (httpx UDS/TCP), discovery, auto-start, SSE reader. |
| `goblin/cli.py` | `run`/`validate` → wire path + `--local`; `daemon` lifecycle subgroup; `runs` listing. |
| `goblin/pyproject.toml` | Add `httpx`; `fastapi` stays (daemon uses it via dependency on `goblin` — or move to `goblin-daemon`; decided in step 3). |
| `goblin/daemon_*.py` | Lifecycle helpers (pid file, spawn, probe) — light imports only. |

**`goblin-daemon/` (new workspace member, step 3):** `pyproject.toml` (deps: `goblin`,
`goblin-common`, `fastapi`, `uvicorn[standard]`, `aiosqlite`), `goblin_daemon/cli.py`
(`serve`), `config.py` (env singleton), `main.py` (`create_app`, lifespan, `app.state`),
`api/` (`healthz`, `blueprints`, `runs`, `events`, `deps`), `core/` (`runs.py` RunService,
`store.py` registry, `events.py` bus, `notify/` step 6), `tests/` (ASGITransport unit
tests [L16]).

**Root:** workspace members + `Taskfile.yml` `goblin-daemon:*` tasks;
`infra/docker/base/Dockerfile` dep-layer copy; docs (`docs/cli/goblin.md`, new
`docs/daemon.md`); `AGENTS.md`/`CLAUDE.md` conventions update; ROADMAP checkbox updates at
the relevant stages.

### 6.4 Out of scope (V2+)

- **Agent Canvas composition** (embedded stock OpenHands agent server + loopback remote
  workspace injection) — remains the valid core of `prds/agent_server_plan`, rebased onto
  `goblin-daemon` as its own follow-up stage [L14].
- **Human-in-the-loop ask** (reviving the removed relay's ask-user/Telegram flow) — step 6
  ships one-way notifications only; HITL gets its own design note once the event bus exists
  [L13].
- **Kubernetes deployment and the operator** — all of it: manifests, CRDs, reconcile
  loops, admission webhooks, HA. Specified in `prds/4 - k8s_operator`, which depends on
  this PRD's step 7 (remote mode). This PRD's only k8s concessions are keeping the
  daemon k8s-agnostic and the wire models pydantic-clean (§3.7).
- **Scheduling/triggers** (cron, webhooks, issue trackers — roadmap "eventbus") [L14];
  a `CronBlueprintRun` CRD design note lives in the k8s operator PRD.
- **Global per-user daemon** and config-per-run refactoring (§4.5).
- **Multi-tenancy / untrusted submissions** — note `output_model` dotted imports execute
  daemon-side code [L10]; local trust domain only in V1.
- **gRPC transport** — runner-up documented in §4; the service layer is transport-agnostic
  enough to add it later without redesign.
- Crew mode as daemon client — after `prds/2 - crew_mode` lands [L15].

---

## 7. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Daemon-hosting assumptions wrong (concurrent runs, per-run state leaks) | Multi-run daemon corrupts runs | Step-1 spike drives N concurrent runs of the example blueprints in one loop before any service code; per-run hook scoping lands with tests [L17] |
| Core hardening breaks working runtime | Regressions in the shipping product | All step-1 changes strictly additive; full `task test validate` + VCR golden-path suite green per stage; `--local` path untouched throughout [L16, L17] |
| `.env`/cwd semantic drift between `--local` and daemon modes | Blueprint resolves different env in different modes | Per-project daemon with cwd = project root makes the modes identical by construction; e2e test asserts parity on a `${VAR}`-using blueprint [L9] |
| Auto-start flakiness (spawn, healthz timeout, zombie daemons) | CLI feels worse, not better | 20 s health budget (covers 11 s import); pid-file + socket liveness checks; `goblin daemon stop` in docs; stale-socket cleanup on probe; clear `--local` guidance on failure |
| SSE through proxies/shells misbehaves (remote mode) | Broken `--follow` remotely | Events also poll-able via `GET /runs/{id}` (checkpoint-sourced); SSE is an enhancement, not the only path |
| `HOOK_REGISTRY` scoping changes hook resolution semantics | Blueprints relying on global registration break | Read-through fallback to the process-global registry preserves today's behavior for externally-registered hooks; scoped writes only isolate `load_project` [L2] |
| Checkpoint format change (structured `spend`) breaks external readers | Scripts parsing repr strings fail | Additive: keep the old key as `spend_raw` for one release, add `spend` object; document in changelog |
| Two server efforts diverge (this PRD vs `agent_server_plan`) | Wasted/duplicated architecture | §3.8 explicitly supersedes agent_server_plan steps 1–3 and rebases its steps 4–8 onto `goblin-daemon`; update that PRD's status header when this one is accepted |
| Idle daemons accumulate (memory per project) | Machine bloat | Default 30-min idle shutdown; `daemon status` shows all live sockets under `~/.goblin/daemons/` registry of project sockets |
| Client/server version skew after upgrades | Confusing failures | `protocol_version` handshake (F9) with actionable error ("run `goblin daemon stop` to restart with the installed version") |

---

## 8. Step plan

| Step | File | Stage | Goal |
|---|---|---|---|
| 1 | `step_01_spike_and_core_hardening.md` | 0 — foundations | Spike daemon-hosting assumptions; land additive core changes (public `run_id`, cancel→`interrupted`, structured `spend`, per-run hook scoping, light validate path) |
| 2 | `step_02_protocol_models.md` | 1 — local split | `goblin/protocol.py` versioned wire models + `goblin/client.py` transport with unit tests |
| 3 | `step_03_daemon_and_run_service.md` | 1 — local split | `goblin-daemon` member: config, registry store, RunService, FastAPI app on UDS, lifecycle |
| 4 | `step_04_thin_cli_cutover.md` | 1 — local split | CLI over the wire: auto-start, `run`/`validate` parity, `--local`, `daemon`/`runs` commands, import guards. **Startup problem solved here.** |
| 5 | `step_05_events_and_streaming.md` | 2 — events | Event bus: checkpoint-diff publisher + SSE endpoint; SDK `callbacks` passthrough for fine-grained events; `goblin run --follow` |
| 6 | `step_06_notifications_service.md` | 3 — notifications | Daemon notification dispatcher (log/webhook/ntfy), run-lifecycle notifications, `on_failure.notify` bridge; HITL design note |
| 7 | `step_07_container_and_remote.md` | 4 — remote | Daemon image + compose; TCP+Bearer remote mode; CLI `--server`; docs |

Steps 1–4 are the V1 critical path (the split itself, solving the startup problem without
breaking anything). Step 5 unlocks live UX; step 6 fulfills the "N" in GOBLIN; step 7
makes the daemon deployable — the hand-off point to `prds/4 - k8s_operator`. Each step is
independently mergeable.

---

## 9. Sources

### External

- **[S1]** gRPC Python: https://grpc.io/docs/languages/python/ ,
  https://grpc.github.io/grpc/python/grpc_asyncio.html (stable `grpc.aio`, four streaming
  modes), UDS naming https://github.com/grpc/grpc/blob/master/doc/naming.md ;
  grpcio 1.83.0 measured: 18 MB installed, ~132 ms cold import, py3.13 wheels (no cp313t).
- **[S2]** msgpack ecosystem: https://pypi.org/pypi/msgpack-rpc-python/json (v0.4.1,
  2018-03-14, unmaintained); https://pypi.org/pypi/msgpack/json (v1.2.1, active; measured
  1.4 MB / 3.4 ms import).
- **[S3]** JSON-RPC: https://pypi.org/pypi/jsonrpcserver/json (5.0.9, 2022),
  https://pypi.org/pypi/jsonrpcclient/json (4.0.3, 2023); LSP's JSON-RPC-over-stdio with
  server notifications: https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/
- **[S4]** FastAPI https://fastapi.tiangolo.com ; uvicorn UDS support
  (`--uds`): https://github.com/Kludex/uvicorn (settings docs).
- **[S5]** Docker Engine API over `/var/run/docker.sock` (CLI↔daemon REST precedent):
  https://docs.docker.com/reference/api/engine/ . Jupyter kernel messaging (ZMQ+JSON):
  https://jupyter-client.readthedocs.io/en/stable/messaging.html
- **[S6]** kopf: https://kopf.readthedocs.io/en/stable/architecture/ (operators watch CRDs
  via aiohttp streams against the API server; reconcile model; status subresource
  writeback). Full kopf assessment lives in `prds/4 - k8s_operator`.
- **[S7]** SSE via `StreamingResponse`: FastAPI/Starlette docs (standard pattern).
- **[S8]** httpx: https://www.python-httpx.org (AsyncHTTPTransport `uds=` support);
  measured 2.3 MB installed, ~173 ms cold import (anyio/idna/certifi dominated).

### In-repo / installed (paths relative to repo root)

- **[L1]** `goblin/goblin/cli.py` — two commands; function-level heavy imports; no
  LazyGroup since relay removal.
- **[L2]** `goblin/goblin/hooks.py:5` (module-level `openhands.sdk.hooks` import),
  `goblin/goblin/validation.py:82` (reaches it from `validate`),
  `goblin/goblin/orchestrator.py:10` (module-level `goblin.hooks`).
- **[L3]** `goblin/goblin/runner.py` — `BlueprintRunner` (private `_run_id` at line 42,
  `run_dir=None → Path.cwd()`, `resume()` re-reads `blueprint_path`).
- **[L4]** `goblin/goblin/checkpoint.py`, `goblin/goblin/enums.py` — checkpoint JSON shape,
  FileLock + atomic writes, `RunStatus.INTERRUPTED` never written, `spend` persisted as
  repr string via `json.dumps(default=str)`.
- **[L5]** `goblin/goblin/orchestrator.py` — dependency waves, `on_failure.notify`
  callable lookup (lines 32–43), no event emitter; `goblin/goblin/blueprint.py`
  (`load_project` writes fixed global hook keys).
- **[L6]** `goblin/goblin/phase.py` — `PromptPhase.run` lazy SDK imports, `_run_acp`
  subprocess harness, `_acp_provider_key` reads `os.environ` directly.
- **[L7]** `goblin/goblin/conversation.py` — `run_conversation()` (no `callbacks`/
  `persistence_dir` passed); installed SDK 1.37.1
  `.venv/.../openhands/sdk/conversation/conversation.py` accepts `callbacks`.
- **[L8]** `goblin/goblin/workspace.py` — `build_workspace()` (module-level SDK import,
  local/remote).
- **[L9]** `goblin/goblin/utils/config.py` + `goblin-common/goblin_common/config.py` —
  env-driven frozen singleton, `load_dotenv_from_cwd()`; `goblin_common/logging.py`
  `configure_logging` replaces handlers; log root discovery falls back to cwd.
- **[L10]** `goblin/goblin/validation.py` — `validate_blueprint(path, raw=...)`, findings
  shape, `output_model` dotted-path `import_module`, `_cached_schema`.
- **[L11]** `goblin/goblin/swarm.py`, `goblin/goblin/swarm_db.py` — sync-sqlite exception,
  `asyncio.to_thread` wrapper, swarm workspace cwd default.
- **[L12]** `scripts/check_import_time.py` — 500 ms `goblin.cli` import guard, wired into
  root lint/test tasks; `goblin/Taskfile.yml`.
- **[L13]** Git history: `9c0452d` (lazy-import discipline), `f41b91e` (removed
  notifications-relay + `goblin notifications` + LazyGroup), `4419001` (abandoned Nuitka);
  `OPT.md`/`PLAN.md` (litellm ~140 providers root-cause; binary compilation postmortem).
- **[L14]** `ROADMAP.md`; `prds/agent_server_plan/README.md` + steps (0 % landed;
  run-service design absorbed here per §3.8).
- **[L15]** `prds/2 - crew_mode/README.md` (0 % landed; CLI-only crew surface).
- **[L16]** `AGENTS.md` — repo conventions (FastAPI, config/logging, aiosqlite `_migrate()`,
  testing rules, structured logging events).
- **[L17]** `spec/v1.json` + `goblin/goblin/spec/v1.json` (byte-identical package data);
  `examples/` blueprints; `goblin/tests/integration/test_golden_path.py` (VCR).
- **[L18]** Startup timings measured on this machine, 2026-07-27 (Python 3.13, warm cache):
  `import litellm` 7.81 s, `import openhands.sdk` 11.07 s, `import goblin.cli` 273 ms pure
  import / 0.46 s wall, `goblin --help` 0.47 s, `goblin validate examples/test.yaml`
  10.6–10.8 s, `from goblin.runner import BlueprintRunner` 0.51 s.
- **[L19]** `goblin/goblin/pyproject.toml` (dead `fastapi>=0.140.0` dep);
  `docs/index.md:14` (stale Telegram HITL bullet); `goblin/VISION.md`, root `README.md`
  (GOBLIN acronym "N"); `docs/advanced/custom-hooks.md` (notify example that silently
  never fires for `HookDefinition` entries).
- **[L20]** `prds/1 - acp_for_alt_harnesses/` + `docs/configuration/harness.md` +
  `goblin/tests/integration/test_acp_harness.py` (ACP fully landed).
