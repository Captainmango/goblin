# Gremlin

Gremlin is an autonomous coding agent harness that runs high-level workflow definitions called **blueprints** (YAML files). The runner parses a blueprint, orchestrates step execution via the Claude Agent SDK, and applies lifecycle hooks around tool calls.

## Project Layout

```
gremlin/
├── gremlin/
│   ├── __init__.py    # Package init (empty)
│   ├── hooks.py       # Hook registry and @hook decorator
│   └── runner.py      # WorkflowRunner: parses and executes blueprints
├── spec/
│   └── v1.json        # JSON Schema for blueprints (evolving standard)
├── workflows/
│   └── test.yaml      # Example blueprint
├── main.py            # Entry point
└── pyproject.toml
```

## Tooling

- **Python 3.11+** — specified in `.python-version`
- **uv** — package manager; use `uv run`, `uv add`, `uv sync` (never pip)
- **claude-agent-sdk** — agent orchestration
- **pyyaml** — YAML parsing for blueprints
- **dotenv** — dev dependency for loading `.env` in `main.py`

## Architecture

### Blueprint

Blueprints are YAML files validated against `spec/v1.json`. The schema is an evolving standard — changes to the spec are a first-class concern of this project.

```yaml
# yaml-language-server: $schema=../spec/v1.json
workflow:
  name: my_workflow
  vars:
    repo: my-service
  defaults:
    hooks:
      post_tool: [run_tests]
  steps:
    - id: step_one
      prompt: "Do something with {repo}"
      allowed_tools: [Bash, Read, Write]
      on_failure:
        action: skip
        notify: alert_hook
```

Steps are either **prompt steps** (run an agent with a prompt) or **load_project steps** (load full project configuration from a cloned project). The schema enforces this with a `oneOf` constraint — a step must have exactly one of `prompt` or `load_project`.

**Key blueprint fields:**

| Field | Description | Status |
|-------|-------------|--------|
| `vars` | Workflow-level variables for `{variable}` interpolation | Schema only — not auto-merged into context by runner |
| `defaults.hooks` | Hook names applied to all steps unless overridden | Implemented |
| `steps[].prompt` | Agent prompt (mutually exclusive with `load_project`) | Implemented |
| `steps[].load_project` | Path to project root — loads settings, CLAUDE.md refs, agent definitions, and hooks | Implemented |
| `steps[].allowed_tools` | List of tool names the agent may use | Implemented |
| `steps[].foreach` | `{{ steps.<id>.output.<field> }}` — iterate step once per item | Scaffolded — `_resolve` not yet implemented |
| `steps[].condition` | `{{ steps.<id>.all_succeeded }}` — skip step if false | Scaffolded — `_evaluate_condition` not yet implemented |
| `steps[].depends_on` | Step ID(s) that must complete first | Schema only — no dependency graph in runner |
| `steps[].on_failure.action` | `skip`, `retry`, or `abort` | Schema defined |
| `steps[].on_failure.notify` | Hook name to call on step failure | Schema defined |

### Runner (`gremlin/runner.py`)

`WorkflowRunner` is the core orchestration class:

1. Loads the YAML blueprint on init
2. For each step: checks for `load_project` or `prompt`, builds hooks, runs the agent
3. Hook resolution: step-level hooks **override** (not merge) workflow defaults
4. Results stored as `results[step_id]` and returned from `run()`
5. External `context` dict is passed to `run()` by the caller (e.g., `main.py`) and used for `{variable}` interpolation in prompts
6. All agent calls set `env={"CLAUDECODE": ""}` to run in Claude Code mode

**`load_project` steps:** When a step has `load_project` instead of `prompt`, the runner loads the full project configuration from the given path:

- **Settings**: Stores `{path}/.claude/settings.json` path (passed to SDK via `settings` param). Also extracts shell command hooks and registers them in `HOOK_REGISTRY` as `project_pre_tool` / `project_post_tool`.
- **References**: Reads `{path}/CLAUDE.md` and `{path}/.claude/CLAUDE.md` if they exist, concatenates them, and appends to the system prompt for subsequent steps.
- **Agents**: Scans `{path}/.claude/agents/*.md` — each file becomes an `AgentDefinition(description=filename, prompt=file_content)` passed to the SDK.
- **cwd**: Sets the project path as the working directory for subsequent prompt steps.

### Hook System (`gremlin/hooks.py`)

Hooks are registered by name with the `@hook` decorator and stored in `HOOK_REGISTRY`. The runner resolves hook names from the blueprint to callables at execution time.

```python
from gremlin.hooks import hook

@hook("run_tests")
def run_tests(tool_use):
    ...
```

Hooks attach to `PreToolUse` and `PostToolUse` events on the Claude agent.

## Spec Evolution

`spec/v1.json` is the source of truth for the blueprint schema. When extending the schema:

- Maintain backward compatibility within v1 where possible
- All new fields must have a `description` and at least one `examples` entry
- Use `additionalProperties: false` on all objects to keep the schema strict
- Version bumps (v2, etc.) get a new file in `spec/`

## Conventions

- All runner and agent operations are **async**
- Prompts support `{variable}` interpolation (Python `.format()`) from the `context` dict passed to `run()`
- Step template expressions use `{{ }}` Jinja-style syntax (not yet implemented)
- Hook names in blueprints are strings that must exist in `HOOK_REGISTRY` at runtime
- Workflow files live in `workflows/`
