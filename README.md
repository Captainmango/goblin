# G.O.B.L.I.N. Monorepo

Generative Orchestration of Basic Logic, Instructions, and Notifications. An Agent Factory facilitating ensemble programming.

This repository is a uv-managed Python workspace containing two projects:

- **`goblin/`** — the autonomous agent harness. It runs YAML **blueprints** that define multi-step agent workflows and executes them via the OpenHands SDK.
- **`goblin-common/`** — shared utilities for configuration, environment variables, and structured JSON logging. Used by every other project.

## Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/)
- [Taskfile](https://taskfile.dev/) (optional, for convenience tasks)

## Setup

Clone the repository and install all workspace dependencies:

```sh
uv sync --all-packages --all-groups
```

This creates a single virtual environment and installs every workspace package.

## Running the Projects

### goblin

Run a blueprint:

```sh
uv run goblin run blueprints/test.yaml
```

Pass context variables as JSON:

```sh
uv run goblin run blueprints/test.yaml --context '{"repo": "https://github.com/example/repo.git"}'
```

Validate one or more blueprints:

```sh
uv run goblin validate blueprints/*.yaml
```

Resume a previous run from a checkpoint:

```sh
uv run goblin run --resume .goblin/run-<id>.json
```

### goblin-common

`goblin-common` is a library; it has no standalone entry point. Import its helpers in the other projects:

```python
from goblin_common.config import env, secret_env, load_dotenv_from_cwd
from goblin_common.logging import configure_logging
```

## Environment Variables

Create a `.env` file in the working directory where you run commands. The projects load environment variables from `.env` in the current working directory only.

### Required

| Variable | Used By | Purpose |
|----------|---------|---------|
| `GOBLIN_MODEL_API_KEY` | goblin | LLM API key. Falls back to `MODEL_API_KEY` if not set. Required when running blueprints that use an LLM. |

### Optional

| Variable | Used By | Default | Purpose |
|----------|---------|---------|---------|
| `GOBLIN_BASE_URL` | goblin | `OPENHANDS_BASE_URL` | Base URL for the LLM API. |
| `OPENHANDS_SUPPRESS_BANNER` | goblin | `1` | Suppress the OpenHands startup banner. |
| `GOBLIN_BACKEND` | goblin | — | Swarm backend selection. |
| `GOBLIN_TURSO_URL` | goblin | — | Turso/libSQL URL for distributed swarm state. |
| `GOBLIN_TURSO_AUTH_TOKEN` | goblin | — | Turso auth token. |
| `GOBLIN_TURSO_MODE` | goblin | — | Turso connection mode. |
| `GOBLIN_TURSO_LOCAL_PATH` | goblin | — | Local libSQL database path. |
| `GOBLIN_LOG_CONFIG` | goblin | — | Path to a YAML logging configuration file. |

### Example `.env`

```env
GOBLIN_MODEL_API_KEY=sk-...
```

## Development

Run quality checks across the whole workspace from the repo root:

```sh
task lint
 task format
task typecheck
task test
task validate
```

Run checks for a single project:

```sh
task goblin:lint
task goblin:test
```

## Project Layout

```
jp-goblin/
├── pyproject.toml          # Workspace manifest
├── uv.lock                 # Shared dependency lockfile
├── Taskfile.yml            # Root task delegations
├── .python-version         # Python version pin
├── blueprints/             # Example blueprint YAML files
├── goblin/                 # Autonomous agent harness
└── goblin-common/          # Shared config/logging utilities
```

See each project's `README.md` for project-specific details.
