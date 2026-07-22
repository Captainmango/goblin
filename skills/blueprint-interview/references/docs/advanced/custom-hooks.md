# Custom Hooks

Hooks can be registered programmatically for developers extending Goblin.

## Registering a Hook

Register a named hook manually:

```python
from openhands.sdk.hooks import HookDefinition
from goblin.hooks import register_hook

register_hook("run_lint", HookDefinition(command="ruff check .", timeout=60))
register_hook("slack_notify", HookDefinition(command="curl -X POST -d 'text=Phase failed' $SLACK_WEBHOOK_URL"))
```

The command is executed before or after each tool call, depending on whether the hook is listed in `pre_tool` or `post_tool`.

## Using Custom Hooks in Blueprints

Once registered, use the hook name in blueprint configuration:

```yaml
defaults:
  hooks:
    pre_tool: [run_lint]
    post_tool: [run_tests]

phases:
  - id: deploy
    prompt: "Deploy to production"
    allowed_tools: [Bash]
    on_failure:
      action: abort
      notify: slack_notify
```

## Hook Execution

Hooks are executed by the OpenHands SDK around each tool call:

1. Before the tool runs, all `pre_tool` hooks execute in order.
2. The tool runs.
3. After the tool runs, all `post_tool` hooks execute in order.

If a hook command fails (non-zero exit code), the OpenHands SDK handles the error according to its configuration.
