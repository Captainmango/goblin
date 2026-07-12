# G.O.B.L.I.N

**Generative Orchestration of Basic Logic, Instructions, and Notifications**

Goblin sits between attended chat-based coding agents and maximalist agent frameworks. It lets you define multi-step agent workflows as YAML files called **blueprints** — familiar to anyone who has written a GitHub Actions workflow or Drone CI pipeline — and execute them unattended via a CLI.

Each blueprint step is a self-contained prompt with explicit tool permissions. Steps are isolated: only filesystem changes persist between them. `load_project` steps layer orchestration on top of a target project's existing OpenHands configuration (settings, CLAUDE.md, custom agents).

The project has two halves: an **OpenHands skill** for authoring blueprints conversationally, and the **runtime CLI** for executing them. They share a contract (`spec/v1.json`) but are otherwise decoupled.

## Setup

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
```

## Usage

```sh
uv run python main.py <blueprint> [--context '{"key": "value"}']
```

## Utilities

Project utilities (linting, formatting, typechecking) are defined in `Taskfile.yml` and run via [Task](https://taskfile.dev):

```sh
task lint
task format
task typecheck
task validate
```