# Step 1 — Spike: pin compatibility + scaffold `goblin-server`

**Goal:** Prove the core technical assumption (stock OpenHands agent server composes with
goblin's pinned SDK) and land an empty but fully-wired `goblin-server` workspace member.

**Depends on:** nothing. **Blocks:** steps 2–5.

---

## 1.1 Spike: verify the composition seam

Before writing the service, verify against the actual pinned versions:

1. Add to a scratch env (or temporarily to the workspace): `openhands-agent-server==1.29.3`
   and confirm `uv lock` resolves with `openhands-sdk==1.29.3` (already locked) [S9].
2. Smoke-test the factory in-process:
   ```python
   from openhands.agent_server.api import create_app
   app = create_app()  # must import and build without error [S4]
   ```
3. Boot it (`uv run python -m openhands.agent_server --host 127.0.0.1 --port 8101`) and
   assert the endpoints the SDK's `RemoteWorkspace` calls actually respond [S2, L6]:
   - `GET /health` → 200
   - `GET /server_info` → 200
   - `GET /api/conversations/count` → 401 without `X-Session-API-Key`, 200 with it
     (set `OH_SESSION_API_KEYS_0` and `OH_SECRET_KEY` first).
4. Run one remote conversation through the SDK from the repo venv (mirrors
   `goblin/conversation.py`'s remote path): build an `Agent` with `build_llm(...)`, a
   `RemoteWorkspace(host="http://127.0.0.1:8101", api_key=..., working_dir=...)`, call
   `run_conversation(...)`, and assert the conversation appears in
   `GET /api/conversations/...`. This is the exact mechanism step 4 relies on.

**Spike exit criteria:** items 1–4 pass with zero changes to upstream code. If the factory or
endpoints differ at 1.29.3 vs. the documented surface, record the delta in this file and
re-evaluate Option A/B from `README.md` §4 before proceeding.

## 1.2 Scaffold the workspace member

Create `goblin-server/` following the repo's FastAPI conventions [L11]:

```
goblin-server/
├── pyproject.toml          # name "goblin-server", deps: goblin, goblin-common,
│                           # fastapi, uvicorn[standard], aiosqlite, httpx, pydantic,
│                           # openhands-agent-server==1.29.3 (see §1.1)
├── README.md
├── goblin_server/
│   ├── __init__.py
│   ├── cli.py              # Click group; `serve` command; global logging options
│   ├── config.py           # frozen dataclass singleton: load_config/get_config/reset_config
│   ├── main.py             # create_app(config) -> FastAPI, lifespan, app.state
│   ├── api/
│   │   ├── __init__.py
│   │   ├── deps.py         # require_api_key (Bearer), service getters
│   │   └── health.py       # GET /health (public)
│   └── core/
│       └── __init__.py
└── tests/
    ├── conftest.py         # create_app(config with tmp_path) + ASGITransport client [L9]
    └── test_health.py
```

Config (env-driven, `goblin_common.config.env`/`secret_env`):

| Variable | Default | Purpose |
|---|---|---|
| `GOBLIN_SERVER_API_KEY` | (required) | Bearer key for the goblin API |
| `GOBLIN_SERVER_HOST` | `127.0.0.1` | Bind host |
| `GOBLIN_SERVER_PORT` | `8100` | Bind port |
| `GOBLIN_SERVER_DB_PATH` | `.goblin/agent-server.db` | Run registry sqlite |
| `GOBLIN_SERVER_WORKSPACE_ROOT` | `.goblin/server` | Per-run dirs, materialized blueprints |
| `GOBLIN_SERVER_MAX_CONCURRENT_RUNS` | `2` | Run semaphore |
| `GOBLIN_SERVER_RUNTIME_HOST` | `127.0.0.1` | Embedded runtime bind host |
| `GOBLIN_SERVER_RUNTIME_PORT` | `8101` | Embedded runtime port |

Also required at runtime (existing goblin vars): `GOBLIN_MODEL_API_KEY`,
`GOBLIN_DEFAULT_MODEL`, and for the runtime `OH_SESSION_API_KEYS_0`,
`OH_SECRET_KEY` [L5, S2].

Wire the member in:

- Root `pyproject.toml`: add `"goblin-server"` to `[tool.uv.workspace] members`.
- Root `Taskfile.yml`: `goblin-server:lint`, `goblin-server:test`, `goblin-server:typecheck`
  (copy the `goblin-common` task shapes).
- `infra/docker/base/Dockerfile`: `COPY goblin-server/pyproject.toml ./goblin-server/`.
- `uv sync` and confirm the lock includes `openhands-agent-server==1.29.3`.

## Acceptance criteria

- [ ] Spike §1.1 items 1–4 pass; findings recorded.
- [ ] `task goblin-server:lint goblin-server:typecheck goblin-server:test` all green.
- [ ] `uv run goblin-server serve` starts; `curl localhost:8100/health` → 200; all other
      endpoints 401 without Bearer key.
- [ ] `uv.lock` contains `openhands-agent-server==1.29.3` alongside `openhands-sdk==1.29.3`.
