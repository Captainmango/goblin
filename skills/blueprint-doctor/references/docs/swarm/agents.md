# Swarm Agents

**Worker agents** execute the tasks created by the coordinator. They run concurrently and communicate through the shared swarm database.

## Configuration

```yaml
swarm:
  agents:
    - name: coder
      prompt: "You are a Python developer. Check the tasks table for your assignments."
      allowed_tools: [Bash, Read, Write, Edit]
      roles: [backend]
      max_iterations: 80
      max_cost: 3.0
      hooks:
        pre_tool: [check_lint]
      mcp_servers:
        db:
          command: uvx
          args: [mcp-server-sqlite, --db-path, /tmp/app.db]
```

## Fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Unique agent identifier within the swarm. |
| `prompt` | Yes | Agent instruction prompt. Supports `{variable}` interpolation. |
| `allowed_tools` | No | Tool whitelist. Agents **always** get `SwarmAgent` and `Bash` unless explicitly removed. |
| `roles` | No | Specialisation roles for task matching. |
| `max_iterations` | No | Max LLM turns. Defaults to swarm-level `max_iterations`. |
| `max_tokens` | No | Max tokens for this agent. |
| `max_cost` | No | Max cost for this agent. |
| `model` / `llm` | No | Per-agent LLM configuration. |
| `hooks` | No | Per-agent hook configuration. |
| `mcp_servers` | No | Per-agent MCP server configuration. |

## Default Tools

Every worker agent automatically receives:

- `SwarmAgent` — For claiming and completing tasks, creating artifacts, and sending signals.
- `Bash` — For executing shell commands.

You can remove `Bash` from `allowed_tools` if you want to restrict an agent to only swarm operations and file editing.

## Per-Agent Overrides

Agent-level configuration overrides swarm-level defaults:

- LLM (`model` / `llm`)
- Hooks
- MCP servers
- Cost limits (`max_iterations`, `max_tokens`, `max_cost`)

This lets you use cheaper models for simple agents and more capable models for complex ones.
