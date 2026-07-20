# Swarm Tools

Swarm phases use two specialised tools for coordination: `SwarmTasks` for the coordinator and `SwarmAgent` for worker agents.

## SwarmTasks (Coordinator)

**Blueprint name:** `SwarmTasks`

Given to the swarm coordinator to manage the shared task board. The coordinator **always** gets this tool and **never** gets `Bash`.

### Supported Commands

- `create_task` — Add a new pending task.
  - `title` (required) — Short task title.
  - `description` — Longer description.
  - `assigned_to` — Assign to a specific agent by name.
  - `required_role` — Restrict to agents with a matching role.
- `list_tasks` — View all tasks and their status.

### Why No Bash?

The coordinator only gets `SwarmTasks` (and any other explicitly listed non-Bash tools) so it cannot write files or forge database rows for tasks it didn't actually delegate. Worker agents use `SwarmAgent` for task mutations.

## SwarmAgent (Workers)

**Blueprint name:** `SwarmAgent`

Given to worker agents to interact with the swarm database. Agents **always** get this tool and **always** get `Bash` (unless explicitly removed).

### Supported Commands

- `query` — Run a read-only `SELECT` statement.
- `claim_task` — Mark a pending task as `in_progress` and assign it to yourself.
- `complete_task` — Mark a claimed task as `done` and record a result.
- `create_artifact` — Register a deliverable in the shared artifacts table.
- `send_signal` — Send a message to another agent or broadcast it.
- `check_signals` — Read signals addressed to you or broadcast to all agents.

### Agent Attribution

The `SwarmAgent` tool is created with the agent's name injected, so `claim_task` and `complete_task` operations are always attributed to the correct worker.

## Configuration

You do not configure these tools directly. Goblin creates them automatically from the swarm database configuration:

```yaml
swarm:
  backend: turso  # or sqlite
  turso:
    mode: local
    url: ".goblin/swarms/{phase_id}/swarm.db"
```

The `db_config` is passed to both tools so they can open their own database connections.
