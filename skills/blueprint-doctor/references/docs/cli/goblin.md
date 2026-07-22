# CLI Reference

## Global Command

```sh
goblin --help
```

## `goblin run`

Execute a blueprint.

```sh
goblin run <blueprint_path> [options]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `blueprint_path` | Path to the blueprint YAML file. Optional when using `--resume`. |

### Options

| Option | Description |
|--------|-------------|
| `--context JSON` | Runtime context variables as a JSON string. Default: `{}`. |
| `--log-config PATH` | Path to a YAML logging configuration file. |
| `--log-level LEVEL` | Override log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`. |
| `--log-file PATH` | Override log file path. Default: `goblin-run.jsonl`. |
| `--quiet` | Suppress stderr logging. |
| `--resume PATH` | Path to a `.goblin/run-<id>.json` checkpoint file to resume. |
| `--run-dir DIR` | Directory for `.goblin/` checkpoint files. Default: current directory. |

### Examples

```sh
# Basic run
goblin run blueprints/test.yaml

# With context
goblin run blueprints/test.yaml --context '{"repo": "https://github.com/example/repo.git"}'

# Resume from checkpoint
goblin run --resume .goblin/run-abc123.json

# Custom run directory
goblin run blueprints/test.yaml --run-dir /tmp/goblin-runs
```

### Resume Behaviour

- You cannot specify both `blueprint_path` and `--resume`.
- On resume, phases already completed or skipped in the checkpoint are skipped.
- Failed phases are re-run.

## `goblin validate`

Validate one or more blueprint YAML files against the JSON Schema.

```sh
goblin validate <blueprints...>
```

### Examples

```sh
goblin validate my_blueprint.yaml
goblin validate blueprints/*.yaml
```

Exits with code 1 if any blueprint is invalid.

## `goblin notifications`

Subcommands for the notifications relay. See the [Notifications](../notifications/overview.md) section.

```sh
goblin notifications --help
```
