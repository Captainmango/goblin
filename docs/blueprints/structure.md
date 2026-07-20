# Blueprint Structure

A blueprint has two top-level keys: `version` and `blueprint`.

## Top-Level Keys

| Key | Required | Description |
|-----|----------|-------------|
| `version` | Yes | Schema version (`v1`). |
| `blueprint` | Yes | The blueprint definition object. |

## Blueprint Block Keys

| Key | Required | Description |
|-----|----------|-------------|
| `name` | Yes | Human-readable identifier. |
| `phases` | Yes | Ordered list of phase definitions. |
| `vars` | No | Blueprint-level variables for `{variable}` interpolation. |
| `defaults` | No | Default values inherited by all phases unless overridden. |
| `workspace` | No | Workspace configuration for all phases. |
| `imports` | No | Array of other blueprints to import and merge inline. |

## Defaults

The `defaults` object sets blueprint-wide defaults:

```yaml
defaults:
  model: anthropic/claude-sonnet-4-6
  llm:
    model: anthropic/claude-sonnet-4-6
    api_key: $GOBLIN_MODEL_API_KEY
  hooks:
    pre_tool: [check_prometheus]
    post_tool: [run_tests]
  mcp_servers:
    github:
      command: npx
      args: [-y, "@modelcontextprotocol/server-github"]
      env:
        GITHUB_TOKEN: $GITHUB_TOKEN
  worktree:
    base_dir: .goblin/worktrees
    deps: [node_modules, .venv]
    auto_link: true
  notifications:
    label: ops-oncall
```

Precedence for any setting is:

1. Phase-level value
2. `defaults` value
3. Built-in fallback (if any)

## Example

```yaml
version: v1
blueprint:
  name: deploy_service
  vars:
    service: api-gateway
    env: staging

  defaults:
    model: anthropic/claude-sonnet-4-6

  workspace:
    type: local
    working_dir: "/tmp/{service}"

  phases:
    - id: build
      prompt: "Build the {service} Docker image for {env}"
      allowed_tools: [Bash]

    - id: deploy
      prompt: "Deploy {service} to {env}"
      allowed_tools: [Bash]
      depends_on: [build]
```
