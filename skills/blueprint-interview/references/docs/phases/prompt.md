# Prompt Phase

The `prompt` phase is the most common phase type. It sends an instruction to an LLM agent and returns the agent's response.

## Full Field Reference

```yaml
- id: my_phase
  prompt: "The instruction text. Supports {variable} interpolation."
  allowed_tools: [Bash, Read, Write, Edit]
  model: anthropic/claude-sonnet-4-6
  llm:
    model: anthropic/claude-sonnet-4-6
    api_key: $GOBLIN_MODEL_API_KEY
  max_iterations: 500
  max_tokens: 100000
  max_cost: 5.0
  history: [previous_phase_id]
  output_schema:
    type: object
    properties:
      status: { type: string }
  worktree:
    base_dir: .goblin/worktrees
    deps: [node_modules]
    auto_link: true
  notifications:
    label: ops-oncall
  hooks:
    pre_tool: [check_prometheus]
    post_tool: [run_tests]
  mcp_servers:
    github:
      command: npx
      args: [-y, "@modelcontextprotocol/server-github"]
  condition: "{{ steps.previous_phase.success }}"
  foreach: "5"
  on_failure:
    action: retry
    max_retries: 3
  depends_on: [previous_phase_id]
```

## Field Descriptions

### `prompt` (required)
The instruction sent to the agent. Supports `{variable}` interpolation from blueprint `vars` and `--context`.

### `allowed_tools`
Whitelist of tools the agent may call. See [Tools Overview](../tools/overview.md) for the full list.

### `model` / `llm`
`model` is a shorthand string (e.g., `anthropic/claude-sonnet-4-6`).
`llm` is a full OpenHands LLM configuration dict with authentication, retries, temperature, etc.

Precedence: phase `llm` > phase `model` > defaults `llm` > defaults `model`.

### `max_iterations`
Maximum LLM turns for this phase. Default: 500.

### `max_tokens`
Maximum cumulative prompt + completion tokens. Null means unlimited.

### `max_cost`
Maximum cumulative cost in the model's currency. Null means unlimited.

### `history`
List of prior phase IDs whose prompts and outputs are prepended to this phase's prompt as context. Cannot reference `foreach` phases.

### `output_schema`
JSON Schema fragment that the agent's extracted JSON response must conform to. If validation fails, the phase returns `success: false`.

### `worktree`
Configuration for the `Worktree` tool. Required when `Worktree` is in `allowed_tools`. See [Worktree](../tools/built-in.md#worktree).

### `notifications`
Configuration for the `AskUser` tool. Optional — falls back to the `default` label when omitted. See [AskUser](../tools/built-in.md#askuser).

### `hooks`
Lifecycle hooks that run before or after each tool call. See [Hooks](../configuration/hooks.md).

### `mcp_servers`
Named MCP server configurations. Tools from these servers are accessed via `mcp__server__tool` names in `allowed_tools`. See [MCP Servers](../tools/mcp-servers.md).

### `condition`
Jinja2 expression evaluated against prior phase results. Phase only runs if truthy.

### `foreach`
Controls iteration: integer count, Jinja2 expression evaluating to an integer, or file path (one run per non-empty line).

### `on_failure`
Failure handling configuration:
- `action`: `skip`, `retry`, or `abort` (default: `abort`)
- `max_retries`: Maximum retry attempts when action is `retry` (default: 1)
- `notify`: Hook function name to call on failure

### `depends_on`
Phase IDs that must finish before this phase starts. Omit for sequential execution.
