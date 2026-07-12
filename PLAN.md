# Plan: Two-Way Notifications Mechanism

## Goal

Add a lightweight, two-way notifications layer to Goblin. The layer is channel-agnostic at the core and ships with Telegram as the first provider (via `python-telegram-bot`). It must allow Goblin to **push** messages to operators (alerts, summaries, questions) and **receive** messages or commands from operators to control or influence a running blueprint. Future channels such as Slack should fit the same abstraction.

## Scope

- In scope: channel-agnostic notifications API, Telegram provider, bot lifecycle, message sending, command handling, **operator pairing flow**, **SQLite persistence of recipients**, configuration.
- Out of scope: persistent chat history, rich media beyond text, inline keyboards (phase 2), Slack implementation (phase 2), multi-bot support.

## Proposed Architecture

```text
┌─────────────────────────────────────────────┐
│              Goblin runner                  │
│  ┌─────────────────────────────────────┐    │
│  │     Notifications manager           │    │
│  │  ┌────────────┐   ┌─────────────┐   │    │
│  │  │  Outbound  │   │   Inbound   │   │    │
│  │  │   router   │   │   router    │   │    │
│  │  └─────┬──────┘   └──────┬──────┘   │    │
│  │        │                 │          │    │
│  │  ┌─────▼─────────────────▼──────┐   │    │
│  │  │      Provider registry       │   │    │
│  │  │  ┌────────────────────────┐  │   │    │
│  │  │  │    Telegram provider   │  │   │    │
│  │  │  │  (python-telegram-bot) │  │   │    │
│  │  │  └────────────────────────┘  │   │    │
│  │  └──────────────────────────────┘   │    │
│  └─────────────────────────────────────┘    │
│                    │                        │
│            Telegram Bot API                 │
└─────────────────────────────────────────────┘
```

## 1. Dependencies

Add to `pyproject.toml`:

```toml
dependencies = [
    "python-telegram-bot>=21.0",
]
```

Use `uv add python-telegram-bot`.

## 2. Configuration

Use environment variables loaded via `dotenv`. Channel-specific config lives under per-channel models; the core notifications layer is channel-agnostic.

### Notifications layer config

- `NOTIFICATIONS_DB_PATH` — SQLite path for recipient pairings. Default: `.goblin/notifications.db`.

### Telegram provider config

- `TELEGRAM_BOT_TOKEN` — Bot token from @BotFather.
- `TELEGRAM_ALLOWED_USER_IDS` — Comma-separated list of Telegram user IDs allowed to pair with the bot (security). If empty, all users can pair.
- The Telegram provider always runs in **polling** mode. Webhooks are not supported.

A `NotificationConfig` model validates the shared settings. A `TelegramConfig` model validates Telegram-specific settings. If `TELEGRAM_BOT_TOKEN` is not set, the Telegram provider is simply not registered.

## 3. Module Layout

Create a channel-agnostic `goblin_cli/notifications/` package with Telegram as the first provider:

```text
goblin_cli/notifications/
├── __init__.py
├── config.py              # NotificationConfig
├── manager.py             # NotificationManager singleton + provider registry
├── provider.py            # NotificationProvider protocol / base class
├── store.py               # SQLite-backed RecipientStore
├── types.py               # NotificationMessage, InboundNotificationEvent, Recipient
├── telegram/
│   ├── __init__.py
│   ├── config.py          # TelegramConfig
│   ├── provider.py        # TelegramNotificationProvider
│   ├── handlers.py        # PTB command/message handlers
│   └── security.py        # allow-list checks
└── slack/                 # placeholder for phase 2
    └── __init__.py
```

## 4. Pairing Flow

To avoid manual wiring, Goblin provides an explicit pairing flow. Each channel implements its own pairing mechanism. For Telegram, a user becomes a known operator by messaging the bot. The notifications layer stores the pairing in a channel-agnostic SQLite table so it survives restarts and can one day support Slack, email, etc.

### Pairing steps (Telegram)

1. Operator opens Telegram and sends `/start` to the bot.
2. The Telegram provider checks `TELEGRAM_ALLOWED_USER_IDS` if it is configured.
   - If the allow-list is empty, any user can pair.
   - If the allow-list is set and the user is in it, the bot replies with a welcome message and stores the pair.
   - If the allow-list is set and the user is not in it, the bot replies with an unauthorized message and drops the update.
3. The provider records a `Recipient` with `channel="telegram"`, `channel_user_id=<telegram user id>`, `address=<chat id>`, `username`, and `paired_at` via `RecipientStore`.
4. From then on, Goblin can send messages to that operator without any further manual configuration.

### Telegram pairing commands

- `/start` — pair or re-pair the operator. Updates `address` (chat ID) if it changed.
- `/unpair` — remove the operator from the local store (operator-side cleanup).
- `/pairings` (admin/allowed users only) — list currently paired operators.

### Sequence diagram

```text
Operator                 Telegram provider            RecipientStore
   │      /start             │                              │
   │ ──────────────────────> │                              │
   │                         │  is_allowed(user_id)?        │
   │                         │ ─────────────────────────>   │
   │                         │                              │
   │                         │  upsert(Recipient)           │
   │                         │ ─────────────────────────>   │
   │      welcome            │                              │
   │ <────────────────────── │                              │
```

## 5. SQLite Persistence

Operator pairings are persisted in a channel-agnostic SQLite table so a restarted runner can still message known operators, regardless of which channel they use.

### `RecipientStore` (`goblin_cli/notifications/store.py`)

Responsibilities:

- Create/migrate the `recipients` table on first use.
- `upsert(recipient: Recipient)` — insert or update a recipient.
- `get_address(channel, channel_user_id) -> str | None` — resolve the delivery address (e.g. Telegram chat ID) for a recipient.
- `list_recipients(channel=None) -> list[Recipient]` — return stored recipients, optionally filtered by channel.
- `delete(channel, channel_user_id) -> bool` — remove a recipient.

### Schema

```sql
CREATE TABLE IF NOT EXISTS recipients (
    channel TEXT NOT NULL,
    channel_user_id TEXT NOT NULL,
    address TEXT NOT NULL,
    username TEXT,
    paired_at TEXT NOT NULL,
    PRIMARY KEY (channel, channel_user_id)
);
```

### Location

Fixed path: `.goblin/notifications.db`. The store is created on `NotificationManager` startup if it does not exist.

## 6. Outbound Channel (Goblin → Operator)

The `NotificationManager` exposes a channel-agnostic send API and routes messages to the appropriate provider.

`NotificationManager.send(message: NotificationMessage, recipient: Recipient | None = None) -> DeliveryResult`

- `NotificationMessage` contains `text`, optional `title`, and future fields for channel-specific payloads.
- If `recipient` is provided, the manager routes to `recipient.channel`.
- If `recipient` is omitted, the manager can broadcast to all recipients or use a default channel (configurable later).
- Each provider implements `send(recipient: Recipient, message: NotificationMessage)`.

### Telegram provider specifics

`TelegramNotificationProvider.send_message(text: str, chat_id: int | None = None, channel_user_id: str | None = None) -> MessageId`

- Resolve `chat_id` in this order:
  1. Explicit `chat_id` argument.
  2. `RecipientStore.get_address("telegram", channel_user_id)`.
- If no chat ID can be resolved, log `telegram_no_chat_id` and return `None`.
- Truncate or split messages longer than Telegram’s 4096-character limit.
- Return the sent message ID so callers can later edit or reply.

Helpers built on top:

- `send_alert(title, body)` — formatted with a prefix.
- `send_question(question, options)` — text + keyboard (phase 2).
- `send_phase_complete(phase_name, result_summary)`.
- `send_to_user(channel_user_id, text)` — convenience wrapper that resolves chat ID from the store.

## 7. Inbound Channel (Telegram → Goblin)

Each provider translates its native inbound events into `InboundNotificationEvent` objects and pushes them to the `NotificationManager`, which dispatches them to the registered hook.

### Telegram inbound handlers

`python-telegram-bot` handlers registered on the `Application`:

- `/start` — pair or re-pair the operator; store the recipient in SQLite.
- `/status` — report current blueprint/run state if any.
- `/pause` — request pause of the running orchestrator (requires hook).
- `/resume` — request resume.
- `/unpair` — remove the operator’s stored pairing from the local database.
- `/pairings` — list currently paired operators (allowed users only).
- `/message` or free-form text — forward operator text into Goblin via the event bus / hook.

Security: every inbound update is filtered by `security.is_allowed(user_id)`. Unknown users receive a silent drop or a single "unauthorized" reply.

## 8. Notification Manager Lifecycle (Singleton)

The `NotificationManager` runs as a **singleton** shared across blueprint runs. It owns the `RecipientStore` and the provider registry. Providers are started once, kept alive in the background, and stopped only when the Goblin process exits.

Reasons:

- Avoids the cost of starting/stopping polling for every blueprint run.
- Allows operators to pair or send messages even when no blueprint is running.
- Matches the shared SQLite store model.
- Keeps the core channel-agnostic; new providers plug into the same manager.

Lifecycle:

1. On first use (e.g. first blueprint run), create and start the singleton `NotificationManager`.
2. The manager initializes the `RecipientStore`.
3. The manager registers and starts any configured providers (e.g. Telegram if `TELEGRAM_BOT_TOKEN` is set).
4. Subsequent runs reuse the existing manager.
5. On Goblin process shutdown, stop all providers and close the SQLite store.

A module-level `get_notification_manager()` factory returns the singleton instance. If no providers are configured, the manager starts in a no-op mode and logs a warning on send attempts.

## 9. Integration With the Orchestrator

Introduce a small event bus abstraction so the notifications layer stays decoupled from the orchestrator internals.

- Providers emit `InboundNotificationEvent` objects.
- The `NotificationManager` dispatches these events to a registered hook (`notification_received`).
- For outbound messages, phases can request `NotificationManager.send(...)` through a tool or hook.

### Inbound integration: hook only

Inbound operator messages are **not** surfaced as a dedicated phase. Blueprints opt in by registering a hook:

```yaml
hooks:
  notification_received: my_hook_handler
```

The hook receives an `InboundNotificationEvent` containing `channel`, `channel_user_id`, `address`, `text`, and `timestamp`. The blueprint decides how to react. Telegram-specific blueprints can still inspect `event.channel == "telegram"` if needed.

### Outbound integration

Phases can call `NotificationManager.send_alert(...)` or `NotificationManager.send_to_recipient(...)` directly via a tool or hook.

### Phase lifecycle hook

Optional `on_phase_complete` hook sends a phase summary via the notifications layer if configured.

## 10. Concurrency Model

- Each provider runs its own IO loop or background task (e.g. PTB `Application` for Telegram).
- `NotificationManager` exposes async `send_*` methods that can be awaited from Goblin’s existing async runner.
- Inbound handlers are async and only place events onto a shared `asyncio.Queue` to avoid blocking provider updates.
- A worker coroutine drains the queue and dispatches events to registered consumers.

## 11. Error Handling & Resilience

- Providers catch their native errors (e.g. `TelegramError`) and log with channel-specific event names such as `telegram_send_failed`.
- Retry transient errors (network, rate limits) with exponential backoff.
- Never let notification failures crash the orchestrator; outbound failures are logged, not raised.
- On provider startup failure, log an error and continue the blueprint run unless `NOTIFICATIONS_REQUIRED=true`.

## 12. Security

- Tokens stored only in environment variables / `.env`, never committed.
- Provider-specific allow-lists enforced on every inbound update (e.g. `TELEGRAM_ALLOWED_USER_IDS` for Telegram).
- No shell commands accepted from notification text; commands map to an explicit allow-list of safe actions.

## 13. Testing

Add tests under `tests/notifications/`:

- `test_notification_config_validation` — shared config defaults and validation.
- `test_notification_manager_singleton` — `get_notification_manager()` returns the same instance across calls.
- `test_notification_manager_routing` — message routed to the correct provider.
- `test_recipient_store` — CRUD operations for `RecipientStore`.

Add Telegram-specific tests under `tests/notifications/telegram/`:

- `test_telegram_config_validation` — missing token, invalid user list.
- `test_telegram_provider_send_message` — mocked `Application.bot.send_message`.
- `test_telegram_inbound_handler_queue` — fake PTB update, assert `InboundNotificationEvent` enqueued.
- `test_telegram_pairing_flow` — `/start` from an allowed user creates a recipient in SQLite.

Use `pytest-asyncio` and `unittest.mock` for the PTB `Application` and `Update` objects. Use a temporary SQLite file for store tests.

## 14. Implementation Order

1. Add dependency and define `NotificationConfig` + `TelegramConfig`.
2. Define core abstractions: `NotificationProvider`, `NotificationMessage`, `InboundNotificationEvent`, `Recipient`.
3. Implement `RecipientStore` with SQLite schema and CRUD operations.
4. Implement `NotificationManager` as a singleton with provider registry and outbound routing.
5. Implement the Telegram provider (`TelegramNotificationProvider`) with outbound send helpers.
6. Implement Telegram security allow-list.
7. Implement Telegram inbound handlers + pairing flow + event queue + tests.
8. Wire `RecipientStore` into Telegram provider so outbound sends resolve chat IDs automatically.
9. Register the `notification_received` hook and dispatch inbound events to it.
10. Wire singleton `NotificationManager` lifecycle into `BlueprintRunner` / `PhaseOrchestrator` startup and shutdown.
11. Add a sample blueprint under `blueprints/` demonstrating `/status` and outbound alerts.
12. Run `task lint`, `task typecheck`, `task format`, `pytest`.

## 15. Decisions

- **Module name**: `goblin_cli/notifications/`, with Telegram as the first provider and Slack as a planned phase 2 provider.
- **Core abstraction**: channel-agnostic `NotificationManager` + `NotificationProvider` protocol. Telegram-specific code lives under `goblin_cli/notifications/telegram/`.
- **Persistence**: channel-agnostic `RecipientStore` with a `recipients(channel, channel_user_id, address, ...)` table.
- **Manager lifecycle**: singleton `NotificationManager` shared across blueprint runs. Started on first use, stopped on process shutdown.
- **Inbound integration**: hook only. Operator messages are delivered via a `notification_received` hook; no dedicated notification phase in v1.
- **Future relay extraction**: when the Telegram layer moves to its own app, the SQLite store moves with it. Goblin will query the relay rather than keep its own pairing database.
