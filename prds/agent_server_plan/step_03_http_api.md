# Step 3 — HTTP API: blueprints, runs, events

**Goal:** Expose the run service over REST + WebSocket with auth, pydantic contracts, and
full API tests. This is the surface that achieves CLI parity (`run`, `validate`, `resume`)
plus server-only capabilities (list/status/cancel/stream).

**Depends on:** step 2. **Blocks:** steps 4, 5, 7.

---

## 3.1 Endpoints

All under `/api`, all behind `require_api_key` (Bearer, copied from
`notifications_relay/api/deps.py` [L9]) except `GET /health`.

| Method & path | Request → Response | Notes |
|---|---|---|
| `POST /api/blueprints/validate` | `{blueprint_yaml}` → `{valid: bool, errors: list}` | Wraps `validate_blueprint(raw=...)` + `format_errors` [L1]; parity with `goblin validate`. |
| `POST /api/runs` | `RunSubmission` → **202** `{run_id, status: "pending"}` | `RunSubmission = {blueprint_yaml? , blueprint_path?, context: dict = {}}` (exactly one source). |
| `GET /api/runs` | → `{runs: [RunRecord...]}` | Query params: `status`, `limit`, `offset`. |
| `GET /api/runs/{run_id}` | → `RunDetail` | Registry row + checkpoint phase map (`status/input/output/started_at/completed_at` per phase [L4]) + `spend` totals when present. |
| `GET /api/runs/{run_id}/results` | → checkpoint `get_results()` dict | 409 while run not terminal. |
| `POST /api/runs/{run_id}/resume` | → **202** `{run_id, status: "pending"}` | Parity with `goblin run --resume`. |
| `POST /api/runs/{run_id}/cancel` | → `{run_id, status: "interrupted"}` | 409 on terminal runs. |
| `WS /api/runs/{run_id}/events` | streams event envelopes (step 2 §2.3) | Replays backlog from checkpoint on connect, then live. |

Thin routers delegating to `RunService`; domain errors → `HTTPException` with clear `detail`
(`raise ... from exc`) per repo conventions [L11]. Blueprint-not-found vs. run-not-found vs.
invalid-state must be distinguishable (404 vs 409).

## 3.2 Pydantic contracts — `goblin_server/core/models.py`

`RunSubmission`, `RunRecord`, `RunDetail`, `ValidationReport`, event envelope models. Follow
existing repo modeling rules (BaseModel for API shapes; ISO-8601 timestamp strings) [L11].

## 3.3 Auth & config

- `Authorization: Bearer $GOBLIN_SERVER_API_KEY`; 401 with `detail` on mismatch (constant-time
  compare, as in the relay).
- The runtime (step 4) keeps its **own** auth (`X-Session-API-Key`); the two schemes stay
  separate in V1 — document this clearly so operators set both keys.

## 3.4 Tests (httpx ASGI transport pattern [L9])

- Auth: 401 without/with wrong key; 200 with key.
- Validate endpoint: valid blueprint, schema violation, Jinja/hook-name errors surface.
- Submit → poll status → results (fake runner from step 2); error mapping for bad
  submissions (both sources set, neither set, inline with `imports:`, path outside
  blueprints dir → 4xx).
- Cancel flow: 200 while running; 409 when terminal.
- Resume flow on a failed run.
- WS events: connect, receive backlog + live `run.completed`.

## Acceptance criteria

- [ ] Full matrix above green under `task goblin-server:test`.
- [ ] `uv run goblin-server serve` + curl walkthrough of every endpoint works against a real
      (single-prompt) blueprint.
- [ ] OpenAPI docs at `/docs` render the whole surface.
