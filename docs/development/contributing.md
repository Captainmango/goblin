# Contributing

Thank you for your interest in contributing to Goblin!

## Code Style

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting. Run quality checks from the repo root:

```sh
task lint
task format
task typecheck
```

Or for a single project:

```sh
task goblin:lint
task goblin:format
task goblin:typecheck
```

## Testing

Run the test suite with pytest:

```sh
task test
```

Or for a single project:

```sh
task goblin:test
task notifications-relay:test
```

## PR Workflow

1. Fork the repository and create a feature branch.
2. Make your changes with tests.
3. Run the full quality check suite.
4. Submit a pull request with a clear description.

## Blueprint Validation

Validate example blueprints:

```sh
task validate
```

This runs `goblin validate` against all blueprints in the `blueprints/` directory.
