# Load Project Phase

The `load_project` phase loads project-level configuration from a directory. It does not invoke an LLM; instead, it reads files and registers settings for use by subsequent phases.

## Syntax

```yaml
- id: load_project
  load_project: /path/to/project
```

The path supports `{variable}` interpolation:

```yaml
- id: load_project
  load_project: "{clone_dir}"
```

## What Gets Loaded

### `.claude/settings.json`

If present, Goblin reads hooks configuration and registers shell command hooks:

- `PreToolUse` commands are registered as `project_pre_tool`
- `PostToolUse` commands are registered as `project_post_tool`

### Reference Documentation

Goblin reads and concatenates:

- `CLAUDE.md`
- `.claude/CLAUDE.md`

The combined text is appended to the system prompt of all subsequent prompt phases.

### Agent Definitions

Goblin scans `.claude/agents/*.md` and creates an `AgentDefinition` for each file:

- Filename (without `.md`) becomes the agent name.
- File content becomes the system prompt.

These definitions are available for use in swarm phases.

## Working Directory

The project path becomes the working directory for subsequent prompt phases (unless overridden by `workspace` configuration). It also serves as the fallback `working_dir` for ACP phases that do not declare `harness.working_dir`.

## ACP interaction

ACP harnesses read their own native project config (`CLAUDE.md` hierarchy,
`.claude/settings.json`, `.mcp.json`) directly from the session `cwd`, so `load_project`
is unnecessary for pure-ACP blueprints. It remains useful when:

- You want `project_path` to be the default `working_dir` for ACP phases.
- The blueprint mixes OpenHands phases (which receive `load_project` refs/hooks) with ACP
  phases.

## Example

```yaml
version: v1
blueprint:
  name: analyse_project
  vars:
    project_path: /tmp/my-project

  phases:
    - id: load
      load_project: "{project_path}"

    - id: analyse
      prompt: "Analyse the codebase architecture"
      allowed_tools: [Read, Glob, Grep]
```
