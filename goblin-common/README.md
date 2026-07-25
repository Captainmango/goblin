# goblin-common

Shared utilities for the Goblin monorepo.

This package contains configuration and logging helpers used by `goblin`. Keep shared code here rather than duplicating it across projects.

## Setup

This project is part of the workspace monorepo. From the repo root:

```sh
uv sync --all-packages
```

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

## Usage

Import helpers in the other workspace projects:

```python
from goblin_common.config import env, secret_env, load_dotenv_from_cwd
from goblin_common.logging import configure_logging, JSONFormatter
```

### Configuration

- `env(name, default, fallback=None)` — read an environment variable and coerce it to the type of `default` (`int`, `bool`, `str`, etc.).
- `secret_env(name, fallback=None)` — read a sensitive environment variable as a pydantic `SecretStr`.
- `load_dotenv_from_cwd()` — load a `.env` file from the current working directory only.

### Logging

- `configure_logging(logger_name, ...)` — configure JSON file logging plus an optional stderr handler.
- `JSONFormatter` — formats log records as single-line JSON objects.

Default log files are written to `logs/<logger_name>.jsonl` under the workspace root.

## Development

Run project tasks from the repo root or from inside this directory:

```sh
task goblin-common:lint
task goblin-common:format
task goblin-common:typecheck
task goblin-common:test
```

Or directly:

```sh
uv run ruff check .
uv run ruff format .
uv run pyrefly check
uv run pytest
```
