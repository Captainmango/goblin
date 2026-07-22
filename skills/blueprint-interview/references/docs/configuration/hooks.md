# Hooks

Hooks are lifecycle functions that run before or after each tool call in a prompt or swarm phase.

## Hook Configuration

```yaml
phases:
  - id: analyse
    prompt: "Analyse the code"
    allowed_tools: [Bash, Read]
    hooks:
      merge: true
      pre_tool: [check_prometheus]
      post_tool: [run_tests, notify_slack]
```

## Fields

| Field | Type | Description |
|-------|------|-------------|
| `pre_tool` | array | Hook names to run before each tool call. |
| `post_tool` | array | Hook names to run after each tool call. |
| `merge` | boolean | When `true`, append phase-level hooks to defaults. When `false` (default), phase-level hooks replace defaults. |

## Merge vs Override

By default, phase-level hooks **replace** blueprint defaults entirely:

```yaml
defaults:
  hooks:
    pre_tool: [check_prometheus]

phases:
  - id: phase_a
    hooks:
      pre_tool: [run_lint]
    # Effective pre_tool: [run_lint] (check_prometheus is NOT run)

  - id: phase_b
    hooks:
      merge: true
      pre_tool: [run_lint]
    # Effective pre_tool: [check_prometheus, run_lint]
```

## Built-in Registries

Hooks are registered by name in a global registry. Register hooks in code with `register_hook`:

```python
from openhands.sdk.hooks import HookDefinition
from goblin.hooks import register_hook

register_hook(
    "check_prometheus",
    HookDefinition(command="curl -sf http://localhost:9090/-/healthy", timeout=30),
)
```

## Project-Level Hooks

When a `load_project` phase reads `.claude/settings.json`, it registers shell command hooks automatically:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "echo 'About to run tool'"
          }
        ]
      }
    ]
  }
}
```

These are registered as `project_pre_tool` and `project_post_tool` in the hook registry.
