# Quickstart

This guide walks you through running a built-in blueprint and passing context variables.

## Run the Test Blueprint

The repository includes a sample blueprint at `blueprints/test.yaml`:

```yaml
version: v1
blueprint:
  name: my_blueprint
  vars:
    repo: https://github.com/parkhub/justpark.git
  defaults:
    model: anthropic/MiniMax-M2.5

  phases:
    - id: clone_repo
      prompt: "Clone {repo} into /tmp/project"
      allowed_tools: [Bash]
      on_failure:
        action: skip

    - id: load_project
      load_project: /tmp/project
      on_failure:
        action: abort

    - id: summarise_project
      prompt: "Summarise what this project is and how it works."
      allowed_tools: [Read, Glob]
```

Run it:

```sh
goblin run blueprints/test.yaml
```

## Pass Context Variables

Override blueprint `vars` at runtime with `--context`:

```sh
goblin run blueprints/test.yaml --context '{"repo": "https://github.com/example/repo.git"}'
```

The `--context` value must be valid, unescaped JSON.

## Observe JSONL Logs

Goblin writes structured JSONL logs to `goblin-run.jsonl` by default. Each line is a JSON object with an `event` field:

```sh
tail -f goblin-run.jsonl | jq '{event, phase_id, message}'
```

You can override the log file with `--log-file` or suppress stderr output with `--quiet`.
