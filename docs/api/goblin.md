# API Reference

The `goblin` package provides the following public modules and classes.

## Blueprint

```python
from goblin.blueprint import V1Blueprint

blueprint = V1Blueprint(spec={...})
results = await blueprint.run(context={"repo": "..."})
```

- `V1Blueprint` — Parses and executes a v1 blueprint spec.
- `Blueprint` — Protocol interface for blueprint runners.

## Runner

```python
from goblin.runner import BlueprintRunner

runner = BlueprintRunner("blueprint.yaml", run_dir="/tmp")
results = await runner.run(context={...})
```

- `BlueprintRunner` — Loads, validates, and executes a blueprint from a file path.
- `BlueprintRunner.resume(path)` — Class method to resume from a checkpoint file.

## Checkpoint

```python
from goblin.checkpoint import Checkpoint

checkpoint = Checkpoint(run_dir=".", run_id="abc", blueprint_path="...", context={...})
```

- `Checkpoint` — JSON-based checkpoint with async file locking.
- `Checkpoint.load(path)` — Load a checkpoint from a file.

## Validation

```python
from goblin.validation import validate_blueprint, format_errors

errors = validate_blueprint("blueprint.yaml")
print(format_errors(errors))
```

- `validate_blueprint(path, raw=None)` — Validate a blueprint against the JSON Schema.
- `format_errors(errors)` — Pretty-print validation errors.

## Tools

```python
from goblin.tools.tool_map import resolve_tools, build_tool_filter_regex
```

- `resolve_tools(names, tool_params)` — Map blueprint tool names to OpenHands `Tool` specs.
- `build_tool_filter_regex(names)` — Build a regex for MCP tool filtering.

## Swarm

```python
from goblin.swarm import SwarmPhase
from goblin.swarm_db import SwarmDB
```

- `SwarmPhase` — Orchestrates multi-agent swarms.
- `SwarmDB` — Database abstraction for swarm state.

## Hooks

```python
from goblin.hooks import hook, register_hook, HOOK_REGISTRY
```

- `hook(name, *, command, timeout)` — Decorator to register shell command hooks.
- `register_hook(name, definition)` — Manual hook registration.

## Configuration

```python
from goblin.utils.config import get_config, load_config, GoblinConfig
```

- `GoblinConfig` — Central configuration dataclass.
- `get_config()` — Returns the module-level config singleton.
- `load_config()` — Creates a fresh config from the current environment.

## Logging

```python
from goblin_common.logging import configure_logging
```

- `configure_logging(...)` — Structured JSONL logging setup.
