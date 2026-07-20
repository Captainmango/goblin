# Environment Variables

Goblin loads environment variables from a `.env` file in the current working directory before resolving configuration.

## Required Variables

| Variable | Used By | Purpose |
|----------|---------|---------|
| `GOBLIN_MODEL_API_KEY` | goblin | LLM API key. Falls back to `MODEL_API_KEY` if not set. Required when running blueprints that use an LLM. |
| `RELAY_API_KEY` | notifications-relay, goblin | API key shared between the relay server and its clients. Required for the relay HTTP API and Goblin's notifications commands. |
| `TELEGRAM_BOT_TOKEN` | notifications-relay | Telegram bot token. Required to send or receive Telegram messages. |

## Optional Variables

| Variable | Used By | Default | Purpose |
|----------|---------|---------|---------|
| `GOBLIN_BASE_URL` | goblin | `OPENHANDS_BASE_URL` | Base URL for the LLM API. |
| `OPENHANDS_SUPPRESS_BANNER` | goblin | `1` | Suppress the OpenHands startup banner. |
| `GOBLIN_BACKEND` | goblin | — | Swarm backend selection (`turso` or `sqlite`). |
| `GOBLIN_TURSO_URL` | goblin | — | Turso/libSQL URL for distributed swarm state. |
| `GOBLIN_TURSO_AUTH_TOKEN` | goblin | — | Turso auth token. |
| `GOBLIN_TURSO_MODE` | goblin | — | Turso connection mode (`local` or `remote`). |
| `GOBLIN_TURSO_LOCAL_PATH` | goblin | — | Local libSQL database path. |
| `GOBLIN_LOG_CONFIG` | goblin | — | Path to a YAML logging configuration file. |
| `RELAY_HOST` | goblin, notifications-relay | `127.0.0.1` | Host where the relay is running. |
| `RELAY_PORT` | goblin, notifications-relay | `8000` | Port where the relay is running. |
| `RELAY_DB_PATH` | notifications-relay | `.goblin/relay.db` | SQLite database path for the relay. |
| `RELAY_PAIRING_TOKEN_TTL_SECONDS` | notifications-relay | `900` | How long pairing tokens remain valid. |
| `RELAY_QUESTION_MAPPING_TTL_SECONDS` | notifications-relay | `3600` | How long question-to-message mappings are kept. |
| `RELAY_LOG_LEVEL` | notifications-relay | — | Logging level override. |
| `RELAY_LOG_FILE` | notifications-relay | — | Log file path override. |
| `RELAY_LOG_QUIET` | notifications-relay | `false` | Suppress stderr logging when `true`. |
| `TELEGRAM_ALLOWED_USER_IDS` | notifications-relay | — | Comma-separated list of allowed Telegram user IDs. |

## Example `.env`

```env
GOBLIN_MODEL_API_KEY=sk-...
RELAY_API_KEY=change-me-in-production
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USER_IDS=123456789
```

## Loading Behaviour

- Goblin loads `.env` from the **current working directory only**.
- It does not walk parent directories.
- Variables already set in the process environment are not overwritten.
