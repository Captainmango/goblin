# Cost Control

Goblin provides a `CircuitBreaker` to prevent runaway spending or token usage.

## Limits

Set limits at the phase or swarm level:

```yaml
phases:
  - id: analyse
    prompt: "Analyse the codebase"
    max_iterations: 50
    max_tokens: 50000
    max_cost: 2.0
```

| Field | Description |
|-------|-------------|
| `max_iterations` | Maximum LLM turns. Each `arun()` call counts as one iteration. |
| `max_tokens` | Maximum cumulative prompt + completion tokens. |
| `max_cost` | Maximum cumulative cost in the model's currency (usually USD). |

## CircuitBreaker Behaviour

After each agent conversation, the circuit breaker checks whether any limit is exceeded:

- If `max_iterations` is reached, the phase fails with `success: false`.
- If `max_tokens` is reached, the phase fails with `success: false`.
- If `max_cost` is reached, the phase fails with `success: false`.

## Spend Tracking

Phase results include a `spend` object with accumulated metrics:

```json
{
  "success": true,
  "message": ["Done"],
  "spend": {
    "iterations": 12,
    "tokens": 15234,
    "cost": 0.45
  }
}
```

## Swarm Limits

Swarm phases support limits at multiple levels:

```yaml
swarm:
  max_iterations: 100
  max_tokens: 500000
  max_cost: 10.0
  coordinator:
    max_iterations: 50
    max_cost: 2.0
  agents:
    - name: coder
      max_iterations: 80
      max_cost: 3.0
```

Precedence for swarm agents: agent-level > swarm-level > defaults.
