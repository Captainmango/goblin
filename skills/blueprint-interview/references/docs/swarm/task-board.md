# Task Board

The **task board** is a shared database table that tracks tasks and their status. It is the central coordination mechanism for swarm phases.

## Task Lifecycle

1. **Created** (`pending`) — The coordinator creates a task via `SwarmTasks.create_task`.
2. **Claimed** (`in_progress`) — A worker agent claims the task via `SwarmAgent.claim_task`.
3. **Completed** (`done`) — The worker marks the task done via `SwarmAgent.complete_task`.
4. **Failed** (`failed`) — A task may be marked failed if an agent encounters an error.

## Task Matching

When the swarm determines which agents to run, it matches pending tasks to agents:

1. **By name** — If a task has `assigned_to: coder`, only the `coder` agent can claim it.
2. **By role** — If a task has `required_role: backend`, only agents with `roles: [backend]` can claim it.
3. **Unrestricted** — If neither `assigned_to` nor `required_role` is set, any agent can claim it.

## Task Board Schema

Each task has these fields:

| Field | Description |
|-------|-------------|
| `id` | Auto-incrementing integer. |
| `title` | Short task title. |
| `description` | Longer description (optional). |
| `status` | `pending`, `in_progress`, `done`, or `failed`. |
| `assigned_to` | Agent name (optional). |
| `required_role` | Role restriction (optional). |
| `result` | Result summary set by `complete_task` (optional). |

## Coordinator Visibility

The coordinator sees the task board as rendered text in its prompt:

```
### Current Task Board
- #1 [pending] Implement add function (coder)
- #2 [done] Write tests for add (tester)
- #3 [in_progress] Implement subtract function (coder)
```

This prevents the coordinator from creating duplicate tasks and lets it evaluate overall progress.
