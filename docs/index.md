# Goblin

**Generative Orchestration of Basic Logic, Instructions, and Notifications**

Goblin is an agent factory and ensemble programming harness. It runs YAML **blueprints** that define multi-step agent workflows and executes them via the OpenHands SDK.

---

## What Goblin Does

- **Blueprint-driven automation** — Describe agent workflows as declarative YAML files with phases, conditions, loops, and failure handling.
- **Tool-augmented agents** — Whitelist exactly which tools each phase may use: Bash, file editing, glob/grep search, git worktrees, swarm coordination, or external MCP servers.
- **Ensemble programming** — Launch coordinated **swarms** of concurrent agents that share a task board and filesystem workspace.
- **Human-in-the-loop** — Pause execution to ask an operator a question via Telegram and block until a reply arrives.
- **Resumable execution** — Automatic checkpointing after every phase lets you resume long-running blueprints from where they left off.

## Quick Links

- [Installation](getting-started/installation.md)
- [Quickstart](getting-started/quickstart.md)
- [Blueprint Reference](blueprints/overview.md)
- [CLI Reference](cli/goblin.md)
- [Swarm Guide](swarm/overview.md)
- [Harness Configuration](configuration/harness.md)

## Example Blueprint

```yaml
version: v1
blueprint:
  name: clone_and_summarise
  vars:
    repo: https://github.com/example/repo.git

  phases:
    - id: clone_repo
      prompt: "Clone {repo} into /tmp/project"
      allowed_tools: [Bash]

    - id: load_project
      load_project: /tmp/project

    - id: summarise_project
      prompt: "Summarise what this project is and how it works."
      allowed_tools: [Read, Glob]
```

Run it:

```sh
goblin run clone_and_summarise.yaml --context '{"repo": "https://github.com/example/repo.git"}'
```
