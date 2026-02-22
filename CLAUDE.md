# Gremlin

Gremlin is an autonomous coding agent harness that runs high-level workflow definitions called **blueprints** (`.grem.yaml` files). The runner parses a blueprint, orchestrates step execution via the Claude Agent SDK, and applies lifecycle hooks around tool calls.

## Project Layout

```
gremlin/
├── gremlin/
│   ├── agent.py       # Claude agent configuration examples
│   ├── hooks.py       # Hook registry and @hook decorator
│   └── runner.py      # WorkflowRunner: parses and executes blueprints
├── spec/
│   └── v1.json        # JSON Schema for .grem.yaml (evolving standard)
├── workflows/
│   └── workflow.grem.yaml  # Example blueprint
├── main.py            # Entry point
└── pyproject.toml
```

## Tooling

- **Python 3.11+** — specified in `.python-version`
- **uv** — package manager; use `uv run`, `uv add`, `uv sync` (never pip)
- **claude-agent-sdk** — sole runtime dependency

## Architecture

### Blueprint (`.grem.yaml`)

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
```

**Key blueprint fields:**

| Field | Description |
|-------|-------------|
| `vars` | Workflow-level variables for `{variable}` interpolation in prompts |
| `defaults.hooks` | Hook names applied to all steps unless overridden |
| `steps[].foreach` | `{{ steps.<id>.output.<field> }}` — iterate step once per item |
| `steps[].condition` | `{{ steps.<id>.all_succeeded }}` — skip step if false |
| `steps[].depends_on` | Step ID(s) that must complete first |
| `steps[].on_failure.action` | `skip`, `retry`, or `abort` |

### Runner (`gremlin/runner.py`)

`WorkflowRunner` is the core orchestration class:

1. Loads the YAML blueprint on init
2. For each step: evaluates `condition`, expands `foreach`, builds hooks, runs the agent
3. Hook resolution: step-level hooks **override** (not merge) workflow defaults
4. Results stored as `results[step_id]` and passed as context to subsequent steps

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
- Prompts support `{variable}` interpolation (Python `.format()`) from `vars` and step context
- Step template expressions use `{{ }}` Jinja-style syntax (resolved by `_resolve` / `_evaluate_condition`)
- Hook names in blueprints are strings that must exist in `HOOK_REGISTRY` at runtime
- Workflow files live in `workflows/` and use the `.grem.yaml` extension
