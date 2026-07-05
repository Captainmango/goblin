# Cost Control & Circuit Breaker

## Overview

Goblin currently passes `max_rounds` and `max_iterations` into the swarm phase, but there is no visibility into actual spend, no token or cost limits, and no equivalent guards for `PromptPhase`. This PRD introduces a unified **Circuit Breaker** that tracks cumulative iterations, tokens, and cost across every LLM call within a phase, halting execution gracefully when any limit is breached.

## Current State

### What Already Exists

| Limit | Location | Hooked Up? | Visibility |
|---|---|---|---|
| `max_rounds` | `swarm.max_rounds` → `SwarmPhase.max_rounds` | **Yes** — checked in `EVALUATING` state (line 327) | None; hard stop only |
| `max_iterations` | `swarm.max_iterations` → `LocalConversation(max_iteration_per_run=...)` | **Yes** — passed to SDK per-agent (line 242) | None; SDK enforces silently |
| `timeout` | `swarm.timeout` | **Yes** — deadline checked in `run()` loop | Logs only |

### What Is Missing

1. **No spend tracking** — Goblin never inspects `LocalConversation.conversation_stats` or `accumulated_token_usage` / `accumulated_cost`.
2. **No token limits** — There is no `max_tokens` knob anywhere.
3. **No cost limits** — OpenHands SDK supports `max_budget_per_run` on `LocalConversation`, but Goblin never sets it.
4. **No coverage for `PromptPhase`** — Prompt phases run without any iteration, token, or cost guard.
5. **No aggregate limits across a swarm** — `max_iterations` is per-agent-per-run. A swarm with 5 agents × 100 iterations × 5 rounds could consume 2,500 agent turns with no single chokepoint.
6. **No spend reporting in results** — Phase results do not include tokens used, cost incurred, or iterations consumed.

## Decision: Rounds vs Iterations

**Keep them separate, track them together.**

- `max_rounds` controls **orchestration depth** (how many times the coordinator re-evaluates).
- `max_iterations` controls **turn budget** (how many LLM completions a single conversation may perform).

Rolling them into one number would conflate two very different costs: a coordinator step is typically 1–2 iterations, whereas a worker agent may need 20–50. Authors should be able to cap each dimension independently. The Circuit Breaker aggregates both (plus tokens and cost) into a single stopping mechanism, so the code path is unified even if the knobs remain distinct.

## Blueprint Schema

### New/Changed Fields

#### `PromptPhase`

```yaml
steps:
  - id: analyse_code
    prompt: "Review this diff and list bugs."
    allowed_tools: [Read, Bash]
    max_iterations: 20      # max LLM turns for this prompt phase
    max_tokens: 50000       # prompt + completion tokens (optional)
    max_cost: 0.50          # USD (optional)
```

#### `SwarmPhase`

```yaml
steps:
  - id: build_feature
    swarm:
      goal: "Implement auth"
      coordinator:
        prompt: "..."
        max_iterations: 10      # per-coordinator-run limit
        max_tokens: 20000       # per-coordinator-run limit
        max_cost: 0.25          # per-coordinator-run limit
      agents:
        - name: backend
          prompt: "..."
          max_iterations: 50    # per-agent-run limit
          max_tokens: 100000    # per-agent-run limit
          max_cost: 1.00        # per-agent-run limit
      max_rounds: 5
      max_iterations: 100       # swarm-wide default for agents (backward compat)
      max_tokens: 500000        # swarm-wide total token budget
      max_cost: 5.00            # swarm-wide total cost budget
```

### Schema Table

| Field | Type | Applies To | Default | Description |
|---|---|---|---|---|
| `max_iterations` | integer | PromptPhase, SwarmPhase (agents), SwarmPhase (coordinator) | `100` (swarm agents), `500` (prompt) | Maximum LLM turns per conversation run. |
| `max_tokens` | integer | PromptPhase, SwarmPhase | `null` (unlimited) | Maximum cumulative prompt + completion tokens per phase. |
| `max_cost` | float | PromptPhase, SwarmPhase | `null` (unlimited) | Maximum cumulative cost in USD per phase. |
| `swarm.max_rounds` | integer | SwarmPhase only | `5` | Maximum coordinator evaluation rounds. |

**Backward compatibility:** Existing blueprints that specify `swarm.max_iterations` continue to work. It becomes the per-agent default when an agent does not declare its own `max_iterations`.

## Architecture

### New Components

| File | Purpose |
|---|---|
| `goblin/cost_control.py` | `CircuitBreaker`, `SpendTracker`, `LimitExceeded` exception |
| `goblin/phase.py` | `PromptPhase` updated to accept limits and wire in `CircuitBreaker` |
| `goblin/swarm.py` | `SwarmPhase` updated to aggregate spend across coordinator + all agents + all rounds |
| `goblin/blueprint.py` | Pass blueprint-level defaults (optional) into phases |
| `spec/v1.json` | JSON Schema additions for `max_iterations`, `max_tokens`, `max_cost` on prompt and swarm steps |

### `SpendTracker`

A thin dataclass that accumulates metrics across one or more `LocalConversation` runs.

```python
from dataclasses import dataclass, field

@dataclass
class SpendTracker:
    iterations: int = 0
    tokens: int = 0
    cost: float = 0.0

    def absorb(self, conversation) -> None:
        """Read stats from a finished LocalConversation."""
        metrics = conversation.conversation_stats.get_combined_metrics()
        self.iterations += conversation.state.iteration  # or derive from events
        if metrics.accumulated_token_usage:
            self.tokens += metrics.accumulated_token_usage.prompt_tokens + metrics.accumulated_token_usage.completion_tokens
        self.cost += metrics.accumulated_cost
```

> **Note:** OpenHands does not expose a single "total iterations" counter on `ConversationState` that matches `max_iteration_per_run`. The circuit breaker should instead increment an internal counter each time it observes a completed `LocalConversation.run()`, or we can count `ActionEvent` / `MessageEvent` entries added during the run. The simplest accurate approach is to snapshot `len(conversation.state.events)` before and after `await conversation.arun()` and treat the delta as an iteration proxy, but that over-counts. A better proxy is to query `conversation.state.max_iterations` versus remaining, or to rely on the SDK's own enforcement and simply treat each `arun()` call as an opaque unit that either succeeds or hits `max_iteration_per_run`. For Goblin's purposes, **the Circuit Breaker should increment by `1` for each `arun()` call that was allowed to run to its natural conclusion or SDK-enforced limit**, because the SDK already hard-caps per-run iterations.
>
> If finer-grained iteration counting is needed later, we can patch `LocalConversation` callbacks.

### `CircuitBreaker`

```python
class LimitExceeded(Exception):
    def __init__(self, reason: str, tracker: SpendTracker):
        self.reason = reason
        self.tracker = tracker


class CircuitBreaker:
    def __init__(
        self,
        max_iterations: int | None = None,
        max_tokens: int | None = None,
        max_cost: float | None = None,
    ):
        self.tracker = SpendTracker()
        self.max_iterations = max_iterations
        self.max_tokens = max_tokens
        self.max_cost = max_cost

    def check(self) -> None:
        """Raise LimitExceeded if any limit is breached."""
        t = self.tracker
        if self.max_iterations is not None and t.iterations >= self.max_iterations:
            raise LimitExceeded("max_iterations", t)
        if self.max_tokens is not None and t.tokens >= self.max_tokens:
            raise LimitExceeded("max_tokens", t)
        if self.max_cost is not None and t.cost >= self.max_cost:
            raise LimitExceeded("max_cost", t)

    def absorb(self, conversation) -> None:
        self.tracker.absorb(conversation)
        self.check()
```

### PromptPhase Integration

```python
class PromptPhase(PhaseProtocol):
    def __init__(self, raw: dict, blueprint: V1Blueprint):
        ...
        self.max_iterations: int | None = raw.get("max_iterations")
        self.max_tokens: int | None = raw.get("max_tokens")
        self.max_cost: float | None = raw.get("max_cost")

    async def run(self, context: dict) -> Dict:
        breaker = CircuitBreaker(
            max_iterations=self.max_iterations,
            max_tokens=self.max_tokens,
            max_cost=self.max_cost,
        )

        conversation = LocalConversation(
            agent=agent,
            workspace=LocalWorkspace(...),
            hook_config=...,
            max_iteration_per_run=self.max_iterations or 500,
            max_budget_per_run=self.max_cost,
        )
        conversation.send_message(prompt)
        await conversation.arun()

        breaker.absorb(conversation)

        text = get_agent_final_response(conversation.state.events)
        ...
        return {
            **parsed,
            "prompt": prompt,
            "output": text,
            "spend": breaker.tracker,  # or model_dump()
        }
```

### SwarmPhase Integration

The swarm runs multiple conversations (coordinator + N agents) across multiple rounds. A single `CircuitBreaker` must span all of them.

```python
class SwarmPhase(PhaseProtocol):
    def __init__(self, raw: dict, blueprint: V1Blueprint):
        ...
        swarm = raw["swarm"]
        self.max_rounds: int = swarm.get("max_rounds", 5)
        self.max_iterations: int | None = swarm.get("max_iterations")
        self.max_tokens: int | None = swarm.get("max_tokens")
        self.max_cost: float | None = swarm.get("max_cost")
        ...

    async def run(self, context: dict) -> Dict:
        self._setup(context)

        breaker = CircuitBreaker(
            max_iterations=self.max_iterations,
            max_tokens=self.max_tokens,
            max_cost=self.max_cost,
        )

        deadline = None
        if self.timeout:
            deadline = asyncio.get_running_loop().time() + self.timeout

        try:
            while True:
                state = self._db.get_state()

                if deadline is not None and asyncio.get_running_loop().time() > deadline:
                    self._db.transition(SwarmState.TEARDOWN)
                    continue

                match state:
                    case SwarmState.SETUP:
                        self._db.transition(SwarmState.COORDINATOR)

                    case SwarmState.COORDINATOR:
                        result = await self._run_coordinator(context, breaker)
                        if result.get("done"):
                            self._db.transition(SwarmState.TEARDOWN)
                        else:
                            self._db.transition(SwarmState.AGENTS_RUNNING)

                    case SwarmState.AGENTS_RUNNING:
                        await self._run_agents(context, breaker)
                        self._db.transition(SwarmState.EVALUATING)

                    case SwarmState.EVALUATING:
                        round_num = self._db.increment_round()
                        if round_num >= self.max_rounds:
                            self._db.transition(SwarmState.TEARDOWN)
                        else:
                            self._db.transition(SwarmState.COORDINATOR)

                    case SwarmState.PAUSED:
                        return {"success": False, "message": ["Swarm paused"], "paused": True, "spend": breaker.tracker}

                    case SwarmState.TEARDOWN:
                        teardown_result = self._teardown()
                        teardown_result["spend"] = breaker.tracker
                        return teardown_result
        except LimitExceeded as exc:
            self._db.transition(SwarmState.TEARDOWN)
            result = self._teardown()
            result["success"] = False
            result["message"].append(f"Circuit breaker: {exc.reason} limit exceeded")
            result["spend"] = exc.tracker
            return result
```

#### `_run_coordinator` with Circuit Breaker

```python
async def _run_coordinator(self, context: dict, breaker: CircuitBreaker) -> dict:
    ...
    conversation = LocalConversation(
        ...,
        max_iteration_per_run=self.coordinator_config.get("max_iterations", 50),
        max_budget_per_run=self.coordinator_config.get("max_cost"),
    )
    conversation.send_message(prompt)
    await conversation.arun()
    breaker.absorb(conversation)
    ...
```

#### `_run_single_agent` with Circuit Breaker

```python
async def _run_single_agent(self, agent_cfg: dict, context: dict, breaker: CircuitBreaker):
    ...
    conversation = LocalConversation(
        ...,
        max_iteration_per_run=agent_cfg.get("max_iterations", self.max_iterations or 100),
        max_budget_per_run=agent_cfg.get("max_cost"),
    )
    conversation.send_message(prompt)
    await conversation.arun()
    breaker.absorb(conversation)
    ...
```

### Result Shape

All phases now include a `spend` key:

```python
{
    "success": bool,
    "message": [str],
    "spend": {
        "iterations": int,
        "tokens": int,
        "cost": float,
    }
}
```

If the circuit breaker fires, `success` is `False` and the final message includes the reason.

## Open Questions / Trade-offs

### 1. Token Counting Granularity

OpenHands' `accumulated_token_usage` counts prompt, completion, cache, and reasoning tokens separately. The Circuit Breaker should probably sum **prompt_tokens + completion_tokens** for the `max_tokens` check, because those are the billable tokens. Cache and reasoning tokens are provider-specific and may not always count against the author's mental budget.

**Recommendation:** `max_tokens` compares against `prompt_tokens + completion_tokens`. Future PRDs can add `max_prompt_tokens` and `max_completion_tokens` if needed.

### 2. Iteration Counting Granularity

As noted above, there is no perfect "iteration" counter exposed by the SDK that aligns with `max_iteration_per_run`. The SDK already aborts the run when its internal iteration count hits `max_iteration_per_run`. Goblin's `CircuitBreaker` should therefore treat each completed `arun()` as **1 unit** against `max_iterations`, unless we instrument the conversation with an event callback.

**Alternative:** Add an `on_event` callback that increments the breaker on every `ActionEvent` (each action represents one agent step / iteration). This is more accurate but adds overhead.

**Recommendation:** Start with the coarse model (each `arun()` = 1 unit). It is simple, zero-overhead, and still protects against runaway blueprints. Document the limitation and leave a TODO for fine-grained counting.

### 3. Blueprint-Level Global Limits

Authors may want a single budget for the entire blueprint (e.g., "this whole workflow must cost less than $10"). This PRD focuses on **per-phase** limits because they are easier to reason about and implement. A future enhancement could add:

```yaml
blueprint:
  defaults:
    spend:
      max_tokens: 1000000
      max_cost: 10.00
```

These defaults would be inherited by every phase unless overridden.

### 4. Coordinator and Agent Limits vs Swarm-Wide Limits

The schema allows three levels:

1. **Swarm-wide** (`swarm.max_iterations`, `swarm.max_tokens`, `swarm.max_cost`) — enforced by the shared `CircuitBreaker`.
2. **Coordinator per-run** (`swarm.coordinator.max_iterations`, etc.) — passed to `LocalConversation` as `max_iteration_per_run` / `max_budget_per_run`.
3. **Agent per-run** (`swarm.agents[].max_iterations`, etc.) — same, per-agent.

This means an author can say: "Each agent may use up to 50 iterations, but the whole swarm must stop after 200 total iterations." The SDK enforces the per-run limit; the `CircuitBreaker` enforces the aggregate limit.

## Testing Plan

1. **Unit tests for `CircuitBreaker`**
   - `check()` raises when each limit is hit exactly.
   - `absorb()` adds token/cost values correctly.
   - Multiple absorbs accumulate.

2. **PromptPhase tests**
   - A prompt phase with `max_iterations=1` stops after one `arun()` call.
   - A prompt phase with `max_tokens=1` stops when tokens exceed the limit.
   - Result dict contains `spend`.

3. **SwarmPhase tests**
   - Swarm with `max_iterations=2` stops after two total agent runs, even if `max_rounds` would allow more.
   - Swarm with `max_cost=0.01` stops when accumulated cost exceeds the limit.
   - Per-agent `max_iterations` still respected by SDK (mock `LocalConversation` to verify params).

4. **Schema tests**
   - `spec/v1.json` validates blueprints with new fields.
   - Missing fields default to `null` / backward-compatible values.

## Rollout

1. Implement `goblin/cost_control.py` and unit tests.
2. Wire `CircuitBreaker` into `PromptPhase`.
3. Wire `CircuitBreaker` into `SwarmPhase` (coordinator + agents).
4. Update `spec/v1.json`.
5. Update `complete_prds/prd-cost-control.md` with any deviations discovered during implementation.

## Implementation Deviations

- **`SwarmPhase.max_iterations` default preserved at `100`** — The PRD draft showed `swarm.get("max_iterations")` without a default. To maintain backward compatibility with existing tests and blueprints that rely on the per-agent default of `100`, the constructor retains `swarm.get("max_iterations", 100)`.
- **Per-run `max_tokens` not passed to `LocalConversation`** — The OpenHands SDK's `LocalConversation` accepts `max_iteration_per_run` and `max_budget_per_run`, but does not expose a `max_tokens_per_run` parameter. Consequently, per-agent and per-coordinator `max_tokens` values are tracked only by the aggregate `CircuitBreaker`; the SDK cannot enforce them at the individual conversation level.
- **Coarse iteration counting used throughout** — Following the open-questions recommendation, each `await conversation.arun()` counts as exactly `1` iteration against the breaker. No per-event callback instrumentation was added.
