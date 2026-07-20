# Swarm Coordinator

The **coordinator** is a single agent responsible for decomposing the swarm's goal into tasks, assigning them to workers, and evaluating when the work is complete.

## Configuration

```yaml
swarm:
  coordinator:
    prompt: |
      Break down the goal into tasks. Assign implementation to the coder
      agent and test writing to the tester agent. Monitor progress and
      mark the swarm complete when all tasks pass.
    allowed_tools: [Read]
    max_iterations: 50
    max_cost: 2.0
```

## Fields

| Field | Required | Description |
|-------|----------|-------------|
| `prompt` | Yes | Coordinator instruction prompt. Supports `{variable}` interpolation. |
| `allowed_tools` | No | Tool whitelist. The coordinator **always** gets `SwarmTasks` and **never** gets `Bash`. |
| `max_iterations` | No | Max LLM turns for a single coordinator run. Default: 50. |
| `max_tokens` | No | Max tokens for a single coordinator run. |
| `max_cost` | No | Max cost for a single coordinator run. |
| `model` / `llm` | No | LLM configuration for the coordinator. |

## Why No Bash?

The coordinator only ever gets the typed `SwarmTasks` tool (and any explicitly listed non-Bash tools). It has no way to write files or forge database rows for tasks it didn't actually delegate. This is a security boundary: the coordinator plans, but workers execute.

## Task Board Rendering

Each coordinator invocation is a fresh, stateless conversation. To prevent the coordinator from re-planning from scratch every round, Goblin renders a **task board snapshot** and appends it to the coordinator's prompt:

```
### Current Task Board
- #1 [pending] Implement add function (coder)
- #2 [done] Write tests for add (tester)
```

This lets the coordinator see progress made in earlier rounds.

## Completion Detection

The swarm transitions to teardown when:

1. The coordinator returns `{"done": true}` **and**
2. There are no outstanding tasks on the task board.

If the coordinator says "done" but tasks remain pending, the swarm continues to another round.
