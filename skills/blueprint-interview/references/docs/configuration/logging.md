# Logging

Goblin uses structured JSONL logging via `goblin-common`.

## Default Behaviour

By default, Goblin writes logs to `goblin-run.jsonl` in the current directory and also logs to stderr.

Each log line is a JSON object:

```json
{
  "timestamp": "2026-07-20T10:00:00+00:00",
  "level": "INFO",
  "logger": "goblin.blueprint",
  "event": "blueprint.start",
  "run_id": "...",
  "message": "Blueprint started"
}
```

## CLI Overrides

The `goblin run` command supports logging overrides:

```sh
goblin run my.yaml --log-level DEBUG
goblin run my.yaml --log-file /var/log/goblin.jsonl
goblin run my.yaml --quiet  # Suppress stderr
```

## YAML Log Configuration

For advanced configuration, provide a YAML logging config file:

```sh
goblin run my.yaml --log-config logging.yaml
```

Or set the environment variable:

```env
GOBLIN_LOG_CONFIG=logging.yaml
```

## Programmatic Configuration

```python
from goblin_common.logging import configure_logging

configure_logging(
    logger_name="goblin",
    config_path="logging.yaml",
    log_level="DEBUG",
    log_file="/var/log/goblin.jsonl",
    quiet=False,
)
```

## Common Events

| Event | Description |
|-------|-------------|
| `blueprint.start` | Blueprint execution began. |
| `blueprint.complete` | Blueprint execution finished. |
| `blueprint.resume` | Resuming from checkpoint. |
| `phase.failed` | A phase failed. |
| `phase.retry` | Retrying a failed phase. |
| `phase.condition_skip` | Phase skipped due to condition. |
| `phase.checkpoint_skip` | Phase skipped because already in checkpoint. |
| `swarm.agent_failed` | A swarm agent failed. |
