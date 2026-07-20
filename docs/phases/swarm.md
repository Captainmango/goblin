# Swarm Phase

The `swarm` phase launches a team of concurrent agents that coordinate through a shared database and filesystem workspace. It is a self-contained multi-agent block within a blueprint.

## Syntax

```yaml
- id: build_feature
  swarm:
    goal: "High-level objective for the swarm"
    coordinator:
      prompt: "Coordinator instructions"
      allowed_tools: [Read]
    agents:
      - name: coder
        prompt: "Coder agent instructions"
        allowed_tools: [Bash, Read, Write, Edit]
      - name: tester
        prompt: "Tester agent instructions"
        allowed_tools: [Bash, Read, Write, Edit]
    max_rounds: 5
    cleanup: on_success
```

## High-Level Anatomy

A swarm phase consists of:

1. **Goal** — Injected into every agent's system prompt.
2. **Coordinator** — A single agent that decomposes the goal into tasks, assigns them, and evaluates completion.
3. **Agents** — One or more worker agents that claim tasks, do the work, and mark tasks done.
4. **Task Board** — A shared database table that tracks tasks and their status.
5. **Workspace** — A shared filesystem directory for artifacts and deliverables.

## Execution Loop

The swarm runs a state machine:

1. **Setup** — Creates workspace directories and initialises the database.
2. **Coordinator** — The coordinator agent plans tasks and adds them to the task board.
3. **Agents Running** — All eligible worker agents run concurrently, claiming and completing tasks.
4. **Evaluating** — After all agents finish, the round counter increments. If `max_rounds` is reached, the swarm tears down.
5. **Teardown** — Returns results, optionally cleans up the workspace.

## Swarm-Level Fields

| Field | Type | Description |
|-------|------|-------------|
| `goal` | string | High-level objective. Required. |
| `coordinator` | object | Coordinator configuration. Required. |
| `agents` | array | Worker agent definitions. Required. |
| `max_rounds` | integer | Maximum coordinator evaluation rounds. Default: 5. |
| `max_iterations` | integer | Swarm-wide default max LLM turns per agent. Default: 100. |
| `max_tokens` | integer | Swarm-wide total token budget. |
| `max_cost` | number | Swarm-wide total cost budget. |
| `timeout` | integer | Swarm-level timeout in seconds. |
| `cleanup` | string | Cleanup policy: `on_success`, `always`, or `never`. Default: `on_success`. |
| `workspace` | string | Path for shared files and DB. Default: `.goblin/swarms/{step_id}`. |
| `backend` | string | Database backend: `turso` or `sqlite`. Default: `turso`. |
| `turso` | object | Turso/libSQL backend configuration. |
| `model` / `llm` | string/object | Swarm-wide default LLM configuration. |

For a deep dive into swarm concepts, see the [Swarm](../swarm/overview.md) section.
