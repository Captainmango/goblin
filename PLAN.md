# Plan: Notifications Relay

## Goal

Build `notifications-relay` as a **standalone web server** that bridges Goblin (and other consumers) to Telegram for two-way notifications. It is channel-agnostic at the core and ships with Telegram as the first provider (via `python-telegram-bot`). The relay owns pairing state, the Telegram bot lifecycle, and a small inbox for replies; Goblin only needs an HTTP client and a per-user pairing token.

In v1 the relay supports:

- **Outbound**: agents push alerts, questions, and summaries to operators.
- **Inbound replies**: operators reply to agent questions via Telegram's reply feature; the relay stores the reply in an inbox and the agent fetches it by polling.

Unsolicited Telegram messages, bot commands, and webhook push are out of scope for v1.

## Scope

- In scope:
  - FastAPI web server in `notifications-relay/`.
  - Channel-agnostic core with Telegram as the first provider.
  - CLI pairing flow: CLI asks relay for a token, user sends it to the bot, token marries Telegram `user_id`/`chat_id`.
  - SQLite persistence of pairings and reply mappings inside the relay.
  - Relay API key authentication + per-user pairing tokens.
  - Polling inbox for inbound replies to agent questions.
  - Reply-to correlation: Telegram replies to a bot message are matched to the `question_id` of that message.
  - Blocking `ask()` client helper that sends a question and polls for a reply.
  - Python client library (`RelayClient`) in the relay package for Goblin to consume.
  - Thin `goblin notifications` CLI wrapper for local token management.
- Out of scope for v1:
  - Unsolicited inbound Telegram messages.
  - Telegram bot commands (e.g. `/status`, `/pause`).
  - Webhook push from relay to agent.
  - Persistent chat history beyond active reply mappings.
  - Rich media beyond text.
  - Inline keyboards.
  - Slack or other channels.
  - Multi-bot support.

## Proposed Architecture

```text
┌─────────────────────────────────────────────────────────────────────┐
│                          Goblin / CLI                               │
│  ┌─────────────────────┐       ┌─────────────────────────────────┐  │
│  │   RelayClient       │       │   .goblin/notifications.json    │  │
│  │  (HTTP client)      │◄─────►│   + TELEGRAM_TOKEN_ envvars     │  │
│  └──────────┬──────────┘       └─────────────────────────────────┘  │
│             │                                                       │
│             │ POST /pairings, POST /send, GET /inbox                │
│             │                                                       │
└─────────────┼───────────────────────────────────────────────────────┘
              │
              │ HTTP + RELAY_API_KEY
              │
┌─────────────▼───────────────────────────────────────────────────────┐
│                     notifications-relay (FastAPI)                   │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                      API layer                                │  │
│  │  /health  /pairings  /send  /inbox  /pairings/{token}         │  │
│  └───────────────────────┬───────────────────────────────────────┘  │
│                          │                                          │
│  ┌───────────────────────▼───────────────────────────────────────┐  │
│  │              Channel-agnostic core                            │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐   │  │
│  │  │ Pairing     │  │ Send        │  │ Inbox               │   │  │
│  │  │ service     │  │ dispatcher  │  │ (reply storage)     │   │  │
│  │  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘   │  │
│  │         │                │                    │              │  │
│  │  ┌──────▼────────────────▼────────────────────▼──────┐       │  │
│  │  │              Provider registry                     │       │  │
│  │  │  ┌────────────────────────────────────────────┐   │       │  │
│  │  │  │           Telegram provider                │   │       │  │
│  │  │  │  (python-telegram-bot: send + reply-to)   │   │       │  │
│  │  │  └────────────────────────────────────────────┘   │       │  │
│  │  └───────────────────────────────────────────────────┘       │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                          │                                          │
│                   Telegram Bot API                                  │
└─────────────────────────────────────────────────────────────────────┘
```

## 1. Dependencies

Add to `notifications-relay/pyproject.toml`:

```toml
dependencies = [
    "fastapi>=0.110",
    "uvicorn[standard]>=0.30",
    "python-telegram-bot>=21.0",
    "pydantic>=2.0",
    "click>=8.0",
    "httpx>=0.27",
    "aiosqlite>=0.20",
]
```

Add `notifications-relay` as a workspace dependency in `goblin/pyproject.toml` (editable) so Goblin can import `RelayClient`.

Use `uv add --project notifications-relay fastapi uvicorn httpx aiosqlite ...` and `uv add --project goblin notifications-relay`.

## 2. Configuration

### Relay environment variables (`notifications-relay`)

- `RELAY_API_KEY` — **mandatory**. Project-level API key for all relay management and send endpoints.
- `RELAY_HOST` — Host to bind the server. Default: `127.0.0.1`.
- `RELAY_PORT` — Port to bind the server. Default: `8000`.
- `RELAY_DB_PATH` — SQLite path for pairings and inbox. Default: `.goblin/relay.db`.
- `TELEGRAM_BOT_TOKEN` — Bot token from @BotFather. Required for the Telegram provider.
- `TELEGRAM_ALLOWED_USER_IDS` — Comma-separated list of Telegram user IDs allowed to complete pairing. If empty, any user can pair.
- `RELAY_PAIRING_TOKEN_TTL_SECONDS` — Time-to-live for unclaimed pairing tokens. Default: `900` (15 minutes).
- `RELAY_QUESTION_MAPPING_TTL_SECONDS` — Time-to-live for `(chat_id, telegram_message_id) → question_id` mappings. Default: `3600` (1 hour).

If `TELEGRAM_BOT_TOKEN` is not set, the Telegram provider is not registered and `POST /pairings` returns an error.

### Consumer environment variables (`goblin` / CLI)

- `RELAY_HOST`, `RELAY_PORT` — relay address for the client.
- `RELAY_API_KEY` — project-level API key.
- `TELEGRAM_TOKEN_<LABEL>` — Optional dotenv override for a pairing token (e.g. `TELEGRAM_TOKEN_ALICE=abc123`).
- `NOTIFICATIONS_CONFIG_PATH` — Path to the local JSON pairing store. Default: `.goblin/notifications.json`.

## 3. Module Layout

### `notifications-relay/`

```text
notifications_relay/
├── __init__.py
├── __main__.py          # python -m notifications_relay
├── cli.py               # Click CLI: serve, pair, send, ask, inbox, list, revoke
├── config.py            # RelayConfig (Pydantic)
├── main.py              # FastAPI app factory
├── api/
│   ├── __init__.py
│   ├── deps.py          # auth dependencies (API key)
│   ├── health.py        # GET /health
│   ├── pairings.py      # /pairings endpoints
│   ├── send.py          # POST /send
│   └── inbox.py         # GET /inbox
├── client.py            # RelayClient for consumers
├── core/
│   ├── __init__.py
│   ├── models.py        # Pydantic request/response models
│   ├── pairing.py       # PairingService
│   ├── dispatcher.py    # route send requests to providers
│   └── inbox.py         # InboxService
├── store.py             # SQLite-backed Store (pairings + inbox + mappings)
├── provider.py          # NotificationProvider protocol / base class
├── telegram/
│   ├── __init__.py
│   ├── config.py        # TelegramConfig
│   ├── provider.py      # TelegramNotificationProvider
│   ├── bot.py           # PTB Application lifecycle
│   ├── handlers.py      # /pair, /unpair, reply-to handler
│   └── security.py      # allow-list checks
└── exceptions.py        # custom exceptions
```

### `goblin/` integration

```text
goblin/
├── cli.py               # add `goblin notifications` subcommand group
├── notifications/
│   ├── __init__.py
│   ├── cli.py           # thin wrappers around RelayClient
│   └── client.py        # (optional) Goblin-specific client helpers
```

Goblin depends on `notifications-relay` as a workspace package and imports `RelayClient` from it.

## 4. Pairing Flow

The pairing flow is **relay-centric** and starts from the CLI.

### Pairing steps (Telegram)

1. Operator runs the pairing command:
   ```sh
   goblin notifications pair --label alice --username alice@example.com
   # or
   python -m notifications_relay pair --label alice
   ```
2. The CLI calls `POST /pairings` on the relay with the project `RELAY_API_KEY` and an optional label/username. The relay generates a single-use token, stores a pending pairing record with `expires_at`, and returns the token.
3. The CLI writes the token to `.goblin/notifications.json` under the supplied label:
   ```json
   {
     "telegram": {
       "alice": "abc123..."
     }
   }
   ```
   It also prints the token and instructions for the user.
4. The operator opens Telegram and sends `/pair <token>` to the bot.
5. The Telegram provider validates the user against `TELEGRAM_ALLOWED_USER_IDS` (if configured). If allowed, it records the Telegram `user_id` and `chat_id` against the token, marks the pairing as claimed, and replies with a welcome message.
6. The token is now bound to that Telegram user. On every subsequent send or ask, the agent presents the same token to the relay, which resolves it to the stored `chat_id`.

### Telegram pairing commands

- `/pair <token>` — claim a pending pairing token. Fails if the token is unknown, expired, or already used.
- `/unpair` — remove the operator’s stored pairing from the relay (operator-side cleanup). Requires the user to have an active pairing.

## 5. SQLite Persistence

Pairings, reply mappings, and inbox replies are persisted inside the relay only. Goblin does not access this database directly.

### Store responsibilities (`notifications_relay/store.py`)

- Create/migrate tables on first use.
- Pairing CRUD: create, claim, get by token, list active, revoke, delete expired pending.
- Question mapping: store `(chat_id, telegram_message_id) → question_id` with TTL; resolve by `chat_id` + `telegram_message_id`; delete expired.
- Inbox: insert reply, fetch and delete by `question_id`, delete expired.

### Schema

```sql
CREATE TABLE IF NOT EXISTS pairings (
    token TEXT PRIMARY KEY,
    label TEXT,
    username TEXT,
    telegram_user_id TEXT,
    chat_id TEXT,
    status TEXT NOT NULL,        -- 'pending' | 'claimed' | 'revoked'
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    claimed_at TEXT
);

CREATE TABLE IF NOT EXISTS question_mappings (
    telegram_message_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    token TEXT NOT NULL,
    question_id TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (chat_id, telegram_message_id)
);

CREATE TABLE IF NOT EXISTS inbox (
    id TEXT PRIMARY KEY,
    token TEXT NOT NULL,
    telegram_user_id TEXT NOT NULL,
    chat_id TEXT NOT NULL,
    question_id TEXT,
    text TEXT NOT NULL,
    received_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
```

All timestamp columns store ISO8601 strings in UTC.

Expired pending tokens, expired question mappings, and expired inbox entries are cleaned up periodically.

## 6. Relay API

All endpoints except `/health` require the `Authorization: Bearer <RELAY_API_KEY>` header.

### `GET /health`

Public. Returns status and whether the Telegram provider is registered.

```json
{
  "status": "ok",
  "telegram_provider_registered": true
}
```

### `POST /pairings`

Create a pending pairing. The relay generates and returns the token.

Request:

```json
{
  "label": "alice",
  "username": "alice@example.com"
}
```

Response:

```json
{
  "token": "abc123...",
  "expires_at": "2026-01-01T12:15:00Z"
}
```

### `GET /pairings`

List active (claimed, non-expired) pairings.

### `DELETE /pairings/{token}`

Revoke a pairing by token. Returns 204 or 404.

### `DELETE /pairings/expired`

Trigger cleanup of expired pending tokens. Returns the number deleted.

### `POST /send`

Send a message to the Telegram user bound to the provided pairing token.

Request:

```json
{
  "token": "abc123...",
  "text": "Build complete.",
  "question_id": "q-123",
  "idempotency_key": "run-42-build-complete"
}
```

If `question_id` is provided, the relay stores the resulting Telegram `message_id` so it can correlate future replies.

If `idempotency_key` is provided, the relay deduplicates sends with the same key within a reasonable TTL (e.g., 24 hours). Duplicate requests return the stored response without sending a second Telegram message.

Response:

```json
{
  "message_id": 12345,
  "channel": "telegram"
}
```

### `GET /inbox`

Fetch and consume a reply. At least one filter is required.

Query parameters:

- `question_id` — return the reply for this question ID.
- `token` — return any replies for this pairing token.

Response:

```json
{
  "messages": [
    {
      "id": "msg-1",
      "token": "abc123...",
      "telegram_user_id": "123456",
      "chat_id": "123456",
      "question_id": "q-123",
      "text": "Yes, proceed.",
      "received_at": "2026-01-01T12:05:00Z"
    }
  ]
}
```

Messages are deleted as they are returned.

## 7. Outbound Channel (Agent → Relay → Operator)

### `RelayClient` (`notifications_relay/client.py`)

A thin async HTTP client wrapping the relay endpoints:

```python
class RelayClient:
    def __init__(self, base_url: str, api_key: str) -> None: ...

    async def create_pairing(self, label: str | None = None, username: str | None = None) -> PairingResponse: ...
    async def send(self, token: str, text: str, question_id: str | None = None) -> SendResponse: ...
    async def ask(self, token: str, text: str, question_id: str, timeout: float = 300.0) -> InboxMessage: ...
    async def list_pairings(self) -> list[Pairing]: ...
    async def revoke(self, token: str) -> None: ...
```

### Blocking `ask()`

`ask()` is the primary agent interface for questions:

1. Calls `send(token, text, question_id=question_id)`.
2. Polls `GET /inbox?question_id=...` with a short interval.
3. Returns the first matching reply.
4. Raises `AskTimeoutError` if no reply arrives within `timeout`.

### Token resolution in Goblin

Goblin resolves a token in this order:

1. Explicit token passed to the send/ask call.
2. Environment variable matching the label (e.g. `TELEGRAM_TOKEN_ALICE`).
3. `.goblin/notifications.json` entry for the label.

If no token is found, the call fails fast with a clear error.

### Telegram provider specifics

`TelegramNotificationProvider.send(token: str, text: str, question_id: str | None = None, idempotency_key: str | None = None) -> SendResponse`

- Look up `chat_id` from the pairing store by token.
- If no active pairing is found, log `telegram_pairing_not_found` and return an error.
- Truncate or split messages longer than Telegram’s 4096-character limit.
- If `question_id` is provided, store `(chat_id, telegram_message_id) → question_id` with TTL.
- Return the sent Telegram message ID.

## 8. Inbound Channel (Telegram → Relay → Agent)

### What is stored

Only Telegram messages that are **replies to a recent bot message** are stored. The relay uses the original bot message's `chat_id` and `telegram_message_id` to look up the `question_id` mapping. The reply is then inserted into the inbox with that `question_id`.

Unsolicited messages, non-reply messages, and replies to expired mappings are dropped (or logged) in v1.

### Telegram inbound handlers

- `/pair <token>` — claim or re-pair the operator.
- `/unpair` — remove the operator’s stored pairing.
- **Reply-to handler** — for any update with `message.reply_to_message`, resolve the original `(chat_id, telegram_message_id)` to a `question_id`, validate the sender has an active pairing, and insert the reply into the inbox.

Security: every inbound update is filtered by `security.is_allowed(user_id)` for pairing commands. Reply storage only accepts messages from users with active pairings.

### Polling model

The agent polls `GET /inbox` for replies. Webhook push from relay to agent is out of scope for v1 but reserved as a future option.

## 9. Notification Manager Lifecycle in Goblin

Because the relay is external, Goblin does not need a singleton manager that owns a bot lifecycle. Instead:

- Goblin creates a `RelayClient` when needed.
- Pairing tokens are loaded from `.goblin/notifications.json` or envvars.
- Sends are stateless HTTP calls to the relay.
- `ask()` blocks and polls the relay for a reply.
- There is no startup/shutdown lifecycle for the notification layer beyond HTTP connection management.

A thin `NotificationManager` wrapper can cache the `RelayClient` and expose `send_alert(label, text)` and `ask(label, text, question_id)` helpers, but it does not start background tasks.

## 10. Concurrency Model

- The relay is an ASGI app (FastAPI + Uvicorn).
- The Telegram provider uses `python-telegram-bot` polling. It runs in a dedicated `asyncio` event loop inside a `ThreadPoolExecutor` that is started during the FastAPI lifespan startup and shut down gracefully during lifespan teardown. This keeps PTB's long-polling loop off the main ASGI event loop.
- `RelayClient` uses `httpx.AsyncClient` for async HTTP.
- All store operations use `aiosqlite` from async endpoints.
- Cleanup of expired tokens, mappings, and inbox entries runs on a background schedule and can be triggered via `DELETE /pairings/expired`.

## 11. Error Handling & Resilience

- All provider-native errors (e.g. `TelegramError`) are caught and logged with channel-specific event names such as `telegram_send_failed`.
- HTTP errors from the relay return structured JSON responses and appropriate status codes.
- Retry transient network/rate-limit errors with exponential backoff inside the Telegram provider.
- Never let notification failures crash the agent; outbound failures are logged and surfaced as failed send responses.
- On relay startup failure, exit with a clear error message.

## 12. Security

- `RELAY_API_KEY` must be set manually and never committed.
- Pairing tokens are single-use, time-bounded, and deleted on expiry.
- Telegram allow-list (`TELEGRAM_ALLOWED_USER_IDS`) is enforced when a user attempts to claim a token.
- Local token storage defaults to `.goblin/notifications.json` with file permissions set to `0o600`.
- No shell commands are accepted from notification text.
- Inbox replies are only accepted from users with active pairings.

## 13. Testing

Add tests under `notifications-relay/tests/`:

- `test_config_validation` — required API key, defaults, invalid TTL.
- `test_pairing_store` — create, claim, list, revoke, expired cleanup.
- `test_question_mapping_store` — TTL, composite-key resolution, cleanup.
- `test_inbox_store` — insert, fetch-and-delete, expiry.
- `test_api_pairings` — `POST /pairings`, `GET /pairings`, `DELETE /pairings/{token}`, `DELETE /pairings/expired` with auth.
- `test_api_send` — `POST /send` resolves token and stores question mapping.
- `test_api_inbox` — `GET /inbox?question_id=...` returns and deletes replies.
- `test_telegram_claim_flow` — `/pair <token>` from allowed user updates the store.
- `test_telegram_reply_flow` — reply to a bot message creates an inbox entry with the correct `question_id`.
- `test_relay_client` — mocked HTTP interactions for `RelayClient`, including blocking `ask()` timeout.

Add tests under `goblin/tests/notifications/`:

- `test_token_resolution` — envvar vs JSON file precedence.
- `test_cli_pair` — invokes `goblin notifications pair` and verifies file write.
- `test_cli_ask` — mocked `RelayClient.ask()` through the Goblin CLI.

Use `pytest-asyncio`, `httpx.ASGITransport` for FastAPI tests, and `unittest.mock` for the PTB `Application` and `Update` objects.

## 14. Implementation Order

1. Set up `notifications-relay` package structure, dependencies, and `RelayConfig`.
2. Implement `Store` with SQLite schema for pairings, question mappings, and inbox.
3. Implement `PairingService` (create, claim, revoke, cleanup).
4. Build FastAPI app, auth dependency, and `/health` endpoint.
5. Implement `/pairings` management endpoints.
6. Add Telegram provider, bot lifecycle, `/pair`, `/unpair`, and reply-to handler.
7. Implement `/send` endpoint, dispatcher, and question-mapping storage.
8. Implement `/inbox` endpoint and `InboxService`.
9. Implement `RelayClient` with `send()` and blocking `ask()`.
10. Add `notifications-relay` CLI (`serve`, `pair`, `send`, `ask`, `inbox`, `list`, `revoke`).
11. Add `goblin notifications` CLI wrapper and local JSON/env token resolution.
12. Add tests for relay and Goblin integration.
13. Run `task lint`, `task typecheck`, `task format`, `pytest`.

## 15. Decisions

- **Architecture**: `notifications-relay` is a standalone FastAPI web server; Goblin communicates with it over HTTP.
- **Authentication**: project-level `RELAY_API_KEY` for agent→relay + per-user pairing token for recipient routing.
- **Token ownership**: the relay generates single-use pairing tokens with a 15-minute default TTL.
- **Pairing flow**: CLI requests token from relay → user sends `/pair <token>` to bot → bot binds Telegram IDs to token.
- **Token storage**: consumer keeps tokens locally in `.goblin/notifications.json` (600 permissions) with optional `TELEGRAM_TOKEN_*` envvar overrides.
- **Inbound replies**: stored only when the user replies to a recent bot message; correlation uses Telegram `reply_to_message` and a stored `(chat_id, telegram_message_id) → question_id` mapping.
- **Unsolicited messages**: dropped in v1. Bot commands and webhook push are deferred.
- **Agent interaction**: `RelayClient.ask(token, text, question_id, timeout)` sends a question and polls `GET /inbox` until a reply arrives.
- **Client library**: `RelayClient` lives in the `notifications-relay` package and is imported by Goblin.
- **Future**: webhook push, Slack provider, inline keyboards, and general bot commands are deferred to phase 2.
