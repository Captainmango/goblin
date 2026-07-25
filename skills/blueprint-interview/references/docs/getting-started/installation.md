# Installation

## Prerequisites

- **Python 3.13+**
- **[uv](https://docs.astral.sh/uv/)** — The recommended Python package manager

## Installing from PyPI

```sh
uv tool install goblin
```

Or with `pip`:

```sh
pip install goblin
```

## Installing from Source

Clone the repository and install all workspace dependencies:

```sh
git clone <repo-url>
cd goblin
uv sync --all-packages --all-groups
```

This creates a single virtual environment and installs every workspace package (`goblin` and `goblin-common`).

## Verifying the Install

```sh
goblin --help
```

You should see the top-level CLI help with the `run` and `validate` commands.
