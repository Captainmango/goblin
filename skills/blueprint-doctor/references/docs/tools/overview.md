# Tools Overview

Tools extend what an agent can do beyond text generation. Goblin uses a **whitelist** approach: you explicitly list which tools a phase may use via `allowed_tools`.

## How `allowed_tools` Works

```yaml
phases:
  - id: analyse
    prompt: "Analyse the codebase"
    allowed_tools: [Read, Glob, Grep]
```

Only the listed tools are available to the agent. If you list a tool that requires configuration (e.g., `Worktree`), the phase must also provide the corresponding configuration block.

## Tool Name Mapping

Goblin maps friendly names to OpenHands SDK tool names:

| Blueprint Name | OpenHands Tool |
|----------------|----------------|
| `Bash` | `terminal` |
| `Read`, `Write`, `Edit`, `MultiEdit` | `file_editor` |
| `Glob` | `glob` |
| `Grep` | `grep` |
| `Worktree` | `worktree` |
| `SwarmTasks` | `swarm_tasks` |
| `SwarmAgent` | `swarm_agent` |

Multiple blueprint names can map to the same underlying tool. For example, `Read`, `Write`, `Edit`, and `MultiEdit` all resolve to the `file_editor` tool. Goblin deduplicates these automatically.

## Deduplication

When multiple names map to the same underlying tool, Goblin only registers it once:

```yaml
allowed_tools: [Read, Write, Edit]  # All map to file_editor — registered once
```

## Wildcards and MCP Tools

- `*` in `allowed_tools` is ignored for built-in tool resolution (MCP tools are handled separately via `mcp_config`).
- MCP server tools are referenced as `mcp__server__tool` (see [MCP Servers](mcp-servers.md)).
