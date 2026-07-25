# G.O.B.L.I.N

**Generative Orchestration of Basic Logic and Instructions**

Goblin is an autonomous coding-agent harness. It runs YAML **blueprints** that describe multi-step agent workflows and executes them via the OpenHands SDK.

Each blueprint step is a self-contained prompt with explicit tool permissions. Steps are isolated: only filesystem changes persist between them. `load_project` steps layer orchestration on top of a target project's existing OpenHands/ Claude Code configuration.

## Setup

This project is part of the workspace monorepo. From the repo root:

```sh
uv sync --all-packages
```

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

## Running

Run a blueprint:

```sh
uv run goblin run blueprints/test.yaml
```

Pass context variables as JSON:

```sh
uv run goblin run blueprints/test.yaml --context '{"repo": "https://github.com/example/repo.git"}'
```

Validate blueprints:

```sh
uv run goblin validate blueprints/*.yaml
```

Resume from a checkpoint:

```sh
uv run goblin run --resume .goblin/run-<id>.json
```

## Environment Variables

Create a `.env` file in the working directory where you run commands.

### Required

| Variable | Purpose |
|----------|---------|
| `GOBLIN_MODEL_API_KEY` | LLM API key. Falls back to `MODEL_API_KEY` if not set. Required when running blueprints that use an LLM. |

### Optional

| Variable | Default | Purpose |
|----------|---------|---------|
| `GOBLIN_BASE_URL` | `OPENHANDS_BASE_URL` | Base URL for the LLM API. |
| `OPENHANDS_SUPPRESS_BANNER` | `1` | Suppress the OpenHands startup banner. |
| `GOBLIN_BACKEND` | — | Swarm backend selection. |
| `GOBLIN_TURSO_URL` | — | Turso/libSQL URL for distributed swarm state. |
| `GOBLIN_TURSO_AUTH_TOKEN` | — | Turso auth token. |
| `GOBLIN_TURSO_MODE` | — | Turso connection mode. |
| `GOBLIN_TURSO_LOCAL_PATH` | — | Local libSQL database path. |
| `GOBLIN_LOG_CONFIG` | — | Path to a YAML logging configuration file. |

### Example `.env`

```env
GOBLIN_MODEL_API_KEY=sk-...
```

## Development

Run project tasks from the repo root or from inside this directory:

```sh
task goblin:lint
task goblin:format
task goblin:typecheck
task goblin:test
task goblin:validate
```

Or directly:

```sh
uv run ruff check .
uv run ruff format .
uv run pyrefly check
uv run pytest
uv run goblin validate ../blueprints/*.yaml
```
