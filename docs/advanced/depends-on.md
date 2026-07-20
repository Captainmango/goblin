# Depends On

The `depends_on` field lets you build directed acyclic graphs (DAGs) inside a blueprint, overriding the default sequential execution order.

## Syntax

```yaml
phases:
  - id: build
    prompt: "Build the project"
    allowed_tools: [Bash]

  - id: test
    prompt: "Run tests"
    allowed_tools: [Bash]
    depends_on: [build]

  - id: lint
    prompt: "Run linter"
    allowed_tools: [Bash]
    depends_on: [build]

  - id: deploy
    prompt: "Deploy to staging"
    allowed_tools: [Bash]
    depends_on: [test, lint]
```

## Execution Order Rules

1. Phases with no `depends_on` run first.
2. A phase only starts after all its dependencies have finished (completed, skipped, or failed).
3. Phases in the same dependency wave run concurrently.
4. If a dependency fails with `action: abort`, dependent phases never run.

## Load Project Barrier

`load_project` phases act as a special barrier:

- When a `load_project` phase is ready to run, only `load_project` phases run in that wave.
- At most one `load_project` phase runs per wave.

This ensures project settings are loaded before other phases begin.

## DAG Validation

The orchestrator does not explicitly validate for cycles. If you create a circular dependency, the orchestrator will eventually exhaust the ready set and exit with pending phases remaining.

## Example: Parallel Analysis

```yaml
phases:
  - id: fetch_data
    prompt: "Fetch the dataset"
    allowed_tools: [Bash]

  - id: analyse_a
    prompt: "Analyse column A"
    allowed_tools: [Bash]
    depends_on: [fetch_data]

  - id: analyse_b
    prompt: "Analyse column B"
    allowed_tools: [Bash]
    depends_on: [fetch_data]

  - id: report
    prompt: "Combine analyses"
    allowed_tools: [Bash]
    depends_on: [analyse_a, analyse_b]
```

In this example, `analyse_a` and `analyse_b` run concurrently after `fetch_data` completes.
