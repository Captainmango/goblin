# Phases Overview

A **phase** is a single step in a blueprint. Phases are executed by the orchestrator, which handles sequencing, checkpointing, and failure recovery.

## Execution Model

By default, phases run **sequentially** in the order they appear in the `phases` array. You can override this with `depends_on` to build DAGs (see [Depends On](../advanced/depends-on.md)).

Phases of the same dependency wave run **concurrently**.

## Phase Types

There are three phase types:

| Type | Key | Description |
|------|-----|-------------|
| `prompt` | `prompt` | Sends a prompt to an LLM agent with a whitelisted tool set. |
| `load_project` | `load_project` | Loads project configuration from a directory. |
| `swarm` | `swarm` | Launches a multi-agent swarm with a coordinator and worker agents. |

Every phase must have an `id` and exactly one of the above keys.

## Checkpointing

Goblin automatically checkpoints after every phase. The checkpoint file is a JSON document at `.goblin/run-<uuid>.json` that records:

- Run ID and blueprint path
- Context variables
- Each phase's input, output, and status (`running`, `completed`, `failed`, `skipped`)

This enables resuming a blueprint from where it left off using `--resume`.

## Common Phase Fields

All phase types support these fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique identifier for this phase. |
| `condition` | string | Jinja2 expression. Phase only runs if this evaluates to truthy. |
| `foreach` | string | Integer, Jinja2 expression, or file path. Runs the phase multiple times. |
| `on_failure` | object | Failure handling: `action` (`skip`, `retry`, `abort`), `max_retries`, `notify`. |
| `depends_on` | array | Phase IDs that must finish before this phase starts. |

## Orchestrator Rules

1. `load_project` phases act as a barrier: when a load_project is ready, only load_project phases run in that wave, and at most one per wave.
2. Phases already completed or skipped in a checkpoint are skipped on resume.
3. Failed phases are re-run on resume (to preserve original behavior).
4. If any phase aborts, the entire blueprint stops.
