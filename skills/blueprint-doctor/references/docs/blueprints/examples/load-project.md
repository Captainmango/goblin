# Example: Load Project

This blueprint clones a repository, loads its project configuration, and then asks an agent to summarise it.

```yaml
version: v1
blueprint:
  name: load_and_summarise
  vars:
    repo: https://github.com/example/repo.git

  phases:
    - id: clone
      prompt: "Clone {repo} into /tmp/project"
      allowed_tools: [Bash]
      on_failure:
        action: abort

    - id: load_project
      load_project: /tmp/project
      on_failure:
        action: abort

    - id: summarise
      prompt: "Summarise what this project is and how it works."
      allowed_tools: [Read, Glob]
```

## What `load_project` Does

When Goblin encounters a `load_project` phase, it reads configuration from the project root:

- `.claude/settings.json` — Project-level hooks and settings
- `CLAUDE.md` and `.claude/CLAUDE.md` — Reference documentation appended to the system prompt
- `.claude/agents/*.md` — Agent definitions for use in later phases

The project path is also set as the working directory for subsequent prompt phases.
