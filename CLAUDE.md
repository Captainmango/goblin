# Goblin Monorepo

This repository is a uv-managed monorepo containing two Python projects:

- **`goblin/`** — the autonomous coding agent harness that runs high-level definitions called **blueprints** (YAML files). The runner parses a blueprint, orchestrates phase execution via the OpenHands SDK, and applies lifecycle hooks around tool calls.
- **`notifications-relay/`** — notification relay service/CLI for the ecosystem.

All projects are declared as workspace members in the root `pyproject.toml` and share a single root `uv.lock`.

## Project Layout

```
jp-goblin/
├── pyproject.toml          # Workspace manifest only
├── uv.lock                 # Shared lockfile for the whole workspace
├── Taskfile.yml            # Root tasks delegate to each project
├── .python-version
├── .gitignore
├── ruff.toml               # Shared ruff config used by all projects
├── CLAUDE.md               # This file
├── AGENTS.md               # Pointer to this file for agent tooling
├── goblin/                 # Goblin runtime CLI project
│   ├── pyproject.toml
│   ├── Taskfile.yml
│   ├── README.md
│   ├── VISION.md
│   ├── ROADMAP.md
│   ├── install.sh
│   ├── goblin/
│   │   ├── __init__.py
│   │   ├── blueprint.py
│   │   ├── orchestrator.py
│   │   ├── dependency_graph.py
│   │   ├── checkpoint.py
│   │   ├── cost_control.py
│   │   ├── conversation.py
│   │   ├── import_resolver.py
│   │   ├── validation.py
│   │   ├── workspace.py
│   │   ├── enums.py
│   │   ├── hooks.py
│   │   ├── logging.py
│   │   ├── prompts.py
│   │   ├── cli.py
│   │   ├── runner.py
│   │   ├── phase.py
│   │   ├── swarm.py
│   │   ├── swarm_db.py
│   │   ├── tools/
│   │   └── utils/
│   ├── spec/
│   │   └── v1.json
│   └── tests/
└── notifications-relay/    # Notification relay project
    ├── pyproject.toml
    ├── Taskfile.yml
    ├── README.md
    ├── notifications_relay/
    └── tests/
```

## Goblin Project

Goblin is an autonomous coding agent harness that runs high-level definitions called **blueprints** (YAML files). The runner parses a blueprint, orchestrates phase execution via the OpenHands SDK, and applies lifecycle hooks around tool calls.

### Tooling

- **Python 3.13+** — specified in `.python-version` and each `pyproject.toml` `requires-python`
- **uv** — package manager; use `uv run`, `uv add`, `uv sync` (never pip)
- **Taskfile** — task runner; use `task goblin:lint`, `task goblin:format`, etc.
- **openhands-sdk** / **openhands-tools** — agent orchestration
- **click** — CLI framework
- **jinja2** — template expressions in conditions and foreach
- **pydantic** — response validation (PhaseResult model)
- **pyyaml** — YAML parsing for blueprints
- **jsonschema** — blueprint schema validation (`goblin validate`, `output_schema`)
- **filelock** — cross-process locking for checkpoint writes
- **ruff** — linter and formatter (dev)
- **pyrefly** — type checker (dev)
- **pytest / pytest-asyncio** — test framework (dev)
- **dotenv** — dev dependency for loading `.env`

### Conventions

- All runner and agent operations are **async**
- Prompts support `{variable}` interpolation (Python `.format()`) from the `context` dict passed to `run()`
- Phase template expressions use `{{ }}` Jinja2 syntax for conditions and foreach
- Hook names in blueprints are strings that must exist in `HOOK_REGISTRY` at runtime
- Blueprint files live in `goblin/blueprints/`
- MCP server secrets use `${VAR}` syntax resolved from `os.environ` (loaded via `.env` / `load_dotenv()`)
- All runtime messages use `logging.getLogger(__name__)`, never `print()`
- Log calls include `extra={"event": "...", ...}` for structured context

## Commands

Run commands from the repo root. The root `Taskfile.yml` delegates to each project.

- `task lint` — Run linting for all projects
- `task format` — Run formatting for all projects
- `task typecheck` — Run typechecking for all projects
- `task test` — Run tests for all projects
- `task validate` — Validate Goblin example blueprints

Target a single project with namespaced tasks:

- `task goblin:lint`
- `task goblin:format`
- `task goblin:typecheck`
- `task goblin:test`
- `task goblin:validate`
- `task notifications-relay:lint`
- `task notifications-relay:test`

Or run tasks directly inside a project directory:

```sh
cd goblin
task lint
```

## Built-in tools

Phases whitelist tools via `allowed_tools`. Besides the OpenHands file-editor, terminal, `Glob`, and `Grep` tools, Goblin registers:

- `SwarmTasks` / `SwarmAgent` — swarm coordination database tools (see `goblin/tools/swarmdb_tool.py` and `goblin/tools/swarm_agent_tool.py`).
- `Worktree` — typed git worktree operations (`list`, `create`, `remove`, `prune`, `link_deps`).

Keys:

- `base_dir` — directory under which all managed worktrees are created (relative to the project root). Default: `.goblin/worktrees`.
- `deps` — list of dependency directory names that may be symlinked from the main worktree. There is no built-in default; users must opt in explicitly.
- `auto_link` — whether `create` automatically symlinks the configured `deps` after creating a worktree. Default: `false`.

If `Worktree` appears in `allowed_tools` but no `worktree` config is defined, the phase fails fast with a clear error.
