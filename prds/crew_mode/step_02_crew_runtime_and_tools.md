# Step 2 — `CrewRuntime` supervisor + typed `Crew` tool

**Goal:** The machinery that turns lead tool calls into live crewmate sessions: spawn,
lifecycle, follow-up messages, stop, and startup reconciliation.

**Depends on:** step 1. **Blocks:** steps 3–4.

---

## 2.1 `CrewRuntime`

New `goblin/goblin/crew/runtime.py`. Constructed with the registry, crew home, resolved LLM
defaults, a `max_crewmates` semaphore count, and a crew-level `CircuitBreaker` [L7]. Owns:

- **Spawn intake.** `request_spawn(SpawnRequest)` is callable from any thread: it writes the
  `pending` session row *synchronously* (durable first — README §7 race mitigation) and then
  hands off to the loop with `loop.call_soon_threadsafe`. A supervisor coroutine consumes
  requests and also drains any leftover `pending` rows on startup.
- **Crewmate construction.** Per spawn: workspace (`LocalWorkspace` on
  `<crew_home>/sessions/<name>/ws` in V1; worktree support arrives in step 6),
  `persistence_dir=<crew_home>/sessions/<name>`, agent built with `build_llm` (spawn model
  override → `crew_model` → `default_model` [L8]), `resolve_tools` for the spawn's
  `allowed_tools` (default: the standard goblin tool set **without** the Crew tool), and the
  `CREW_MATE_PROMPT` (added to `prompts.py` [L11]: identity, "your brief is all the context
  you have", "report a concise outcome as your final message").
- **Turn loop per crewmate** (one asyncio task, acquired under the semaphore):
  `pending → running`; `send_message(brief)`; `await arun()`; then loop over
  `undelivered_messages(name)` — each becomes the next turn and is `mark_delivered`'d.
  When no messages remain: absorb spend into the crew breaker, persist `result` (via
  `get_agent_final_response` [L4]) and `cost`, set `done`, `emit_event("completed", …)`.
- **Failure paths.** `LimitExceeded` → status `failed`, event `failed` with the reason (the
  swarm's circuit-breaker message pattern [L1]). Any other exception → log
  `crew.crewmate_failed` with `extra={"event": ...}`, status `failed`, event — never crash
  the supervisor [L1, L12].
- **Stop.** `request_stop(name)`: registry-flag + cancel the asyncio task; status `stopped`,
  event `stopped`. Stopping an unknown or terminal session returns a typed error the tool
  surfaces to the lead.
- **Wake-up.** `message` to a `done`/`stopped` session re-queues it (`pending`) so the
  supervisor starts a fresh turn on the persisted conversation (step-1 spike item 2 proves
  cold resume) [L5].
- **Reconciliation.** `start()` runs `reconcile_orphans()` (step 1) and drains `pending`
  rows; `shutdown(grace_period)` lets running turns finish or cancels on a second signal.

The runtime must never call blocking driver code on the event loop: registry calls are
wrapped in `asyncio.to_thread`, matching `SwarmPhase._db_call` [L1].

## 2.2 The `Crew` tool

New `goblin/goblin/crew/tools.py`, one typed tool following `swarmdb_tool.py` [L3]:

- `CrewAction.command`: `Literal["spawn", "list", "read", "message", "stop"]` with fields
  per README §5.5 (`name`, `task`, `model`, `allowed_tools`, `max_cost`, `max_iterations`,
  `message`, `tail`).
- Executor holds the registry config (plain dict/path — serializable, same constraint as
  `db_config` [L3]) plus a runtime handle for `spawn`/`stop` handoff. `list`/`read`/`message`
  go straight to the registry (sync driver is thread-safe per [L2]).
- `read` renders the registry row + last N events from `sessions/<name>/events/*.json`,
  skipping an unparseable newest file (§1.1.3).
- Tool description states the guardrails: the lead cannot do project work itself; one
  crewmate per goal; briefs must be self-contained; duplicates rejected [L3, L11].
- Register with `register_tool` and map the name in `goblin/tools/tool_map.py` if that is the
  established resolution path for typed tools (verify against `resolve_tools` while
  implementing; keep the Crew tool off the default phase tool list — it is lead-only).

## 2.3 Tests

Unit tests (`goblin/tests/unit/crew/`), mocking at the project boundary [L12]:

- Runtime with **mocked conversation construction** (fake `arun` that records prompts and
  returns canned events): spawn → pending → running → done with result/cost persisted and a
  `completed` event.
- Semaphore: with `max_crewmates=1`, a second spawn stays `pending` until the first finishes.
- `message` during a run is delivered as the next turn (assert prompt order); `message` to a
  `done` session wakes it (status back to `running`, then `done`).
- `stop` cancels the task → `stopped` + event; stopping a terminal session errors cleanly.
- Exception in a crewmate turn → `failed` + event; supervisor keeps processing other spawns.
- `LimitExceeded` from the crew breaker → `failed` with the breaker reason.
- Reconciliation on `start()`: pre-seeded `running` row → `interrupted` + event; pre-seeded
  `pending` row gets run.
- Tool executor: each command against a real registry on `tmp_path` (spawn asserts the
  pending row + handoff; `read` renders persisted event files written by hand).

Integration coverage is deferred to step 3's end-to-end chat test; no MCP servers [L12].

## Acceptance criteria

- [ ] All §2.3 unit tests pass under `task goblin:test`; lint/typecheck green.
- [ ] A scripted demo (test-only) drives two concurrent crewmates + a follow-up message with
      zero registry inconsistencies.
- [ ] No blocking registry calls on the event loop (code review + `asyncio` slow-callback
      debug run in tests).
- [ ] Structured events logged: `crew.spawned`, `crew.crewmate_done`, `crew.crewmate_failed`,
      `crew.stopped`, `crew.reconciled`.
