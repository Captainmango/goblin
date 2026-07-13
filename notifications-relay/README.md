# Notifications Relay

FastAPI HTTP service and Click CLI for sending and receiving Telegram notifications in the Goblin ecosystem.

The relay exposes an authenticated HTTP API for pairing, sending messages, asking questions, and consuming replies. When `TELEGRAM_BOT_TOKEN` is configured, it also runs the Telegram bot that delivers messages and captures replies.

## Setup

This project is part of the workspace monorepo. From the repo root:

```sh
uv sync --all-packages
```

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

## Running

Start the HTTP server:

```sh
uv run notifications-relay serve
```

The API listens on `http://127.0.0.1:8000` by default.

Pair a label with the Telegram bot:

```sh
uv run notifications-relay pair --label my-laptop
```

Send a message:

```sh
uv run notifications-relay send --label my-laptop "Hello from Goblin"
```

Ask a question and wait for a reply:

```sh
uv run notifications-relay ask --label my-laptop "Deploy to production?" --question-id deploy-123
```

List active pairings:

```sh
uv run notifications-relay list
```

Revoke a pairing:

```sh
uv run notifications-relay revoke --label my-laptop
```

Fetch replies from the inbox:

```sh
uv run notifications-relay inbox --question-id deploy-123
```

## Environment Variables

Create a `.env` file in the working directory where you run commands.

### Required

| Variable | Purpose |
|----------|---------|
| `RELAY_API_KEY` | API key shared between the relay server and its clients. Required for the HTTP API. |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token. Required to send or receive Telegram messages. |

### Optional

| Variable | Default | Purpose |
|----------|---------|---------|
| `RELAY_HOST` | `127.0.0.1` | Host to bind the HTTP server to. |
| `RELAY_PORT` | `8000` | Port to bind the HTTP server to. |
| `RELAY_DB_PATH` | `.goblin/relay.db` | SQLite database path. |
| `RELAY_PAIRING_TOKEN_TTL_SECONDS` | `900` | How long pairing tokens remain valid. |
| `RELAY_QUESTION_MAPPING_TTL_SECONDS` | `3600` | How long question-to-message mappings are kept. |
| `RELAY_LOG_LEVEL` | — | Logging level override. |
| `RELAY_LOG_FILE` | — | Log file path override. |
| `RELAY_LOG_QUIET` | `false` | Suppress stderr logging when `true`. |
| `TELEGRAM_ALLOWED_USER_IDS` | — | Comma-separated list of allowed Telegram user IDs. |

### Example `.env`

```env
RELAY_API_KEY=change-me-in-production
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USER_IDS=123456789
```

## Development

Run project tasks from the repo root or from inside this directory:

```sh
task notifications-relay:lint
task notifications-relay:format
task notifications-relay:typecheck
task notifications-relay:test
```

Or directly:

```sh
uv run ruff check .
uv run ruff format .
uv run pyrefly check
uv run pytest
```
