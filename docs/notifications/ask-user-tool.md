# AskUser Tool

The `AskUser` tool lets an agent pause execution, ask a human operator a question, and block until a reply arrives.

## Usage in Blueprints

```yaml
phases:
  - id: confirm_deploy
    prompt: |
      Ask the user whether to deploy to production.
      Wait for their reply before proceeding.
    allowed_tools: [AskUser]
    notifications:
      label: ops-oncall
```

The agent uses the `AskUser` tool with a `question` parameter. The tool:

1. Sends the question to the notifications relay.
2. The relay forwards it to the paired Telegram user.
3. The tool blocks for up to 15 minutes.
4. When the user replies, the reply text is returned to the agent.

## Question ID Correlation

Each `AskUser` invocation generates a unique `question_id` (UUID). The relay uses this ID to correlate the reply with the original question. This ensures that even if multiple questions are in flight simultaneously, each reply is matched to the correct question.

## Fallback Behaviour

If `notifications` configuration is omitted, the tool falls back to the `default` label:

```yaml
phases:
  - id: ask_anyone
    prompt: "Ask for clarification"
    allowed_tools: [AskUser]
    # Uses label: default
```

## Timeout

The default timeout is 15 minutes. If no reply arrives within that time, the tool returns an error observation:

```
Error: No reply received within timeout
```

## Relay Client

Goblin creates a `RelayClient` from environment variables:

```python
from goblin.notifications.client import make_client

client = make_client()  # Uses RELAY_HOST, RELAY_PORT, RELAY_API_KEY
```

The client sends questions and polls for replies via the relay HTTP API.
