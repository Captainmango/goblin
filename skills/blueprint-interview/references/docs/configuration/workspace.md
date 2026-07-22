# Workspace Configuration

The workspace determines where agent operations run. Goblin supports local and remote workspaces.

## Local Workspace (Default)

If no workspace is configured, Goblin uses a `LocalWorkspace` with the project path or current directory as the working directory.

```yaml
blueprint:
  workspace:
    type: local
    working_dir: /tmp/project
```

## Remote Workspace

Connect to an OpenHands agent server:

```yaml
blueprint:
  workspace:
    type: remote
    host: https://agent-server.example.com
    working_dir: /workspace
    api_key: $REMOTE_API_KEY
    read_timeout: 1200
    max_connections: 10
```

### Remote Fields

| Field | Required | Description |
|-------|----------|-------------|
| `type` | Yes | Must be `remote`. |
| `host` | Yes | Remote agent server URL. |
| `working_dir` | No | Path on the remote server. Defaults to `.`. |
| `api_key` | No | Authentication key. |
| `read_timeout` | No | HTTP read timeout in seconds. Default: 600. |
| `max_connections` | No | Maximum HTTP connections. Null means unlimited. |

## Precedence

Phase-level `worktree` config overrides blueprint-level `defaults.worktree`.

```yaml
defaults:
  worktree:
    base_dir: .goblin/worktrees
    deps: [node_modules]
    auto_link: true

phases:
  - id: special_branch
    prompt: "Work on a feature branch"
    allowed_tools: [Worktree]
    worktree:
      base_dir: custom/worktrees
      auto_link: false
```

In this example, `special_branch` uses `custom/worktrees` with `auto_link: false`, but still inherits `deps: [node_modules]` from defaults because `worktree` configs are merged (not replaced) at the field level.
