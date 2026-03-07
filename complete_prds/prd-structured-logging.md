# PRD: Structured Logging

## Summary

Replace all `print()` calls across the Goblin codebase with Python's built-in `logging` module, emit structured JSON logs by default, write to a `.jsonl` log file, and allow end users to configure the logger via an external YAML file. This lays the groundwork for human-in-the-loop flows where machine-readable log streams are essential for UI integration and auditability.

## Problem

Today Goblin uses bare `print()` for all runtime output: step lifecycle events, failure handling, project loading, swarm agent errors, JSON parse retries, and final workflow results. This causes several problems:

1. **No verbosity control.** Users cannot silence noisy output or increase detail for debugging. Tests that exercise code paths with `print()` pollute stdout.
2. **No structured output.** All messages are free-form strings. Downstream tooling (TUI dashboards, CI log aggregators, future human-in-the-loop UIs) cannot programmatically parse events.
3. **No log routing.** Output goes exclusively to stdout. There is no way to write logs to a file, send them to an external service, or separate operational messages from workflow results.
4. **`WorkflowRunner.run()` dumps results raw.** `runner.py:27` calls `print(results)` unconditionally, conflating structured return data with informational output.

### Current `print()` inventory

| File | Count | Categories |
|------|-------|------------|
| `blueprint.py` | 10 | Project loading (settings, refs, agents, hooks), step failure handling, condition skips |
| `swarm.py` | 1 (in Goblin code) + 10 (in CLI script template) | Agent failure; CLI script uses `print()` for its own stdout/stderr which is correct and out of scope |
| `step.py` | 1 | JSON parse retry warnings |
| `runner.py` | 1 | Raw results dump |

**Total in-scope:** 13 `print()` calls to replace across 4 files.

## Goals

1. All runtime messages use `logging` instead of `print()`.
2. JSON-structured log output by default (one JSON object per line).
3. Logs written to a `.jsonl` file by default, with stdout as optional.
4. Users can supply a YAML logging config file to customize handlers, formatters, levels, and filters.
5. Log events carry structured context fields (step ID, swarm agent name, workflow name, etc.) for machine consumption.
6. The design supports future human-in-the-loop flows where a UI process consumes the log stream in real time.

## Non-Goals

- Replacing `print()` calls inside `_SWARMDB_CLI_SCRIPT` (the embedded CLI wrapper). That script runs in a subprocess with its own stdout/stderr and is not part of Goblin's logging surface.
- Building a TUI dashboard (separate roadmap item).
- Sending logs to external services (users can configure this via their own YAML config).

## Design

### Logger hierarchy

Use a single root logger namespace: `goblin`.

```
goblin              # root — configured by the YAML file or defaults
goblin.runner       # WorkflowRunner lifecycle
goblin.blueprint    # V1Blueprint orchestration, project loading
goblin.step         # PromptStep execution, JSON parse/repair
goblin.swarm        # SwarmStep lifecycle, agent execution
```

Each module acquires its logger via `logging.getLogger(__name__)`, which naturally produces the hierarchy above given the package structure.

### Default configuration (no YAML file)

When no user config is provided, Goblin applies a programmatic default:

- **Handler:** `FileHandler` writing to `goblin-run.jsonl` (in the current working directory).
- **Formatter:** `JSONFormatter` (custom class, see below) that emits one JSON object per line.
- **Level:** `INFO`.
- **Stderr handler:** A secondary `StreamHandler` on stderr at `WARNING` level with a plain-text format, so critical errors are always visible in the terminal.

### JSON log format

Each line is a self-contained JSON object:

```json
{
  "timestamp": "2026-03-07T14:23:01.123456Z",
  "level": "INFO",
  "logger": "goblin.blueprint",
  "message": "Skipping step — condition not met",
  "step_id": "deploy",
  "workflow": "release_pipeline",
  "event": "step.condition_skip"
}
```

**Standard fields:** `timestamp`, `level`, `logger`, `message`.
**Context fields:** Attached via `logging.LoggerAdapter` or `extra={}` on individual calls. Common context keys: `workflow`, `step_id`, `agent_name`, `round`, `attempt`, `event`.

**Event taxonomy** (the `event` field):

| Event | Level | Source |
|-------|-------|--------|
| `workflow.start` | INFO | runner |
| `workflow.complete` | INFO | runner |
| `workflow.results` | DEBUG | runner (replaces the `print(results)`) |
| `project.settings_loaded` | INFO | blueprint |
| `project.settings_missing` | WARNING | blueprint |
| `project.hooks_registered` | INFO | blueprint |
| `project.hook_failed` | WARNING | blueprint |
| `project.reference_loaded` | INFO | blueprint |
| `project.agent_loaded` | INFO | blueprint |
| `step.condition_skip` | INFO | blueprint |
| `step.failed` | WARNING | blueprint |
| `step.skip_on_failure` | WARNING | blueprint |
| `step.retry` | WARNING | blueprint |
| `step.retry_exhausted` | ERROR | blueprint |
| `step.abort` | ERROR | blueprint |
| `step.json_parse_failed` | WARNING | step |
| `swarm.agent_failed` | ERROR | swarm |

### User-configurable YAML logging

Users can provide a logging config via:

1. **CLI flag:** `goblin run --log-config logging.yaml workflow.yaml`
2. **Environment variable:** `GOBLIN_LOG_CONFIG=logging.yaml`

The YAML file is loaded with `logging.config.dictConfig()` after being parsed with `yaml.safe_load()`. This is the standard Python pattern and supports the full `logging.config` schema: handlers, formatters, filters, loggers, root logger, etc.

Example user config:

```yaml
version: 1
disable_existing_loggers: false

formatters:
  json:
    class: goblin.logging.JSONFormatter
  plain:
    format: "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

handlers:
  file:
    class: logging.FileHandler
    filename: /var/log/goblin/run.jsonl
    formatter: json
  console:
    class: logging.StreamHandler
    stream: ext://sys.stderr
    formatter: plain
    level: DEBUG

loggers:
  goblin:
    level: DEBUG
    handlers: [file, console]
```

### CLI integration

```
goblin run [OPTIONS] BLUEPRINT_PATH

Options:
  --context      JSON    Context dict passed to the workflow
  --log-config   PATH    Path to a YAML logging configuration file
  --log-level    TEXT    Override root log level (DEBUG, INFO, WARNING, ERROR)
  --log-file     PATH    Override default log file path (default: goblin-run.jsonl)
  --quiet        FLAG    Suppress stderr handler entirely
```

`--log-level`, `--log-file`, and `--quiet` are convenience shortcuts that modify the default config without requiring a full YAML file. If `--log-config` is provided, these flags are ignored (the YAML file takes full control).

### Implementation: `goblin/logging.py`

New module containing:

1. **`JSONFormatter(logging.Formatter)`** — Serializes `LogRecord` to a JSON line. Extracts `extra` fields (step_id, agent_name, event, etc.) and includes them as top-level keys.
2. **`configure_logging(config_path, log_level, log_file, quiet)`** — Applies YAML `dictConfig` if a path is given, otherwise builds the programmatic default. Called once at CLI entry.
3. **`WorkflowLoggerAdapter(logging.LoggerAdapter)`** — Optional adapter that injects `workflow` name into all log records for a given run.

### Migration mapping

Each `print()` call maps to a specific log call:

| Current code | Replacement |
|---|---|
| `print(f"Loaded project settings from {settings_path}")` | `logger.info("Loaded project settings", extra={"event": "project.settings_loaded", "path": settings_path})` |
| `print(f"No .claude/settings.json found in {path}")` | `logger.warning("No .claude/settings.json found", extra={"event": "project.settings_missing", "path": path})` |
| `print(f"Project hook failed (exit {result.returncode}): {cmd}")` | `logger.warning("Project hook failed", extra={"event": "project.hook_failed", "cmd": cmd, "exit_code": result.returncode})` |
| `print(f"Registered {len(commands)} ...")` | `logger.info("Registered project hooks", extra={"event": "project.hooks_registered", "count": len(commands), "event_type": event_type, "registry_name": registry_name})` |
| `print(f"Loaded reference from {ref_path}")` | `logger.info("Loaded reference", extra={"event": "project.reference_loaded", "path": ref_path})` |
| `print(f"Loaded agent definition: {agent_name}")` | `logger.info("Loaded agent definition", extra={"event": "project.agent_loaded", "agent": agent_name})` |
| `print(f"Step {step.id} failed")` | `logger.warning("Step failed", extra={"event": "step.failed", "step_id": step.id})` |
| `print(f"Skipping failed step {step.id}...")` | `logger.warning("Skipping failed step", extra={"event": "step.skip_on_failure", "step_id": step.id})` |
| `print(f"Retrying step {step.id}...")` | `logger.warning("Retrying step", extra={"event": "step.retry", "step_id": step.id, "attempt": attempt, "max_retries": max_retries})` |
| `print(f"Step {step.id} failed after...")` | `logger.error("Step failed after retries", extra={"event": "step.retry_exhausted", "step_id": step.id, "max_retries": max_retries})` |
| `print(f"Aborting workflow due to...")` | `logger.error("Aborting workflow", extra={"event": "step.abort", "step_id": step.id})` |
| `print(f"Skipping {step.id} — condition not met")` | `logger.info("Skipping step — condition not met", extra={"event": "step.condition_skip", "step_id": step.id})` |
| `print(f"JSON parse failed...")` | `logger.warning("JSON parse failed", extra={"event": "step.json_parse_failed", "attempt": attempt+1, "max_attempts": 1+retries, "error": str(exc)})` |
| `print(results)` (runner.py) | `logger.debug("Workflow results", extra={"event": "workflow.results", "results": results})` |
| `print(f"Agent '{name}' failed: {e}")` | `logger.error("Swarm agent failed", extra={"event": "swarm.agent_failed", "agent": name, "error": str(e)})` |

### Human-in-the-loop considerations

The structured JSON log stream is designed to serve as the event backbone for future human-in-the-loop flows:

- **Real-time tailing:** A UI process can `tail -f goblin-run.jsonl` and parse events as they arrive. Each line is self-contained JSON.
- **Approval events:** Future approval/pause steps will emit events like `step.awaiting_approval` with structured context. A UI can watch for these and prompt the user.
- **Swarm state transitions:** Swarm state machine transitions (coordinator, agents_running, evaluating, etc.) should also emit log events to give the UI visibility into concurrent agent progress.
- **Correlation:** The `workflow`, `step_id`, and `agent_name` fields allow a UI to correlate events to specific parts of the execution tree.
- **Separate channels:** By using `logging`, a future implementation can add a dedicated handler (e.g., a queue-backed handler or WebSocket handler) for the UI without modifying any of the log call sites.

### Test impact

- Tests that currently capture `print()` output via `capsys` will need to switch to asserting against log records. Use `caplog` (pytest's built-in log capture fixture) instead.
- The `_SWARMDB_CLI_SCRIPT` print statements are **not changed** — they run in a subprocess and are unrelated to Goblin's logging.
- `JSONFormatter` should have its own unit tests verifying the output schema.
- `configure_logging` should be tested with both YAML config and programmatic defaults.

### Dependencies

- **No new dependencies.** `logging`, `logging.config`, and `json` are all stdlib. YAML parsing already uses `pyyaml`.

## Rollout

1. Add `goblin/logging.py` with `JSONFormatter`, `configure_logging`, and `WorkflowLoggerAdapter`.
2. Wire `configure_logging()` into `goblin/cli.py` (called before `WorkflowRunner`), add CLI options.
3. Replace all `print()` calls in `blueprint.py`, `step.py`, `swarm.py`, and `runner.py` with structured log calls.
4. Update tests to use `caplog` instead of `capsys`.
5. Add unit tests for `JSONFormatter` and `configure_logging`.
6. Update `CLAUDE.md` architecture section and `spec/v1.json` if any schema fields are affected.
7. Remove the two roadmap items from `ROADMAP.md`.
