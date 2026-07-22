# Blueprints Overview

The **blueprint** is the core unit of work in Goblin. It is a declarative YAML file that describes:

- What variables are available
- What phases to run
- Which tools each phase may use
- How to handle failures
- How agents should coordinate in a swarm

Every blueprint starts with a `version` field and a `blueprint` block.

## Version Field

The top-level `version` key determines which blueprint runner Goblin uses:

```yaml
version: v1
```

Currently only `v1` is supported. Future versions may introduce new features or schema changes.

## Blueprint Block

The `blueprint` object contains the full definition:

```yaml
blueprint:
  name: my_blueprint
  vars:
    repo: https://github.com/example/repo.git
  defaults:
    model: anthropic/claude-sonnet-4-6
  phases:
    - id: step_one
      prompt: "Do something with {repo}"
      allowed_tools: [Bash]
```

See the following pages for detailed coverage of each key:

- [Structure](structure.md) — Top-level keys
- [Variables and Interpolation](vars-and-interpolation.md) — `{variable}`, `$VAR`, and Jinja2
- [Imports](imports.md) — Reusing blueprint fragments
- [Validation](validation.md) — JSON Schema and the `validate` command
