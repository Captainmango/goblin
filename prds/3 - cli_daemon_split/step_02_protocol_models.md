# Step 2 — Wire protocol models + thin client transport

**Goal:** Define the versioned contract between CLI and daemon as pydantic models in
`goblin/goblin/protocol.py`, and the client transport in `goblin/goblin/client.py`. Both
modules are **import-light** (pydantic + httpx only) — they ship in the `goblin` package so
the daemon (which depends on `goblin`) and the CLI share one source of truth. No daemon
exists yet; this step is pure contract + transport with unit tests.

**Depends on:** step 1. **Blocks:** steps 3, 4.

---

## 2.1 `goblin/goblin/protocol.py` — the contract

Pure pydantic v2, `from __future__ import annotations`, no imports beyond stdlib +
pydantic. `PROTOCOL_VERSION: str = "1"` is the compatibility token (README F9).

```python
# --- health / handshake -----------------------------------------------------
class HealthResponse(BaseModel):
    status: Literal["ok"]
    protocol_version: str
    daemon_version: str          # importlib.metadata version of goblin-daemon
    project_root: str
    started_at: str              # ISO 8601 UTC
    active_runs: int

# --- validation -------------------------------------------------------------
class ValidateRequest(BaseModel):
    yaml: str                    # raw blueprint YAML text

class ValidationFinding(BaseModel):   # mirrors validation.py's finding dicts exactly [L10]
    path: str
    message: str
    validator: str
    severity: Literal["error", "warning"]

class ValidateResponse(BaseModel):
    findings: list[ValidationFinding]

# --- runs -------------------------------------------------------------------
class SubmitRunRequest(BaseModel):
    blueprint_path: str | None = None    # absolute path on the daemon's filesystem
    blueprint_yaml: str | None = None    # inline YAML (imports: rejected daemon-side)
    context: dict[str, Any] = Field(default_factory=dict)
    run_dir: str | None = None           # defaults to client cwd (CLI sends it explicitly)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> SubmitRunRequest: ...

class PhaseRecord(BaseModel):           # normalized from the checkpoint [L4]
    status: Literal["running", "completed", "failed", "skipped"]
    input: dict[str, Any] | None
    output: dict[str, Any] | None       # spend is a dict here (step 1.3.3), never a repr
    started_at: str | None
    completed_at: str | None

class RunRecord(BaseModel):
    run_id: str
    status: Literal["pending", "in_progress", "completed", "failed", "interrupted"]
    blueprint_name: str | None
    blueprint_source: Literal["path", "inline"]
    run_dir: str
    checkpoint_path: str | None
    context: dict[str, Any]
    phases: dict[str, PhaseRecord]      # checkpoint-sourced, normalized
    error: str | None
    created_at: str
    started_at: str | None
    completed_at: str | None

class SubmitRunResponse(BaseModel):     # always 202 Accepted
    run_id: str
    status: Literal["pending"]

class RunListResponse(BaseModel):
    runs: list[RunRecord]

# --- events (defined now, wired in step 5) ----------------------------------
class RunEvent(BaseModel):
    event: str                   # dot-notation: run.started, run.phase_completed, ...
    run_id: str
    phase_id: str | None = None
    ts: str
    payload: dict[str, Any] = Field(default_factory=dict)
```

Rules:

- Field names/shapes are **frozen once used by either side**; evolution is additive-only
  with defaults, or bump `PROTOCOL_VERSION`. A CI test round-trips every model with
  `model_dump`/`model_validate` and asserts no field renames against a checked-in JSON
  snapshot of each model's schema (cheap drift alarm).
- `RunRecord.status` deliberately reuses `RunStatus` values plus `pending` (queued behind
  the daemon semaphore) [L4].
- Context may contain arbitrary JSON; the daemon rejects non-JSON-serializable values at
  the boundary with a clear 422 (they'd explode later in checkpoint writes anyway [L4]).

## 2.2 `goblin/goblin/client.py` — transport

```python
class DaemonError(click.ClickException): ...          # base; CLI boundary converts [L16]
class DaemonUnavailable(DaemonError): ...
class ProtocolMismatch(DaemonError): ...

class DaemonClient:
    def __init__(self, socket_path: Path | None = None,
                 base_url: str | None = None,        # remote mode (step 7)
                 token: str | None = None,
                 timeout: float = 30.0): ...
    async def health(self) -> HealthResponse                    # checks protocol_version
    async def validate(self, yaml_text: str) -> ValidateResponse
    async def submit_run(self, req: SubmitRunRequest) -> SubmitRunResponse
    async def get_run(self, run_id: str) -> RunRecord
    async def list_runs(self, status: str | None = None, limit: int = 20) -> RunListResponse
    async def resume_run(self, run_id: str) -> SubmitRunResponse
    async def cancel_run(self, run_id: str) -> RunRecord
    async def stream_events(self, run_id: str) -> AsyncIterator[RunEvent]   # step 5
```

- **Transport selection:** local → `httpx.AsyncHTTPTransport(uds=str(socket_path))` with
  base URL `http://localhost`; remote (step 7) → plain base URL + `Authorization: Bearer`.
  Local auth = filesystem trust (socket dir `0700`, socket `0600`); a token is accepted
  but not required locally.
- **Handshake:** `health()` compares `protocol_version` against
  `protocol.PROTOCOL_VERSION`; mismatch → `ProtocolMismatch` with the actionable message
  ("daemon runs an incompatible version; `goblin daemon stop` and retry").
- **Timeouts/retries:** connect timeout 5 s, read timeout 30 s (runs are async — 202
  returns immediately); no automatic retries for submit (a retry could double-submit —
  idempotency via client-supplied `Idempotency-Key` header is a step-3 server concern).
- **Discovery** (`goblin/goblin/daemon_discovery.py`, also light): walk up from cwd for a
  `.goblin/` dir (mirroring today's project intuition), else cwd itself; socket at
  `<root>/.goblin/daemon.sock`, overridable with `GOBLIN_DAEMON_SOCKET` [L9 conventions].
- Async context manager (`__aenter__/__aexit__`) owns the httpx client per repo
  conventions [L16].

## 2.3 Import-budget guard (extend `scripts/check_import_time.py`)

Add checks, wired into the existing `task goblin:check-imports` [L12]:

1. `import goblin.protocol` and `import goblin.client` each < 500 ms combined with
   `goblin.cli`.
2. After importing `goblin.cli`, `goblin.protocol`, `goblin.client`: assert
   `openhands`, `litellm`, and `fastapi` are **absent** from `sys.modules`. This test is
   the permanent guard against the thin client re-fattening.

## 2.4 Tests (`goblin/tests/unit/test_protocol.py`, `test_client.py`)

- Model round-trips + schema snapshot drift test (§2.1).
- `SubmitRunRequest` validator: exactly one of path/yaml; both/neither rejected.
- Client against a stub ASGI app (a hand-rolled Starlette/FastAPI test double is fine —
  it's dev-dependency code, not a CLI import) using `httpx.ASGITransport` **and** a real
  uvicorn-on-UDS fixture on `tmp_path` to prove socket transport works end-to-end [S4, S8].
- `ProtocolMismatch` raised on version disagreement; `DaemonUnavailable` on missing
  socket.
- Non-JSON-serializable `context` → 422 surfaced as `DaemonError` with a readable message.

## Acceptance criteria

- [ ] `goblin/protocol.py` + `goblin/client.py` + discovery module landed, fully typed
      (pyrefly green).
- [ ] §2.3 guards pass in CI; measured import times recorded in this file.
- [ ] Unit tests green (`task goblin:test`); no existing file changed except
      `scripts/check_import_time.py` and `goblin/pyproject.toml` (`httpx` added).
