# Notifications Overview

Goblin's notifications system enables human-in-the-loop workflows via Telegram. It consists of three components:

1. **Relay HTTP API** — A FastAPI service that receives questions from Goblin and forwards them to Telegram.
2. **Telegram Bot** — A bot that sends messages to operators and receives replies.
3. **Operator** — A human who receives questions and sends replies through Telegram.

## Architecture

```
Goblin Agent -> Relay API -> Telegram Bot -> Operator
                 ^                           |
                 +---------------------------+
                              (reply)
```

When an agent uses the `AskUser` tool, Goblin sends the question to the relay. The relay forwards it to the paired Telegram user. When the user replies, the relay correlates the reply with the original question and returns it to the agent.

## Components

- **notifications-relay** — A FastAPI HTTP service and Click CLI. Lives in the `notifications-relay/` workspace project.
- **goblin notifications CLI** — A subcommand group under `goblin` for pairing and sending messages.

## Prerequisites

- A Telegram bot created with BotFather.
- `TELEGRAM_BOT_TOKEN` environment variable.
- `RELAY_API_KEY` shared between Goblin and the relay.

See [Setup](setup.md) for detailed instructions.
