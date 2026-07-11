# Goblin

Goblin is an autonomous coding agent harness that runs high-level definitions called **blueprints** (YAML files). The runner parses a blueprint, orchestrates phase execution via the OpenHands SDK, and applies lifecycle hooks around tool calls.

## Project Layout

```
goblin/
├── goblin/
│   ├── __init__.py         # Package init (empty)
│   ├── blueprint.py        # V1Blueprint: parses phases, resolves config/hooks/mcp/foreach/conditions
│   ├── orchestrator.py     # PhaseOrchestrator: wave-based phase execution loop, checkpointing, failure handling
│   ├── dependency_graph.py # DependencyGraph: depends_on defaults, cycle detection, history-reference validation
│   ├── checkpoint.py       # Checkpoint: persists run/phase state to .goblin/run-<id>.json for --resume
│   ├── cost_control.py     # SpendTracker, CircuitBreaker, LimitExceeded: iteration/token/cost limits
│   ├── conversation.py     # run_conversation(): shared Local/RemoteConversation setup for PromptPhase & SwarmPhase
│   ├── import_resolver.py  # resolve_imports(): merges blueprint.imports before schema validation
│   ├── validation.py       # load_schema(), validate_blueprint(), format_errors(): JSON Schema validation
│   ├── workspace.py        # build_workspace(): constructs OpenHands LocalWorkspace/RemoteWorkspace
│   ├── enums.py            # StrEnum classes (SwarmState, TaskStatus, AgentStatus, CleanupPolicy, RunStatus, PhaseStatus)
│   ├── hooks.py            # Hook registry and @hook decorator
│   ├── logging.py          # Logging config, JSONFormatter, configure_logging()
│   ├── prompts.py          # System prompts (BASE_SYSTEM_PROMPT, JSON_REPAIR_PROMPT, SWARM_*_PROMPT)
│   ├── cli.py              # Click CLI: `goblin run` / `goblin validate`
│   ├── runner.py           # BlueprintRunner: loads YAML, resolves imports, validates, routes to blueprint version
│   ├── phase.py            # PromptPhase, LoadProjectPhase, PhaseProtocol, PhaseResult, JSON extraction helpers
│   ├── swarm.py            # SwarmPhase: concurrent agent orchestration with state machine
│   ├── swarm_db.py         # SwarmDB: SQLite coordination layer for swarm agents
│   └── utils/
│       ├── __init__.py
│       ├── click_helpers.py   # JsonParamType for CLI
│       ├── env.py             # $VAR / ${VAR} env var resolution
│       ├── jinja_helpers.py   # Jinja2 custom filters (contains, json_value)
│       ├── llm.py             # build_llm(): resolves model config into an OpenHands LLM
│       ├── swarm_agent_tool.py   # Typed swarm-database tool for worker agents
│       ├── swarmdb_tool.py       # Typed task-board tool for swarm coordinators
│       ├── worktree_tool.py      # Typed git worktree tool for isolated working copies
│       └── tree.py            # walk_strings(): shared recursive string-transform over dict/list structures
├── spec/
│   └── v1.json             # JSON Schema for blueprints (evolving standard)
├── tests/                  # pytest suite, one file per module/behavior (see goblin/ list above)
│   └── test_worktree_tool.py     # Unit tests for the Worktree tool
├── blueprints/
│   ├── test.yaml           # Example blueprint
│   └── swarm_example.yaml  # Example swarm blueprint
├── main.py                 # CLI entry point (calls goblin.cli.cli())
├── install.sh              # pipx-installs this repo as the `goblin` CLI
├── pyproject.toml
├── ruff.toml               # Linter/formatter config
├── Taskfile.yml            # Task runner (lint, format, typecheck, validate)
├── AGENTS.md               # Pointer to this file for agent tooling
├── ROADMAP.md              # Short-term roadmap to v1
└── VISION.md               # Project motivation and design principles
```

## Tooling

- **Python 3.13+** — specified in `.python-version` and `pyproject.toml` `requires-python`
- **uv** — package manager; use `uv run`, `uv add`, `uv sync` (never pip)
- **Taskfile** — task runner; use `task lint`, `task format`, `task typecheck`, `task validate`
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

## Conventions

- All runner and agent operations are **async**
- Prompts support `{variable}` interpolation (Python `.format()`) from the `context` dict passed to `run()`
- Phase template expressions use `{{ }}` Jinja2 syntax for conditions and foreach
- Hook names in blueprints are strings that must exist in `HOOK_REGISTRY` at runtime
- Blueprint files live in `blueprints/`
- MCP server secrets use `${VAR}` syntax resolved from `os.environ` (loaded via `.env` / `load_dotenv()`)
- All runtime messages use `logging.getLogger(__name__)`, never `print()`
- Log calls include `extra={"event": "...", ...}` for structured context

## Commands

- Use the Taskfile to know what commands are available for managing the project
- `task lint` - Run linting for the project
- `task format` - Run the formatter for the project
- `task typecheck` - Run typechecking for the project
- `task validate` - Validate the example blueprints against `spec/v1.json`
- If a command is missing that you feel is necessary, feel free to add it and explain why

## Built-in tools

Phases whitelist tools via `allowed_tools`. Besides the OpenHands file-editor, terminal, `Glob`, and `Grep` tools, Goblin registers:

- `SwarmTasks` / `SwarmAgent` — swarm coordination database tools (see `goblin/utils/swarmdb_tool.py` and `goblin/utils/swarm_agent_tool.py`).
- `Worktree` — typed git worktree operations (`list`, `create`, `remove`, `prune`, `link_deps`).

Keys:

- `base_dir` — directory under which all managed worktrees are created (relative to the repo root). Default: `.goblin/worktrees`.
- `deps` — list of dependency directory names that may be symlinked from the main worktree. There is no built-in default; users must opt in explicitly.
- `auto_link` — whether `create` automatically symlinks the configured `deps` after creating a worktree. Default: `false`.

If `Worktree` appears in `allowed_tools` but no `worktree` config is defined, the phase fails fast with a clear error.
