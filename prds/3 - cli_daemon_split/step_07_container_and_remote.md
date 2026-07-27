# Step 7 — Containerize the daemon + remote (TCP) mode

**Goal:** Package `goblin-daemon` as a container image, prove it with compose, and enable
**remote mode**: the same FastAPI app bound to TCP with Bearer auth instead of a unix
socket, so a CLI on one machine can drive a daemon on another. This is the deployment
bridge between the local split (steps 1–4) and the k8s operator (`prds/4 - k8s_operator`)
— and the point where the `agent_server_plan` PRD's hosting story is rebased onto the
daemon [L14].

**Depends on:** step 4 (steps 5–6 make remote mode more useful but aren't prerequisites).
**Blocks:** nothing in this PRD; enables `prds/4 - k8s_operator`.

---

## 7.1 Remote mode in the daemon (small, config-driven)

- `GOBLIN_DAEMON_HOST` + `GOBLIN_DAEMON_PORT` set → uvicorn binds TCP instead of UDS;
  `GOBLIN_DAEMON_TOKEN` becomes **required** (startup refuses otherwise) and is enforced
  by the existing `Authorization: Bearer` dependency from step 3 [L16].
- TLS: terminate outside the process (ingress/Caddy/sidecar) — document this; the daemon
  itself speaks plain HTTP on a private interface. In-cluster (the k8s operator PRD) the
  Service network is the boundary.
- **Remote-mode run semantics change, honestly documented:** `blueprint_path` submissions
  and client-cwd `run_dir` defaults only make sense on a shared filesystem. Remote
  submissions use `blueprint_yaml` (inline; `imports:` rejected as in step 3) and
  daemon-side `run_dir` under a configured workspace root. The CLI enforces this: remote
  + path submission → actionable error suggesting inline submission or a mounted
  blueprints directory (parity with the agent-server PRD's conclusion [L14]).
- Keep the per-project concept in remote mode via a required `X-Goblin-Project` header
  (defaults to the daemon's single configured project root when absent). True
  multi-project remote daemons are out of scope (README §4.5).

## 7.2 CLI: `--server` / env configuration

- `goblin --server http://host:8100 run ...` (or `GOBLIN_DAEMON_URL` +
  `GOBLIN_DAEMON_TOKEN` via `secret_env`-style handling — never logged [L16]) switches
  the step-2 client from UDS to TCP; auto-start and `daemon` lifecycle commands are
  local-only and skipped with a clear message when `--server` is used.
- Wire `--server` at the top-level group so every subcommand inherits it.
- `daemon status --server ...` works against remote daemons (health is public; still
  checks protocol version).

## 7.3 Image + compose

- `infra/docker/daemon/Dockerfile` (new): from the repo's `jp-goblin` base (uv/py3.13
  dependency layer [L14, L16]) — `uv sync --frozen` for `goblin-daemon` + `goblin` +
  `goblin-common`, entry `goblin-daemon serve --project-root /workspace
  --host 0.0.0.0 --port 8100`. Include `git` + `node`/`npx` (ACP harnesses and MCP stdio
  servers need them [L6]) and `tmux` only if the OpenHands runtime path needs it (verify;
  the agent-server PRD found the bash tool uses a libtmux socket [L14]).
- `infra/docker/compose.yaml` (new): `daemon` service — image above, `8100:8100`, env
  from `.env` (`GOBLIN_MODEL_API_KEY`, `GOBLIN_DEFAULT_MODEL`, `GOBLIN_DAEMON_TOKEN`),
  a named volume on `/workspace` (run dirs, checkpoints, registry), healthcheck hitting
  `/healthz`. Documented env contract: daemon-side env owns **all** secrets [L9].
- Root `Taskfile.yml`: `daemon:image` build task; `daemon:up`/`daemon:down` compose
  shortcuts.

## 7.4 Relationship to `prds/agent_server_plan` (act on it in this step)

- Update that PRD's README status header: steps 1–3 **superseded** by this PRD's steps
  1–4 + 7; steps 4–8 remain as follow-up work rebased onto `goblin-daemon`:
  - **Canvas composition** (its step 4): embed the stock `openhands-agent-server` in this
    same container/pod and inject `workspace: {type: remote, host: <loopback>}` into
    daemon runs — unchanged idea, new host process (the daemon instead of a separate
    `goblin-server`).
  - Its steps 5–6 (compose/k8s): realized here (compose) and in `prds/4 - k8s_operator`
    (k8s) in daemon-shaped form.
  - Its steps 7–8 (CLI client, scheduling): realized by step 4 here and the k8s operator
    PRD's scheduling design note respectively.

## 7.5 Tests + verification

- Remote-mode matrix (ASGI + a real TCP uvicorn fixture on `tmp_path`/`127.0.0.1:0`):
  token required (401 without), inline submit → run → events over SSE through the TCP
  boundary (heartbeats keep the stream alive), path submission rejected with the §7.1
  error.
- CLI: `--server` + env parity; lifecycle commands refuse gracefully against remote.
- Image: builds (`task daemon:image`); container boots; `docker compose up` + a real
  `goblin --server http://localhost:8100 validate` + a mocked-LLM run end-to-end from the
  host CLI (record as the step's e2e evidence; VCR where HTTP is involved [L16]).
- Security checks: token never logged (grep logs during tests); workspace volume files
  are daemon-uid owned; `/healthz` exposes no secrets.

## 7.6 Docs

- `docs/daemon.md`: remote mode section (semantics delta, auth, TLS-at-the-boundary).
- New `docs/deployment.md` (or `docs/advanced/remote-daemon.md`): image, compose, env
  contract, volume layout, upgrading (protocol version handshake behavior on skew).
- `ROADMAP.md`: tick "Agent Server — test with Compose set up" (daemon form).

## Acceptance criteria

- [ ] `docker compose up` brings up a healthy daemon; host CLI validates and runs a
      blueprint against it with measured submission latency recorded here.
- [ ] Token auth enforced on every non-health route; no secret leakage in logs.
- [ ] Remote semantics delta (inline-only, daemon-side run dirs) enforced and documented.
- [ ] `agent_server_plan` README status updated; `ROADMAP.md` updated.
- [ ] `task lint typecheck test` green across all three workspace members.
