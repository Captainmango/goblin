# Checkpoints and Resume

Goblin automatically checkpoints blueprint execution after every phase. This enables resuming long-running blueprints from where they left off.

## Checkpoint Format

Checkpoints are JSON files at `.goblin/run-<uuid>.json`:

```json
{
  "run_id": "abc123",
  "blueprint_path": "/path/to/blueprint.yaml",
  "context": {"repo": "https://github.com/example/repo.git"},
  "started_at": "2026-07-20T10:00:00+00:00",
  "updated_at": "2026-07-20T10:05:00+00:00",
  "status": "in_progress",
  "phases": {
    "clone_repo": {
      "status": "completed",
      "input": {"context": {"repo": "..."}},
      "output": {"success": true, "message": ["Done"]},
      "started_at": "2026-07-20T10:00:01+00:00",
      "completed_at": "2026-07-20T10:02:00+00:00"
    },
    "analyse": {
      "status": "running",
      ...
    }
  }
}
```

## Checkpoint Status Values

| Status | Description |
|--------|-------------|
| `running` | Phase is currently executing. |
| `completed` | Phase finished successfully. |
| `failed` | Phase failed. |
| `skipped` | Phase was skipped (condition false or checkpoint already done). |

## Resuming

Resume a blueprint from a checkpoint:

```sh
goblin run --resume .goblin/run-abc123.json
```

On resume:

1. Goblin reloads the blueprint YAML and re-validates it.
2. Phases with status `completed` or `skipped` are skipped.
3. Phases with status `failed` are re-run.
4. Any phases still `running` when the process died are removed from the checkpoint and re-run.

## Custom Run Directory

By default, checkpoints are written to `.goblin/` in the current directory. Use `--run-dir` to change this:

```sh
goblin run blueprint.yaml --run-dir /tmp/goblin-runs
```

This creates `/tmp/goblin-runs/.goblin/run-<uuid>.json`.

## File Locking

Checkpoint writes use `filelock` to prevent corruption when multiple processes access the same checkpoint file. The lock timeout is 30 seconds.
