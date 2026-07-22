# Notifications Setup

## Create a Telegram Bot

1. Message [@BotFather](https://t.me/botfather) on Telegram.
2. Send `/newbot` and follow the prompts.
3. Save the bot token you receive.

## Set Environment Variables

```env
TELEGRAM_BOT_TOKEN=<your-bot-token>
RELAY_API_KEY=change-me-in-production
```

## Start the Relay

```sh
uv run notifications-relay serve
```

The API listens on `http://127.0.0.1:8000` by default.

## Pair a Label

A **label** identifies a paired operator (e.g., a specific Telegram user). Before Goblin can ask questions, you must pair a label:

```sh
goblin notifications pair --label my-laptop
```

Or directly with the relay CLI:

```sh
uv run notifications-relay pair --label my-laptop
```

This generates a pairing token. The operator sends this token to the Telegram bot to complete pairing.

## Labels

Labels let you route questions to different operators:

```yaml
phases:
  - id: ask_ops
    prompt: "Ask ops for approval"
    allowed_tools: [AskUser]
    notifications:
      label: ops-oncall

  - id: ask_pm
    prompt: "Ask the product manager for priority"
    allowed_tools: [AskUser]
    notifications:
      label: product-manager
```

Each label must be paired independently.

## Send a Test Message

```sh
uv run notifications-relay send --label my-laptop "Hello from Goblin"
```

## Ask a Question

```sh
uv run notifications-relay ask --label my-laptop "Deploy to production?" --question-id deploy-123
```

The command blocks until a reply is received or the timeout elapses.
