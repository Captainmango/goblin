# Swarm Overview

A **swarm** is a multi-agent system where a coordinator decomposes a goal into tasks and assigns them to worker agents. Workers claim tasks, do the work, and mark them done. The swarm repeats this cycle for a configurable number of rounds.

## State Machine

The swarm runs through these states:

```
Setup -> Coordinator -> Agents Running -> Evaluating -> (repeat) -> Teardown
```

1. **Setup** — Creates workspace directories and initialises the swarm database.
2. **Coordinator** — The coordinator agent plans tasks and adds them to the task board.
3. **Agents Running** — All eligible worker agents run concurrently, claiming and completing tasks.
4. **Evaluating** — After agents finish, the round counter increments. If `max_rounds` is reached, the swarm transitions to teardown.
5. **Teardown** — Returns results and optionally cleans up the workspace.

## When to Use a Swarm

Use a swarm when:

- A task can be decomposed into independent subtasks.
- Different specialisations are needed (e.g., backend coder + QA tester).
- You want agents to work concurrently rather than sequentially.

## Minimal Example

```yaml
version: v1
blueprint:
  name: swarm_demo
  phases:
    - id: build_feature
      swarm:
        goal: "Create a simple Python calculator with add, subtract, multiply, divide and tests."
        coordinator:
          prompt: |
            Break down the goal into tasks. Assign implementation to the coder
            agent and test writing to the tester agent. Monitor progress and
            mark the swarm complete when all tasks pass.
          allowed_tools: [Read]
        agents:
          - name: coder
            prompt: "You are a Python developer. Implement each task and write files to the shared workspace."
            allowed_tools: [Bash, Read, Write, Edit]
          - name: tester
            prompt: "You are a QA engineer. Write pytest tests for completed implementation tasks."
            allowed_tools: [Bash, Read, Write, Edit]
        max_rounds: 3
        cleanup: never
```

See the following pages for detailed coverage of swarm concepts:

- [Coordinator](coordinator.md)
- [Agents](agents.md)
- [Task Board](task-board.md)
- [Backends](backends.md)
- [Cleanup](cleanup.md)
