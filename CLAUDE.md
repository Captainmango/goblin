# Goblin Monorepo Conventions

This is a uv-managed Python workspace with three projects:

- `goblin/` — autonomous agent harness that runs YAML **blueprints**.
- `goblin-common/` — shared config/logging utilities used by the other projects.
- `notifications-relay/` — FastAPI service and CLI for Telegram notifications.

All projects are workspace members in the root `pyproject.toml` and share a single `uv.lock`.

## Tooling

- **Python 3.13+** — specified in `.python-version` and every `pyproject.toml`.
- **uv** — package manager. Use `uv sync`, `uv run`, and `uv add`. Do not use pip.
- **Taskfile** — task runner. Run namespaced tasks from the repo root (e.g. `task goblin:test`).
- **ruff** — linter and formatter (line length 120, double quotes).
- **pyrefly** — type checker.
- **pytest / pytest-asyncio** — test framework.

## Workspace Commands

Run from the repo root:

```sh
task lint        # lint all projects
task format      # format all projects
task typecheck   # typecheck all projects
task test        # test all projects
task validate    # validate example blueprints
```

Target a single project with namespaced tasks:

```sh
task goblin:lint
task goblin:test
task goblin:validate
task goblin-common:test
task notifications-relay:test
```

## Code Conventions

### Python style

- Start every Python file with `from __future__ import annotations`.
- Use type hints everywhere; prefer PEP 604 union syntax (`str | None`).
- Target Python 3.13 features but keep syntax compatible with the shared `ruff.toml`.
- Keep lines to 120 characters.
- Use `is None` / `is not None` for optional checks.

### Types and data modeling

- Use **pydantic** for validation and serialization:
  - `pydantic.BaseModel` for API request/response models and parsed LLM outputs.
  - `pydantic.dataclasses.dataclass` for frozen configuration classes.
- Use the standard `dataclasses.dataclass` for simple mutable data containers.
- Prefer `Path` from `pathlib` over raw string paths.
- Model timestamps as ISO 8601 strings; use `datetime.now(timezone.utc)` when generating them.

### Async I/O

- I/O-bound code is async by default (database, HTTP, agent orchestration).
- Use `asyncio.Lock` for in-process synchronization.
- Provide async context managers (`__aenter__` / `__aexit__`) for resources that need cleanup, such as database connections and HTTP clients.
- Run blocking disk writes in `loop.run_in_executor` when called from async code.

### Configuration and secrets

- Load configuration from environment variables.
- Use `goblin_common.config.env` and `secret_env` instead of reading `os.environ` directly.
  - `env()` coerces values to the type of the default (`int`, `bool`, `str`).
  - `secret_env()` returns a pydantic `SecretStr` for sensitive values.
- `.env` files are loaded from the **current working directory only** via `load_dotenv_from_cwd`.
- Store configuration in frozen dataclass singletons with `load_config()` and `get_config()`.
- Provide `reset_config()` on config singletons so tests can re-evaluate environment variables.
- Keep secrets masked with `SecretStr` until they must be used; never log them.

### Logging

- Use `logging.getLogger(__name__)`; never `print()`.
- Configure loggers with `goblin_common.logging.configure_logging`.
- Default log files are written to `logs/<logger_name>.jsonl` under the workspace root.
- Include structured context with `extra={"event": "...", ...}`.
- Use `event` names with dot notation (e.g. `phase.failed`, `project.reference_loaded`).

### Errors

- Define project-specific exception hierarchies rooted in a single base exception (e.g. `RelayError`).
- Convert domain exceptions to transport-appropriate errors at the boundary:
  - FastAPI endpoints raise `HTTPException` with clear `detail` strings.
  - CLI commands raise `click.UsageError` or let exceptions propagate with a non-zero exit.
- Chain exceptions with `raise ... from exc` when wrapping an underlying cause.

### Databases and storage

- Use `aiosqlite` for SQLite access; keep schema creation in a `_migrate()` method.
- Exception: the swarm DB backends (`SQLiteSwarmDB` / `TursoSwarmDB`) use synchronous `sqlite3` / `pyturso` drivers because the OpenHands SDK tool executor runs on a worker thread pool. `SwarmPhase.run()` calls these sync methods directly from async orchestration code; this is a documented deviation to avoid async-driver complexity inside tool executors.
- Create parent directories with `exist_ok=True` before opening databases or writing files.
- Write files atomically when durability matters: write to a temp file, then `os.replace` or `Path.replace`.
- Use restrictive permissions (`0o600`) for files that contain tokens or keys.
- Treat row-level data as dictionaries via `aiosqlite.Row` when passing between store and service layers.

### HTTP APIs

- Build APIs with FastAPI.
- Keep endpoint functions thin; delegate to services in `core/`.
- Use FastAPI dependencies for shared concerns like authentication (`require_api_key`) and service lookup.
- Validate request and response shapes with pydantic models.
- Store initialized services on `app.state` during lifespan setup.

### CLIs

- Build CLIs with Click.
- Group related commands under a top-level group.
- Add global logging options (`--log-config`, `--log-level`, `--log-file`, `--quiet`) and wire them to `configure_logging`.
- Load configuration lazily inside commands, not at import time.

### Testing

- Use `pytest` and `pytest-asyncio`; mark async tests with `@pytest.mark.asyncio`.
- Put shared fixtures in `conftest.py`.
- Reset config singletons between tests with an `autouse` fixture.
- Use `tmp_path` for any test that touches the filesystem.
- Use `AsyncMock` and `MagicMock` to isolate units from external dependencies.
- Mock at the project boundary (step/phase, provider, HTTP client), not at internal SDK seams.
- Name test helpers consistently (`_make_blueprint`, `_make_client`, etc.).
- Keep tests focused: one behavior per test method, grouped into classes by feature.

## Environment Variables

| Variable | Project | Purpose |
|----------|---------|---------|
| `GOBLIN_MODEL_API_KEY` | goblin | LLM API key (falls back to `MODEL_API_KEY`). |
| `GOBLIN_BASE_URL` | goblin | LLM base URL (falls back to `OPENHANDS_BASE_URL`). |
| `GOBLIN_DEFAULT_MODEL` | goblin | Default LLM model when no model is specified in the blueprint. Required unless all phases/swarm agents declare a model. |
| `OPENHANDS_SUPPRESS_BANNER` | goblin | Suppress OpenHands startup banner (default: `1`). |
| `GOBLIN_BACKEND` | goblin | Swarm backend selection. |
| `GOBLIN_TURSO_URL` | goblin | Turso/libSQL URL for distributed swarm DB. |
| `GOBLIN_TURSO_AUTH_TOKEN` | goblin | Turso auth token. |
| `GOBLIN_TURSO_MODE` | goblin | Turso connection mode. |
| `GOBLIN_TURSO_LOCAL_PATH` | goblin | Local libSQL DB path. |
| `GOBLIN_LOG_CONFIG` | goblin | Path to YAML logging config. |
| `RELAY_API_KEY` | all | API key for notifications-relay HTTP API (required by server and clients). |
| `RELAY_HOST` | all | Relay host (default: `127.0.0.1`). |
| `RELAY_PORT` | all | Relay port (default: `8000`). |
| `RELAY_DB_PATH` | notifications-relay | SQLite DB path (default: `.goblin/relay.db`). |
| `RELAY_PAIRING_TOKEN_TTL_SECONDS` | notifications-relay | Pairing token TTL (default: `900`). |
| `RELAY_QUESTION_MAPPING_TTL_SECONDS` | notifications-relay | Question mapping TTL (default: `3600`). |
| `RELAY_LOG_LEVEL` / `RELAY_LOG_FILE` / `RELAY_LOG_QUIET` | notifications-relay | Logging overrides. |
| `TELEGRAM_BOT_TOKEN` | notifications-relay | Telegram bot token (required for Telegram). |
| `TELEGRAM_ALLOWED_USER_IDS` | notifications-relay | Comma-separated allowed Telegram user IDs. |

## Project Notes

### goblin

- Entry point: `uv run goblin run <blueprint>`.
- Validate blueprints: `uv run goblin validate <paths...>`.
- Blueprint prompt strings use `{variable}` interpolation from the run `context` dict.
- Blueprint template expressions (conditions, foreach) use Jinja2 `{{ }}` syntax.
- Hook names in blueprints must exist in `HOOK_REGISTRY` at runtime.
- Blueprint files live in `blueprints/` at the repo root.
- Checkpoint files are written to `.goblin/run-<id>.json` under the run directory.
- The `notifications` subcommand talks to the notifications-relay.

### notifications-relay

- Entry point: `uv run notifications-relay serve`.
- FastAPI app is created by `notifications_relay.main:create_app`.
- Telegram is only enabled when `TELEGRAM_BOT_TOKEN` is set.

### goblin-common

- Keep shared code here; avoid duplicating helpers across projects.
- `env()` coerces values to the type of their default (`int`, `bool`, etc.).
- `secret_env()` returns a pydantic `SecretStr`.
