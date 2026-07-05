# Goblin

Goblin is an autonomous coding agent harness that runs high-level definitions called **blueprints** (YAML files). The runner parses a blueprint, orchestrates phase execution via the OpenHands SDK, and applies lifecycle hooks around tool calls.

## Project Layout

```
goblin/
├── goblin/
│   ├── __init__.py        # Package init (empty)
│   ├── blueprint.py       # V1Blueprint: core orchestration class
│   ├── enums.py           # StrEnum classes (SwarmState, TaskStatus, AgentStatus, CleanupPolicy)
│   ├── hooks.py           # Hook registry and @hook decorator
│   ├── logging.py         # Logging config, JSONFormatter, configure_logging()
│   ├── prompts.py         # System prompts (BASE_SYSTEM_PROMPT, JSON_REPAIR_PROMPT, SWARM_*_PROMPT)
│   ├── runner.py          # BlueprintRunner: loads YAML, routes to blueprint version
│   ├── phase.py           # PromptPhase, LoadProjectPhase, PhaseProtocol
│   ├── swarm.py           # SwarmPhase: concurrent agent orchestration with state machine
│   ├── swarm_db.py        # SwarmDB: SQLite coordination layer for swarm agents
│   └── utils/
│       ├── __init__.py
│       ├── click_helpers.py   # JsonParamType for CLI
│       ├── env.py             # $VAR / ${VAR} env var resolution
│       └── jinja_helpers.py   # Jinja2 custom filters (contains, json_value)
├── spec/
│   └── v1.json            # JSON Schema for blueprints (evolving standard)
├── tests/
│   ├── __init__.py
│   ├── test_blueprint_foreach.py
│   ├── test_build_hooks.py
│   ├── test_evaluate_condition.py
│   ├── test_extract_json.py
│   ├── test_jinja_helpers.py
│   ├── test_load_project.py
│   ├── test_logging.py
│   ├── test_parse_phase.py
│   ├── test_run_orchestration.py
│   └── test_swarm.py
├── agents/                # OpenHands agents (symlinked into .claude/)
│   └── blueprint-maker.md
├── commands/              # OpenHands commands (symlinked into .claude/)
│   └── run-blueprint.md
├── references/            # OpenHands references (symlinked into .claude/)
│   └── blueprint_creation_guidance.md
├── skills/                # OpenHands skills (symlinked into .claude/)
│   └── install/
├── blueprints/
│   ├── test.yaml          # Example blueprint
│   └── swarm_example.yaml # Example swarm blueprint
├── main.py                # CLI entry point (Click)
├── pyproject.toml
├── ruff.toml              # Linter/formatter config
└── Taskfile.yml           # Task runner (lint, format, typecheck)
```

## Tooling

- **Python 3.11+** — specified in `.python-version`
- **uv** — package manager; use `uv run`, `uv add`, `uv sync` (never pip)
- **Taskfile** — task runner; use `task lint`, `task format`, `task typecheck`
- **openhands** — agent orchestration
- **click** — CLI framework
- **jinja2** — template expressions in conditions and foreach
- **pydantic** — response validation (PhaseResult model)
- **pyyaml** — YAML parsing for blueprints
- **ruff** — linter and formatter (dev)
- **pyrefly** — type checker (dev)
- **pytest / pytest-asyncio** — test framework (dev)
- **dotenv** — dev dependency for loading `.env`

## Architecture

### Blueprint

Blueprints are YAML files validated against `spec/v1.json`. The schema is an evolving standard — changes to the spec are a first-class concern of this project.

```yaml
# yaml-language-server: $schema=../spec/v1.json
blueprint:
  name: my_blueprint
  vars:
    repo: my-service
  defaults:
    hooks:
      post_tool: [run_tests]
  phases:
    - id: phase_one
      prompt: "Do something with {repo}"
      allowed_tools: [Bash, Read, Write]
      on_failure:
        action: skip
        notify: alert_hook
```

Phases are **prompt phases** (run an agent with a prompt), **load_project phases** (load full project configuration from a cloned project), or **swarm phases** (launch concurrent agents coordinating through a shared SQLite database). The schema enforces this with a `oneOf` constraint — a phase must have exactly one of `prompt`, `load_project`, or `swarm`.

**Key blueprint fields:**

| Field | Description | Status |
|-------|-------------|--------|
| `vars` | Blueprint-level variables for `{variable}` interpolation | Implemented — merged into context at runtime |
| `defaults.hooks` | Hook names applied to all phases unless overridden | Implemented |
| `defaults.mcp_servers` | Named MCP server configs (stdio/sse/http) available to all phases | Implemented |
| `phases[].prompt` | Agent prompt (mutually exclusive with `load_project`) | Implemented |
| `phases[].load_project` | Path to project root — loads settings, CLAUDE.md refs, agent definitions, and hooks | Implemented |
| `phases[].allowed_tools` | List of tool names the agent may use | Implemented |
| `phases[].foreach` | Integer, Jinja2 expression (must resolve to integer), or file path. Injects `{index}`; file mode also injects `{item}`. | Implemented |
| `phases[].condition` | Jinja2 expression evaluated against prior phase results. Custom filters: `contains`, `json_value`. | Implemented |
| `phases[].mcp_servers` | Per-phase MCP servers; merges with defaults (phase wins on name collision) | Implemented |
| `phases[].on_failure.action` | `skip`, `retry`, or `abort` | Implemented |
| `phases[].on_failure.notify` | Hook name to call on phase failure | Implemented |
| `phases[].swarm` | Swarm phase — launches concurrent agents with a coordinator, shared SQLite DB, and filesystem workspace | Implemented |

### Runner (`goblin/runner.py`)

`BlueprintRunner` is a thin entry point:

1. Loads the YAML blueprint
2. Routes to the appropriate blueprint version class (currently `V1Blueprint`)
3. Calls `blueprint.run(context)` and returns results

### Blueprint (`goblin/blueprint.py`)

`V1Blueprint` is the core orchestration class:

1. Parses phases from the raw spec via `_parse_phase()` (dispatches to `PromptPhase`, `LoadProjectPhase`, or `SwarmPhase`)
2. For each phase: evaluates conditions, resolves foreach, builds hooks, runs the phase
3. Hook resolution: phase-level hooks **override** (not merge) blueprint defaults
4. Results stored as `results[phase_id]` and returned from `run()`
5. External `context` dict is passed to `run()` by the caller and merged with blueprint `vars` for `{variable}` interpolation
6. All agent calls run through the OpenHands SDK

**Key methods:**

- `_resolve()` — Three-mode foreach resolution: integer, Jinja2 expression, or file-backed
- `_evaluate_condition()` — Evaluate Jinja2 conditions against prior phase results
- `_build_hooks()` — Resolve hook names from defaults/phase config to callables
- `_build_mcp_servers()` — Merge blueprint + phase MCP configs, resolve `$VAR` env refs, apply `{variable}` interpolation
- `_load_project()` — Load full project configuration from a path
- `_handle_phase_failure()` — Apply on_failure policy (skip, retry, abort)

### Phases (`goblin/phase.py`)

`PhaseProtocol` defines the interface: `id`, `condition`, `foreach`, `on_failure` attributes and `async run(context)`.

- **`PromptPhase`** — Runs an agent with a prompt. Parses the response as JSON via `_parse_response()`, with automatic retry/repair (up to 3 attempts via `_request_json_repair()`).
- **`LoadProjectPhase`** — Delegates to `blueprint._load_project()` to load project configuration.
- **`SwarmPhase`** — Launches concurrent agents coordinating through a shared SQLite database (`SwarmDB`) and filesystem workspace. Uses a state machine (`setup → coordinator → agents_running → evaluating → ... → teardown`) to orchestrate rounds of work. The coordinator decomposes goals into tasks; worker agents claim and complete tasks via SQL. SwarmDB tools are registered as in-process SDK MCP servers.

**`load_project` phases:** When a phase has `load_project` instead of `prompt`, the blueprint loads the full project configuration from the given path:

- **Settings**: Stores `{path}/.claude/settings.json` path (passed to SDK via `settings` param). Also extracts shell command hooks and registers them in `HOOK_REGISTRY` as `project_pre_tool` / `project_post_tool`.
- **References**: Reads `{path}/CLAUDE.md` and `{path}/.claude/CLAUDE.md` if they exist, concatenates them, and appends to the system prompt for subsequent phases.
- **Agents**: Scans `{path}/.claude/agents/*.md` — each file becomes an `AgentDefinition(description=filename, prompt=file_content)` passed to the SDK.
- **cwd**: Sets the project path as the working directory for subsequent prompt phases.

### Hook System (`goblin/hooks.py`)

Hooks are registered by name with the `@hook` decorator and stored in `HOOK_REGISTRY`. The runner resolves hook names from the blueprint to callables at execution time.

```python
from goblin.hooks import hook

@hook("run_tests")
def run_tests(tool_use):
    ...
```

Hooks attach to `PreToolUse` and `PostToolUse` events on the OpenHands agent.

### Logging (`goblin/logging.py`)

Structured JSON logging via Python's `logging` module. `configure_logging()` is called
once at CLI entry. Default: JSON lines to `goblin-run.jsonl` + plain-text stderr at WARNING.
Users can supply a YAML config via `--log-config` or `GOBLIN_LOG_CONFIG` env var.

## Spec Evolution

`spec/v1.json` is the source of truth for the blueprint schema. When extending the schema:

- Maintain backward compatibility within v1 where possible
- All new fields must have a `description` and at least one `examples` entry
- Use `additionalProperties: false` on all objects to keep the schema strict
- Version bumps (v2, etc.) get a new file in `spec/`

## Conventions

- All runner and agent operations are **async**
- Prompts support `{variable}` interpolation (Python `.format()`) from the `context` dict passed to `run()`
- Phase template expressions use `{{ }}` Jinja2 syntax for conditions and foreach
- Hook names in blueprints are strings that must exist in `HOOK_REGISTRY` at runtime
- Blueprint files live in `blueprints/`
- MCP server secrets use `$VAR` / `${VAR}` syntax resolved from `os.environ` (loaded via `.env` / `load_dotenv()`)
- All runtime messages use `logging.getLogger(__name__)`, never `print()`
- Log calls include `extra={"event": "...", ...}` for structured context
- **Plugin assets live at the project root, not in `.claude/`.** The top-level `agents/`, `commands/`, `references/`, and `skills/` directories are the source of truth for OpenHands plugin assets. The corresponding `.claude/` subdirectories are **symlinks** pointing to these outer folders — never edit or create files directly in `.claude/agents/`, `.claude/commands/`, `.claude/references/`, or `.claude/skills/`

## Commands

- Use the Taskfile to know what commands are available for managing the project
- `task lint` - Run linting for the project
- `task format` - Run the formatter for the project
- `task typecheck` - Run typechecking for the project
- If a command is missing that you feel is necessary, feel free to add it and explain why
