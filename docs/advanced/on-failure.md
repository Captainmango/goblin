# On Failure

The `on_failure` field controls what happens when a phase fails.

## Actions

| Action | Behaviour |
|--------|-----------|
| `skip` | Log the failure and continue to the next phase. |
| `retry` | Re-run the phase up to `max_retries` times. If all retries fail, abort. |
| `abort` | Stop the entire blueprint. This is the default. |

## Syntax

```yaml
phases:
  - id: risky_operation
    prompt: "Try something that might fail"
    allowed_tools: [Bash]
    on_failure:
      action: skip

  - id: flaky_operation
    prompt: "Try something that might be flaky"
    allowed_tools: [Bash]
    on_failure:
      action: retry
      max_retries: 3

  - id: critical_operation
    prompt: "This must succeed"
    allowed_tools: [Bash]
    on_failure:
      action: abort
```

## Retry Behaviour

When `action: retry` is set:

1. The phase runs.
2. If it fails, Goblin waits and re-runs it.
3. This repeats up to `max_retries` times (default: 1).
4. If the phase succeeds on any retry, the blueprint continues.
5. If all retries are exhausted, the blueprint aborts.

## Notification on Failure

You can trigger a hook function when a phase fails:

```yaml
phases:
  - id: deploy
    prompt: "Deploy to production"
    allowed_tools: [Bash]
    on_failure:
      action: abort
      notify: slack_notify
```

The hook function must be registered in the hook registry (see [Hooks](../configuration/hooks.md)).

## Foreach and Failure

In a `foreach` phase, failure handling applies per-iteration:

- If an iteration fails and `action: skip`, the loop continues with the next item.
- If an iteration fails and `action: retry`, only that iteration is retried.
- If an iteration fails and `action: abort` (or retries are exhausted), the entire foreach phase aborts.

The final result includes an `iterations` array with the result of each iteration.
