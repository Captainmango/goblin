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
- **ruff** — linter and formatter.
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

- All runner, orchestration, and agent operations are **async**.
- Use `logging.getLogger(__name__)`; never `print()`. Include `extra={"event": "...", ...}` for structured context.
- Use `goblin_common.logging.configure_logging` and `goblin_common.config.env` / `secret_env` instead of duplicating config or logging code.
- Environment variables are loaded from a `.env` file in the current working directory only (`load_dotenv_from_cwd`).
- Secrets use `${VAR}` syntax in blueprints and are resolved from `os.environ` at runtime.
- Blueprint prompt strings use `{variable}` interpolation from the run `context` dict.
- Blueprint template expressions (conditions, foreach) use Jinja2 `{{ }}` syntax.
- Hook names in blueprints must exist in `HOOK_REGISTRY` at runtime.
- Blueprint files live in `blueprints/` at the repo root.
- Checkpoint files are written to `.goblin/run-<id>.json` under the run directory.

## Project Notes

### goblin

- Entry point: `uv run goblin run <blueprint>`.
- Validate blueprints: `uv run goblin validate <paths...>`.
- The `notifications` subcommand talks to the notifications-relay.

### notifications-relay

- Entry point: `uv run notifications-relay serve`.
- FastAPI app is created by `notifications_relay.main:create_app`.
- Telegram is only enabled when `TELEGRAM_BOT_TOKEN` is set.

### goblin-common

- Keep shared code here; avoid duplicating helpers across projects.
- `env()` coerces values to the type of their default (`int`, `bool`, etc.).
- `secret_env()` returns a pydantic `SecretStr`.
