# Step 5 — Docker image + docker compose (local hosting)

**Goal:** One `docker compose up` brings up the Goblin Agent Server, satisfying the
ROADMAP's "Test with Compose set up" [L7].

**Depends on:** step 4. **Blocks:** step 6.

---

## 5.1 Image — `infra/docker/agent-server/Dockerfile`

Follow the established two-stage pattern (`jp-goblin` dep base → wheel layer → slim runtime)
used by the repo's Docker conventions [L11]:

- **Builder** (`FROM jp-goblin:latest`): copy `goblin-common`, `goblin`, and `goblin-server`
  sources + READMEs; `uv build --wheel` each; `uv pip install --no-deps /dist/*.whl` into the
  prepared venv.
- **Runtime** (`FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim`): copy `.venv`; install
  runtime OS deps — **`tmux` is required** (the embedded agent server's bash service uses the
  libtmux socket [S4]); keep `libffi8`, `libssl3`.
- **Supervisor entrypoint** (`infra/docker/agent-server/entrypoint.sh`): start
  `python -m openhands.agent_server --host 0.0.0.0 --port 8101` in the background from
  `/workspace` (so runtime state lands on the volume), then `exec goblin-server serve`
  in the foreground; `trap` to forward SIGTERM to both. Health endpoints stay separate.
- Rebuild the base image first (`task base:build`) — step 1 added `goblin-server/pyproject.toml`
  to its COPY set.

## 5.2 Compose service — root `docker-compose.yml`

Add the agent-server service:

```yaml
  agent-server:
    container_name: goblin_agent_server
    build:
      context: .
      dockerfile: ./infra/docker/agent-server/Dockerfile
    restart: unless-stopped
    volumes:
      - agent_workspace:/workspace          # runtime state + run dirs + checkpoints
      - ./blueprints:/blueprints:ro         # server-side blueprint submissions
    environment:
      GOBLIN_SERVER_API_KEY: ${GOBLIN_SERVER_API_KEY}
      GOBLIN_SERVER_WORKSPACE_ROOT: /workspace/runs
      GOBLIN_SERVER_BLUEPRINTS_DIR: /blueprints
      GOBLIN_SERVER_RUNTIME_HOST: 127.0.0.1
      GOBLIN_SERVER_RUNTIME_PORT: "8101"
      OH_SESSION_API_KEYS_0: ${OH_SESSION_API_KEYS_0}
      OH_SECRET_KEY: ${OH_SECRET_KEY}
      GOBLIN_MODEL_API_KEY: ${GOBLIN_MODEL_API_KEY}
      GOBLIN_DEFAULT_MODEL: ${GOBLIN_DEFAULT_MODEL}
    ports:
      - "8100:8100"   # goblin blueprint API (Bearer)
      - "8101:8101"   # runtime (Canvas backend, X-Session-API-Key)
    healthcheck:
      test: ["CMD", "curl", "-f", "http://127.0.0.1:8100/health"]
      interval: 20s
      timeout: 10s
      retries: 5
      start_period: 15s
    networks:
      - goblin

volumes:
  agent_workspace:
```

- Document required `.env` additions: `GOBLIN_SERVER_API_KEY`, `OH_SESSION_API_KEYS_0`,
  `OH_SECRET_KEY` (plus existing model keys).

## 5.3 E2E verification (compose)

1. `task base:build && docker compose build && docker compose up -d`
2. Both containers healthy; runtime `curl -H "X-Session-API-Key: $OH_SESSION_API_KEYS_0"
   localhost:8101/api/conversations/count` → 200.
3. Submit `blueprints/test.yaml` by server path (`/blueprints/test.yaml`) and an inline
   blueprint via `POST /api/runs`; poll to `completed`; fetch results.
4. Canvas → Manage Backends → `http://localhost:8101` + key → conversations visible (repeat
   the step-4 smoke test against the containerized box).
5. `docker compose restart agent-server` → registry + checkpoints + runtime conversations
   survive (volume-backed).
6. (No built-in human-in-the-loop tool exists; mid-run approval is out of scope for V1.)

## Acceptance criteria

- [ ] Single `docker compose up` gives a working box (checks 2–6 pass).
- [ ] Image rebuilds are incremental (base image untouched unless deps change).
- [ ] Compose file validates (`docker compose config -q`) and is documented in this folder.
