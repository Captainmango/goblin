# PRD: Goblin Agent Server

**Status:** Proposed
**Author:** Investigation + planning (AI-assisted)
**Date:** 2026-07-21
**Roadmap ref:** `ROADMAP.md` — "Agent Server (Remote runtime with all the GOBLIN stuff)" [L7]

---

## 1. Summary

Build a **Goblin Agent Server**: an ASGI service that runs goblin blueprints remotely —
submit a blueprint + context over HTTP, the server orchestrates the full run server-side
(no CLI connection required), and every phase conversation is visible in **OpenHands Agent
Canvas** through an embedded, **unmodified** OpenHands Agent Server.

The key decision of this PRD: **hand-roll a thin goblin-native service and *compose* the
stock OpenHands agent server as a library — do not fork either codebase.** The investigation
below shows the OpenHands agent server is conversation-centric and knows nothing about
blueprints, but it is pip-installable in a version that exactly matches goblin's SDK pin and
exposes a supported `create_app()` composition seam.

Deliverables: a new workspace member `goblin-server/`, a Docker image + compose service for
local hosting, Kubernetes manifests for remote hosting, and a V2 design note for scheduled
blueprint runs.

---

## 2. Problem statement

Today a blueprint run is a local, CLI-bound process:

- `uv run goblin run <blueprint>` executes in the caller's shell via
  `BlueprintRunner(path, run_dir=...)` → `await runner.run(context)` [L1].
- There is no way to submit a blueprint to a remote machine and disconnect.
- The only existing remote capability is per-blueprint `workspace: {type: remote, host: ...}`,
  which moves *agent tool execution* to a remote OpenHands agent server while orchestration
  (phases, dependency waves, retries, checkpoints) stays in the local CLI process [L2, L3].
- There is no run registry (a run's UUID is private to `BlueprintRunner` and only discoverable
  from a log line), and no cancellation (`RunStatus.INTERRUPTED` exists in
  `goblin/enums.py` but is never written) [L1, L4].
- OpenHands Agent Canvas — the control-center UI the user wants to use — can only see and
  manage backends that speak the OpenHands Agent Server HTTP/WebSocket API [S5, S6]. Goblin
  has no such API.

The user's requirements, restated:

1. Run blueprints remotely: submit blueprint YAML + context to a server; the server runs it
   to completion **entirely server-side**.
2. Feature parity with everything the goblin CLI can do locally (`run` incl. `--context`,
   `--resume`, `--run-dir`; `validate`; `notifications`).
3. Be visible to Agent Canvas as a backend.
4. Deployable locally via docker compose, and remotely in a k8s cluster.
5. (V2) Schedule blueprint runs.

---

## 3. Investigation

### 3.1 What the OpenHands Agent Server is (and isn't)

The OpenHands Agent Server (`openhands-agent-server`, source in
`OpenHands/software-agent-sdk` under `openhands-agent-server/openhands/agent_server`) is a
FastAPI REST/WebSocket server that wraps the **Software Agent SDK** [S1, S2]:

- **Conversations API** — CRUD for conversations, event search, and a WebSocket for live
  event streaming (`/api/conversations/...`, `/api/conversations/{id}/events/socket`) [S3].
- **Workspace APIs** — bash command execution (`/api/bash/...`), file upload/download
  (`/api/file/...`), git changes/diff (`/api/git/...`) [S2, S3, L6].
- **Platform APIs** — settings + secrets (`/api/settings`, encrypted at rest with
  `OH_SECRET_KEY`), skills, profiles, MCP, tools, server details (`/health`, `/ready`,
  `/server_info`) [S2, S3].
- **Auth** — optional session API keys sent as `X-Session-API-Key`
  (`OH_SESSION_API_KEYS_0`, `OH_SESSION_API_KEYS_1`, ... for rotation) [S2].
- **Runtime model** — it stores conversations and workspace files on local disk
  (`workspace/conversations/...`, `workspace/project/...`) and uses tmux for bash sessions
  [S3, S4].

What it is **not**: it has no concept of a *blueprint*, *phase*, *swarm*, *checkpoint*, or
*run*. Its unit of work is a single agent conversation. Goblin's orchestration layer
(`PhaseOrchestrator`, dependency waves, `foreach`/conditions, retries, cost circuit breakers,
structured outputs) lives entirely above the SDK and cannot be expressed as agent-server API
calls [L1, L8].

**Conclusion: the stock agent server cannot run goblin blueprints, and bolting blueprint
orchestration onto its conversation-centric internals would mean maintaining a fork of a
fast-moving upstream (25 PyPI releases between 1.24.0 and 1.36.1) [S3, S9].**

### 3.2 What Agent Canvas needs to "see" a backend

Agent Canvas is OpenHands' open-source "developer control center" UI. From its README and
docs [S5, S6]:

- A Canvas **backend** is "an agent server and the workspace it operates in".
- Registering a backend in **Manage Backends** requires exactly three things: a display name,
  a **host URL**, and an **API key** (the session API key).
- All conversations, settings, LLM config, MCP servers, and automations are then scoped to
  the selected backend.

So for the Goblin Agent Server to be visible to Canvas it must expose the agent-server
HTTP/WebSocket API surface under a URL + session key. The SDK's remote workspace code
(installed locally, `openhands-sdk 1.29.3`) shows exactly which endpoints a remote
conversation uses: `/api/conversations`, `/api/settings`, `/api/settings/secrets`,
`/api/profiles/{name}`, `/api/skills`, `/api/bash/*`, `/api/file/*`, `/api/git/*` [L6].

### 3.3 The composition seam that makes forking unnecessary

Three facts line up:

1. **Lockstep releases.** `openhands-agent-server` and `openhands-sdk` are released from the
   same monorepo in lockstep. PyPI has **both at `1.29.3`** — exactly goblin's pin
   (`openhands-sdk~=1.29.3`, `openhands-tools~=1.29.3`) [L5, S9].
2. **Importable app factory.** `openhands.agent_server.api.create_app(config: Config | None)
   -> FastAPI` exists at tag `v1.29.3` (verified against the tagged source) and wires up all
   routers, auth, CORS, and lifespan services (conversation service, bash service, tool
   preload) [S4].
3. **Goblin already speaks remote.** `goblin/conversation.py:run_conversation()` transparently
   uses `RemoteConversation` (over HTTP/WebSocket to an agent server) whenever the phase
   workspace is a `RemoteWorkspace`, and `goblin/workspace.py:build_workspace()` already
   builds one from `workspace: {type: remote, host, api_key, ...}` [L2, L3].

This means the Goblin Agent Server can:

- embed the stock agent server (as a dependency, not a fork) to satisfy Canvas, and
- orchestrate blueprint runs itself via `BlueprintRunner`, injecting
  `workspace: {type: remote, host: <loopback to the embedded runtime>}` into submitted runs
  so every phase conversation registers with the embedded runtime and **appears live in
  Agent Canvas** — while orchestration happens entirely server-side.

### 3.4 What goblin offers as integration points (verified in-repo)

| Concern | API | Location |
|---|---|---|
| Run a blueprint from a file | `BlueprintRunner(path, run_dir=...)` → `await runner.run(context) -> dict` | `goblin/goblin/runner.py` [L1] |
| Resume from checkpoint | `await BlueprintRunner.resume(checkpoint_path)` | `goblin/goblin/runner.py` [L1] |
| Run from an in-memory spec | `V1Blueprint(spec_dict)` → `await bp.run(ctx, checkpoint=None)` | `goblin/goblin/blueprint.py` [L8] |
| Validate | `validate_blueprint(path, raw=None) -> list[dict]` | `goblin/goblin/validation.py` |
| Run state | `Checkpoint` JSON at `<run_dir>/.goblin/run-<id>.json`; `Checkpoint.load(path)` | `goblin/goblin/checkpoint.py` [L4] |
| Status enums | `RunStatus` (incl. unused `INTERRUPTED`), `PhaseStatus` | `goblin/goblin/enums.py` [L4] |
| Remote workspace | `build_workspace()` → `RemoteWorkspace(host, working_dir, api_key, ...)` | `goblin/goblin/workspace.py` [L3] |
| Remote conversation | `run_conversation()` picks `RemoteConversation` for remote workspaces | `goblin/goblin/conversation.py` [L2] |
| Service style template | `create_app(config)` + lifespan + `app.state` + `require_api_key` Bearer auth | `notifications-relay/notifications_relay/main.py`, `api/deps.py` [L9] |
| Blueprint schema | JSON Schema draft-07 | `spec/v1.json` |

Gaps the server must fill (none exist today) [L1, L4]:

- HTTP submit/status/results/resume/cancel API and a run registry.
- Cancellation mechanism (the `INTERRUPTED` status exists but nothing sets it).
- `BlueprintRunner._run_id` is private — needs a public accessor for a registry.
- Blueprint-over-the-wire ingestion (`imports:` are file-path based — inline submissions must
  be restricted or resolved server-side).
- Concurrency control around runs; each run is long-lived (minutes–hours), so runs must be
  background asyncio tasks with a semaphore, not request/response.

### 3.5 CLI parity analysis

`goblin` CLI surface [L1, L10]:

| CLI command | Server equivalent |
|---|---|
| `goblin run <path> [--context JSON] [--run-dir DIR]` | `POST /api/runs` (blueprint by server path or inline YAML) |
| `goblin run --resume <checkpoint>` | `POST /api/runs/{id}/resume` |
| `goblin validate <paths...>` | `POST /api/blueprints/validate` |
| `goblin notifications <pair/send/ask/list/revoke>` | **Already remote** — talks to the notifications-relay over HTTP [L10]. No server work needed; the agent server simply needs `RELAY_*` env wiring so in-run notifications reach the relay. |

New capabilities the server adds beyond the CLI: run listing/status, results retrieval,
cancellation, event streaming, and (V2) scheduling.

---

## 4. Options analysis

### Option A — Fork `openhands/agent_server` and add blueprint endpoints

*This is what `ROADMAP.md` guessed at ("Probs just fork the existing server") [L7].*

- Pros: full control; single process.
- Cons: the fork must track a fast-moving upstream (12 minor releases in recent months) while
  goblin pins the SDK [S9]; blueprint orchestration is foreign to its conversation-centric
  services and storage; every upstream bugfix requires merge work; the ROADMAP note predates
  this investigation — the composition seam (§3.3) was not considered.
- **Verdict: rejected.** Composition achieves the same single-box result with zero fork
  maintenance.

### Option B — Hand-roll everything from scratch (reimplement the agent-server API)

- Pros: no upstream dependency.
- Cons: the Canvas-facing surface is large (conversations, events + WS, bash, file, git,
  settings/secrets, skills, profiles, MCP) [S3, S4]; reimplementing it faithfully is an
  ongoing compatibility burden and pure duplication of MIT-licensed code we can simply import.
- **Verdict: rejected** for the Canvas-facing surface. Hand-roll **only** the goblin-native
  blueprint API, which is small and uses established repo patterns.

### Option C — Compose: new `goblin-server` service + embedded stock agent server ✅

- A new workspace member `goblin-server/` (FastAPI, modeled on `notifications-relay`) hosts
  the blueprint/run API and orchestrates runs with `BlueprintRunner`.
- The stock `openhands-agent-server==1.29.3` (locked with the SDK pin) runs **embedded in the
  same container** as the "runtime", exposing the Canvas-facing API.
- Submitted runs get `workspace: {type: remote, host: <runtime loopback>}` injected so phase
  conversations appear in Canvas.
- Pros: no fork; Canvas compatibility inherited for free; blueprint logic stays in goblin's
  own code and conventions; each server keeps its native config/lifespan; mirrors the
  upstream Docker deployment model [S1, S3].
- Cons: two processes/ports to operate (mitigated: one container, one compose service, one
  k8s pod); version-pin discipline required (both packages must move together).
- **Verdict: selected.**

---

## 5. Target architecture (V1)

```
                        ┌───────────────────────────── container / pod ─────────────────────────────┐
                        │                                                                           │
  Agent Canvas ──URL+key──▶ runtime: OpenHands agent server (stock, unmodified)  :8101              │
  (Manage Backends)       │   /health /ready /server_info /api/conversations /api/bash /api/file …  │
                        │            ▲                                                              │
  goblin CLI / curl ──────┼──▶ goblin-server (new, FastAPI) :8100                                   │
  (Bearer key)            │      POST /api/runs ──▶ RunService ──▶ BlueprintRunner.run()            │
                        │      GET  /api/runs/{id}        │        workspace: {remote, host:        │
                        │      POST /api/runs/{id}/cancel │                  127.0.0.1:8101}        │
                        │      WS   /api/runs/{id}/events │                  (injected per run)     │
                        │      POST /api/blueprints/validate                 │                      │
                        │            │                     └─ RemoteConversation per phase ─────────┘
                        │            ▼ run registry (sqlite) + checkpoints (.goblin/run-<id>.json)
                        │   shared workspace volume (/workspace)  ◀── both processes read/write
                        └───────────────────────────────────────────────────────────────────────────┘
                                            │
                                            ▼  RELAY_* env
                              notifications-relay (existing service, :8000)
```

- **goblin-server** owns: blueprint ingestion + validation, run registry (aiosqlite, mirroring
  the relay's `store.py` pattern [L9]), background run tasks, cancellation, resume, run event
  streaming, Bearer auth (`require_api_key` pattern).
- **runtime** owns: everything Canvas sees. Conversations created by runs are ordinary
  agent-server conversations; Canvas streams their events over its normal WebSocket [S3, S5].
- **Shared workspace volume**: both processes must see the same filesystem (phase working
  dirs, `load_project` reading `.claude/settings.json` in the orchestrator process [L1]).
  Single-container-two-process gets this for free; k8s sidecars share an `emptyDir`/PVC.
- **Notifications**: in-run hooks/`AskUser`/`notify` reach the existing notifications-relay
  via standard `RELAY_HOST/PORT/RELAY_API_KEY` env [L10] — no new code.

### Request flow: submit and run a blueprint

1. `POST /api/runs` with `{blueprint_yaml | blueprint_path, context, run_id?}` → validated
   against `spec/v1.json` → `202 Accepted {run_id, status: "pending"}`.
2. `RunService` creates a run dir under `GOBLIN_SERVER_WORKSPACE_ROOT/<run_id>/`, materializes
   inline YAML to `blueprint.yaml` there, injects/overrides `blueprint.workspace` to point at
   the loopback runtime with a per-run `working_dir`, and starts an asyncio task running
   `BlueprintRunner(...).run(context)` (respecting a concurrency semaphore).
3. Checkpoints stream to `<run_dir>/.goblin/run-<run_id>.json` as today; the registry mirrors
   status for fast listing; a pub/sub feeds `WS /api/runs/{id}/events`.
4. Each phase's conversation registers with the runtime → visible and inspectable in Canvas.
5. `POST /api/runs/{id}/cancel` cancels the asyncio task and marks the checkpoint
   `interrupted` (requires the small goblin-core change in §6.3).

---

## 6. Requirements

### 6.1 Functional (V1)

| ID | Requirement |
|---|---|
| F1 | Submit a blueprint (inline YAML or server-side path) + JSON context; run executes fully server-side; client may disconnect. |
| F2 | Validate blueprint YAML without running it (equivalent to `goblin validate`). |
| F3 | List runs; get run status, phase statuses, and final results (checkpoint-sourced). |
| F4 | Resume a failed/interrupted run (equivalent to `goblin run --resume`). |
| F5 | Cancel a running run; checkpoint recorded as `interrupted`. |
| F6 | Stream run lifecycle events (run/phase started/completed/failed) over WebSocket or SSE. |
| F7 | Server registers with Agent Canvas as a standard backend (URL + session key); phase conversations of a run are visible in Canvas. |
| F8 | In-run notifications (hooks, `on_failure.notify`, AskUser) reach the notifications-relay. |
| F9 | Bearer-key auth on all goblin-server API endpoints; runtime protected by its session key. |
| F10 | Local hosting: single `docker compose up` brings up relay + agent server (per ROADMAP "Test with Compose set up" [L7]). |
| F11 | Remote hosting: k8s manifests (Deployment/Service/Secret/PVC/Ingress) deploy the same image. |

### 6.2 Non-functional

- Repo conventions per `CLAUDE.md`: FastAPI with thin routers delegating to `core/` services,
  pydantic models, `app.state` lifespan wiring, `goblin_common` config/logging, aiosqlite
  store with `_migrate()`, atomic file writes, tests with `httpx.ASGITransport` [L9, L11].
- Pin discipline: `openhands-agent-server` version must always equal the `openhands-sdk`
  minor (lockstep releases) [S9]; bumping one bumps both.
- Concurrency: default max 2 concurrent runs (`GOBLIN_SERVER_MAX_CONCURRENT_RUNS`), queue the
  rest; never block the event loop on `RemoteConversation.run` (already `asyncio.to_thread`
  in goblin [L2]).
- Secrets: never log API keys; `OH_SECRET_KEY` stable across restarts (else stored
  conversation secrets become undecryptable) [S2, S3].
- Runs are long-lived: all run endpoints are async/non-blocking; submit returns 202.

### 6.3 Required changes in existing packages (small, additive)

1. `goblin/goblin/runner.py`: expose `run_id` as a public read-only property.
2. `goblin/goblin/runner.py` / `orchestrator.py`: honor task cancellation — on
   `asyncio.CancelledError`, mark the checkpoint `RunStatus.INTERRUPTED` (enum already exists
   [L4]) and re-raise.
3. `goblin/pyproject.toml`: nothing (server deps live in the new package).
4. Root `pyproject.toml`: add `goblin-server` to workspace members; root `Taskfile.yml`:
   `goblin-server:lint/test/typecheck` tasks.
5. `infra/docker/base/Dockerfile`: `COPY goblin-server/pyproject.toml ./goblin-server/` so the
   dependency layer covers the new member.

### 6.4 Out of scope (V2+, see `step_08`)

- Scheduling (cron) and event/webhook triggers for blueprints (ROADMAP "eventbus" questions
  [L7]); issue-tracker consumption.
- `AgentSandbox` CRDs / "cattle vs pet" k8s operator work [L7].
- Multi-tenancy, per-user auth, horizontal run distribution across pods.

---

## 7. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Upstream agent-server API drift when bumping the SDK pin | Canvas compat breaks | Pins move in lockstep; spike test (`step_01`) asserts runtime endpoints respond; dependabot-style manual bumps. |
| Canvas expects absolute paths/URLs that break under path-prefix mounting | Canvas can't reach runtime | Avoid sub-path mounting in V1: runtime gets its own port (8101); revisit single-port ingress later. |
| Shared-filesystem requirement missed in k8s | `load_project`/worktree phases fail | Pod design shares a workspace volume between processes/containers; e2e test runs `load_project` blueprint remotely. |
| tmux missing in runtime image | bash tool fails in runtime | Add `tmux` to the runtime image (agent server uses a libtmux socket [S4]); compose healthcheck covers `/ready`. |
| Cancellation mid-conversation leaves remote conversation running | Cost/scope leak | Cancel path also calls the runtime conversation stop endpoint; document best-effort semantics. |
| Inline blueprint with `imports:` | File-path imports can't resolve | V1: reject inline blueprints declaring `imports:`; server-side path submissions resolve against a mounted blueprints dir. |
| `OH_SECRET_KEY` rotated | Stored conversation secrets lost | Generate once, store in k8s Secret / `.env`; document [S2]. |

---

## 8. Step plan

| Step | File | Goal |
|---|---|---|
| 1 | `step_01_spike_and_scaffold.md` | Verify pin compatibility; scaffold `goblin-server` workspace member |
| 2 | `step_02_run_service.md` | Run registry + orchestration core (submit/resume/cancel/events) |
| 3 | `step_03_http_api.md` | Blueprint/run HTTP + WS API with auth and tests |
| 4 | `step_04_runtime_and_canvas.md` | Embed stock agent server; workspace injection; Canvas smoke test |
| 5 | `step_05_docker_compose.md` | Image + compose service; e2e local deployment |
| 6 | `step_06_kubernetes.md` | k8s manifests for remote hosting |
| 7 | `step_07_cli_client_and_docs.md` | `goblin run --server` client; docs/AGENTS/ROADMAP updates |
| 8 | `step_08_v2_scheduling.md` | Design note: scheduled & triggered blueprint runs |

Steps 1–5 are the V1 critical path ("seen by Canvas, hosted via compose, runs blueprints
remotely"). Step 6 unlocks k8s; step 7 is polish; step 8 is V2.

---

## 9. Sources

### External

- **[S1]** OpenHands SDK docs — *Remote Agent Servers overview*:
  https://docs.openhands.dev/sdk/guides/agent-server/overview
  (what an agent server is; DockerWorkspace/APIRemoteWorkspace; RemoteConversation over
  HTTP/WebSocket; workspace file/command operations).
- **[S2]** OpenHands SDK docs — *Agent Server Package*:
  https://docs.openhands.dev/sdk/arch/agent-server
  (install; `python -m openhands.agent_server`; `OH_SESSION_API_KEYS_*` + `X-Session-API-Key`
  auth; `OH_SECRET_KEY` secret-encryption semantics; `/health`, `/ready`, `/server_info`;
  "such as an Agent Canvas backend" usage note).
- **[S3]** `OpenHands/software-agent-sdk` — `openhands-agent-server/openhands/agent_server`
  README and directory listing:
  https://github.com/OpenHands/software-agent-sdk/tree/main/openhands-agent-server/openhands/agent_server
  (routers/services inventory; conversations/events REST + WebSocket API; local file
  persistence layout; webhooks; config options).
- **[S4]** `openhands/agent_server/api.py` at tag `v1.29.3`:
  https://raw.githubusercontent.com/OpenHands/software-agent-sdk/v1.29.3/openhands-agent-server/openhands/agent_server/api.py
  (`create_app(config)` factory; `api_lifespan` services; router registration incl. tmux
  usage via `openhands.tools.terminal`). Also `pyproject.toml`:
  https://raw.githubusercontent.com/OpenHands/software-agent-sdk/main/openhands-agent-server/pyproject.toml
  (package name, entry point `agent-server`, dependency set).
- **[S5]** `OpenHands/agent-canvas` README:
  https://github.com/OpenHands/agent-canvas
  ("self-hosted developer control center"; backends = agent servers on a host/port; "Agent
  Canvas can connect to multiple Agent Servers"; paired Automation Server reference
  https://github.com/OpenHands/automation; quickstart images and ports).
- **[S6]** Agent Canvas docs — *Backends*:
  https://docs.openhands.dev/openhands/usage/agent-canvas/backends
  (a backend is "an agent server and the workspace it operates in"; Manage Backends stores
  display name, host URL, API key; settings/automations scoped per backend).
- **[S9]** PyPI release listings (lockstep verification):
  https://pypi.org/pypi/openhands-agent-server/json and https://pypi.org/pypi/openhands-sdk/json
  (both publish `1.29.3`; agent-server releases 1.24.0→1.36.1). Git tags `v1.29.0/v1.29.2/v1.29.3`
  confirmed via `git ls-remote` on the SDK repo.

### In-repo (paths relative to repo root)

- **[L1]** `goblin/goblin/runner.py` (`BlueprintRunner`, private `_run_id`, `resume`),
  `goblin/goblin/cli.py` (`run`/`validate`/`notifications`), `goblin/goblin/orchestrator.py`
  (dependency waves; no external cancel hook), `goblin/goblin/phase.py` (PromptPhase → SDK
  Agent + `run_conversation`), `goblin/goblin/blueprint.py` (`V1Blueprint`, hooks registry
  use), `goblin/goblin/import_resolver.py` (file-path imports).
- **[L2]** `goblin/goblin/conversation.py` — `run_conversation()`; `RemoteConversation` run
  via `asyncio.to_thread`.
- **[L3]** `goblin/goblin/workspace.py` — `build_workspace()` → `RemoteWorkspace(host, ...)`;
  docs: `docs/configuration/workspace.md`.
- **[L4]** `goblin/goblin/checkpoint.py`, `goblin/goblin/enums.py`
  (`RunStatus.INTERRUPTED` defined, never emitted; checkpoint JSON shape + atomic writes).
- **[L5]** `goblin/pyproject.toml` — `openhands-sdk~=1.29.3`, `openhands-tools~=1.29.3`;
  `uv.lock` (locked 1.29.3).
- **[L6]** Installed SDK 1.29.3 (`.venv/.../openhands/sdk/workspace/remote/base.py`,
  `remote_workspace_mixin.py`, `conversation/impl/remote_conversation.py`) — endpoints a
  remote conversation calls: `/api/conversations`, `/api/settings`, `/api/settings/secrets`,
  `/api/profiles/{name}`, `/api/skills`, `/api/bash/*`, `/api/file/*`, `/api/git/*`.
- **[L7]** `ROADMAP.md` — Agent Server, k8s, and eventbus line items.
- **[L8]** `goblin/goblin/blueprint.py`, `spec/v1.json` — blueprint schema; phases
  (prompt/load_project/swarm); `blueprints/` examples.
- **[L9]** `notifications-relay/` — service template: `main.py:create_app`, `api/deps.py`
  (`require_api_key`), `store.py` (aiosqlite `_migrate`), `tests/conftest.py` (ASGITransport
  pattern); `infra/docker/base/Dockerfile`, `infra/docker/notifications-relay/Dockerfile`,
  root `docker-compose.yml`.
- **[L10]** `goblin/goblin/notifications/` — relay client usage from the CLI.
- **[L11]** `CLAUDE.md` — repo conventions (FastAPI, config, logging, testing, errors).
