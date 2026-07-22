# Cleanup

The `cleanup` policy controls whether the swarm workspace is deleted after teardown.

## Policies

| Policy | Behaviour |
|--------|-----------|
| `on_success` | Delete the workspace only if all tasks completed successfully. This is the default. |
| `always` | Always delete the workspace, even if tasks failed. |
| `never` | Never delete the workspace. Useful for debugging. |

## Configuration

```yaml
swarm:
  cleanup: on_success
```

```yaml
swarm:
  cleanup: never  # Keep workspace for inspection
```

## Workspace Contents

The swarm workspace contains:

- `shared/` — Shared filesystem for agent artifacts.
- `agents/<name>/` — Per-agent working directories.
- `swarm.db` — The swarm database (if using local mode).

When cleanup runs, the entire workspace directory is removed with `shutil.rmtree(..., ignore_errors=True)`.
