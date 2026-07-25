# Step 4 — Embed the stock agent server ("runtime") + Agent Canvas visibility

**Goal:** Ship the Canvas-facing half of the box: run the **unmodified** OpenHands agent
server alongside goblin-server, and make blueprint runs register their phase conversations
with it so runs are visible in Agent Canvas.

**Depends on:** steps 1–3. **Blocks:** steps 5, 6.

---

## 4.1 Process model (V1): two processes, one box

- **goblin-server** (`uvicorn`, :8100) — the blueprint API from step 3.
- **runtime** (`python -m openhands.agent_server --host $GOBLIN_SERVER_RUNTIME_HOST
  --port $GOBLIN_SERVER_RUNTIME_PORT`, :8101) — the stock server from
  `openhands-agent-server==1.29.3` [S2, S4].

Rationale for two processes instead of mounting `create_app()` into our FastAPI app: the
runtime's `api_lifespan` is opinionated (tmux tmpdir setup/teardown, stale-session cleanup,
tool preload, bash retention tasks [S4]); keeping lifespans isolated avoids coupling our
startup to theirs, preserves their `/health`/`/ready` semantics, and matches upstream's own
container model [S1, S3]. A tiny supervisor entrypoint (step 5) runs both in one container;
both share the filesystem (workspace volume) by construction.

Do **not** mount the runtime under a path prefix in V1 — Canvas/SDK URL handling under a
prefix is an unneeded risk (see `README.md` §7).

## 4.2 Runtime configuration

Set on the box (compose `.env` / k8s Secret):

- `OH_SESSION_API_KEYS_0` — the key Canvas users paste into **Manage Backends** [S2, S6].
- `OH_SECRET_KEY` — stable random key; rotating it orphans stored conversation secrets [S2].
- `OH_ALLOW_CORS_ORIGINS_0` — only if a browser-based Canvas frontend calls the runtime
  directly [S2].
- Runtime state lives under `workspace/` in its CWD — point CWD at the shared workspace
  volume so conversations survive restarts.

## 4.3 Workspace injection: runs → Canvas-visible conversations

In `RunService` (step 2), before executing a submitted blueprint, rewrite its effective
workspace config:

```yaml
blueprint:
  workspace:
    type: remote
    host: "http://127.0.0.1:8101"        # GOBLIN_SERVER_RUNTIME_*
    api_key: "$OH_SESSION_API_KEYS_0"    # server-side only, never exposed to clients
    working_dir: "/workspace/runs/<run_id>"  # per-run dir on the shared volume
```

Rules:

- If the submitted blueprint declares no `workspace`, inject the above.
- If it declares `type: local`, override to the loopback remote (document: on the server,
  "local" means "on the box" — same thing from the user's perspective).
- If it declares `type: remote` with an explicit external `host`, honor it as-is
  (power users aiming phases at other runtimes; those conversations appear on *that*
  runtime's Canvas backend instead).
- Per-swarm `workspace` blocks get the same treatment (`swarm.py` builds its own workspaces
  [L1]).
- `working_dir` per run keeps concurrent runs' file state isolated on the shared volume.

Mechanism note: this is fully supported by existing goblin code —
`build_workspace()` builds the `RemoteWorkspace` [L3] and `run_conversation()` switches to
`RemoteConversation` automatically [L2]; the runtime then registers each phase conversation
in its store, which is exactly what Canvas lists and streams [S3, S5, S6].

## 4.4 Canvas smoke test (manual gate)

1. Start the box locally (both processes, no Docker needed yet).
2. Start Agent Canvas (`npx agent-canvas --frontend-only` or its Docker image [S5]).
3. **Manage Backends → Add**: name `goblin-local`, URL `http://localhost:8101`, API key
   `$OH_SESSION_API_KEYS_0` → backend shows healthy [S6].
4. `POST /api/runs` a cheap blueprint (2 prompt phases).
5. In Canvas: conversations appear as phases execute; events stream live; run completes;
   `GET /api/runs/{id}` shows `completed` with results.
6. (No built-in human-in-the-loop tool exists; mid-run approval is out of scope for V1.)

## 4.5 Cancellation follow-through

`POST /api/runs/{id}/cancel` cancels the orchestrator task (step 2). The phase's remote
conversation may still be running on the runtime: extend the cancel path to also call the
runtime's conversation stop/pause endpoint for the active conversation id (track it from the
`RemoteConversation` handle `run_conversation()` returns [L2]). Best-effort; document.

## Acceptance criteria

- [ ] Both processes start from one command; `/health` (goblin-server) and `/ready`
      (runtime) both pass.
- [ ] Submitted run's phases are visible as conversations in Canvas (screenshot/asciinema
      captured into this folder).
- [ ] `load_project` blueprint works end-to-end on the shared volume.
- [ ] Cancel stops orchestration and marks checkpoint `interrupted`; runtime conversation
      stop attempted.
- [ ] No modifications to any `openhands/*` package (verify: `git status` clean outside
      goblin/goblin-server/infra).
