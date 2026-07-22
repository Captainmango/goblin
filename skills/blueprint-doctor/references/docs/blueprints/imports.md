# Imports

The `imports` array lets you reuse blueprint fragments by merging them inline before validation or execution.

## Syntax

```yaml
blueprint:
  imports:
    - path: ./common/defaults.yaml
    - path: ./setup.yaml
      as: setup
```

| Key | Required | Description |
|-----|----------|-------------|
| `path` | Yes | Relative path to the blueprint file to import. Resolved relative to the importing blueprint's directory. |
| `as` | No | Namespace prefix for imported phase IDs. All phase IDs from the imported blueprint are prefixed with this value. |

## How Imports Work

1. Goblin reads the importing blueprint.
2. For each entry in `imports`, it loads the referenced file and resolves its own imports recursively.
3. Imported phases are merged into the `phases` array.
4. If `as` is specified, every imported phase ID is prefixed (e.g., `setup__clone_repo`).

## Example

**`common/defaults.yaml`**:

```yaml
version: v1
blueprint:
  name: common_defaults
  defaults:
    model: anthropic/claude-sonnet-4-6
    hooks:
      pre_tool: [check_prometheus]
```

**`main.yaml`**:

```yaml
version: v1
blueprint:
  name: main
  imports:
    - path: ./common/defaults.yaml

  phases:
    - id: build
      prompt: "Build the project"
      allowed_tools: [Bash]
```

The imported `defaults` are merged into `main.yaml`'s blueprint-level defaults.

## Namespacing

Use `as` to avoid phase ID collisions:

```yaml
imports:
  - path: ./setup.yaml
    as: setup

phases:
  - id: build
    prompt: "Build after setup"
    allowed_tools: [Bash]
    depends_on: [setup__init]
```

If `setup.yaml` defines a phase with `id: init`, it becomes `setup__init` in the merged blueprint.
