# Step 5 — Run events: event bus, SSE streaming, and live `--follow`

**Goal:** Turn the daemon from a submit-and-poll service into a streaming one. Two tiers,
in order: (a) **lifecycle events** from registry transitions + checkpoint diffs — zero
runtime changes, works for every run; (b) **fine-grained agent events** via the SDK's
conversation `callbacks` seam — one additive passthrough in `goblin/conversation.py`.
Both feed one in-process `EventBus` and one SSE endpoint consumed by `goblin run
--follow`.

**Depends on:** step 4 (step-1 spike item 6 already proved checkpoint diffing). **Blocks:**
step 6 (notifications consume the bus).

---

## 5.1 EventBus — `goblin_daemon/core/events.py`

- In-process pub/sub: `subscribe(run_id) -> asyncio.Queue`, `publish(event: RunEvent)`;
  per-run subscriber sets, bounded queues (drop-oldest for slow consumers — the stream is
  a convenience, never backpressure against a run), `close(run_id)` on terminal status.
- Event envelope = step-2 `RunEvent` (`event`, `run_id`, `phase_id?`, `ts`, `payload?`),
  dot-notation names per repo logging conventions [L16]:
  `run.pending | run.started | run.phase_started | run.phase_completed |
  run.phase_failed | run.phase_skipped | run.completed | run.failed | run.interrupted`.
- Registry transitions publish from `RunService` directly (pending/started/terminal);
  phase transitions come from §5.2.

## 5.2 Tier (a): checkpoint-diff publisher — `core/checkpoint_watcher.py`

- One watcher task **per active run**, polling the checkpoint JSON at 250 ms (spike
  §1.1.6 proved reads are never torn [L4]): diff `phases` against the last snapshot,
  publish the corresponding phase events with checkpoint timestamps.
- Lifecycle: started when a run leaves `pending`, stopped on terminal registry status
  (final diff flushed before close).
- This tier covers **every** phase type — prompt, load_project, swarm — because all of
  them write checkpoints [L4, L5, L11].

## 5.3 Tier (b): conversation callbacks passthrough — `goblin/goblin/conversation.py`

One additive parameter, default-off, zero behavior change when unused [L7]:

```python
async def run_conversation(agent, workspace, prompt, *, hook_config=None,
                           max_iterations, max_cost=None, secrets=None,
                           callbacks: list[ConversationCallbackType] | None = None): ...
```

- Forward to the `LocalConversation`/`RemoteConversation` constructors (the pinned SDK
  accepts `callbacks: list[Callable[[Event], None]]` [L7]).
- Thread through `PromptPhase.run` (both openhands and ACP paths) as an optional phase
  kwarg sourced from a per-run context key the daemon sets — e.g.
  `context["__event_callbacks__"]` is **rejected** (context is checkpoint-persisted JSON);
  instead pass via an optional parameter on `V1Blueprint.run(..., event_sink=None)` →
  orchestrator → phases, all default `None`. Design the exact plumbing in the step;
  constraint: nothing non-serializable ever touches the checkpoint [L4].
- Daemon-side adapter (`core/event_sink.py`): a callback that maps SDK events to
  `RunEvent`s — `agent.action` / `agent.observation` / `agent.message` with a redacted
  payload (text truncated to ~500 chars, **no secrets**, no tool-arg env values) — and
  publishes to the bus.
- Swarm phases: out of scope for tier (b) in this step (their per-agent conversations are
  built deep in `SwarmPhase` [L11]); tier (a) already covers swarm lifecycle. Note swarm
  callbacks as a follow-up.

## 5.4 SSE endpoint — `api/events.py`

`GET /v1/runs/{id}/events`:

- `StreamingResponse`, `text/event-stream`; each `RunEvent` as `data: {json}\n\n` with
  `event:` type field; heartbeat comment every 15 s (keeps idle connections alive through
  proxies — matters in remote mode, step 7).
- **Replay semantics:** on connect, emit the current checkpoint-derived state as synthetic
  `run.phase_*` events for already-finished phases, then live events — a late-attaching
  client reconstructs full run history. Terminal runs return the replay and close (this
  also gives `goblin runs <id> --events` a history view).
- Unknown run → 404. Client disconnects are routine; no run coupling.

## 5.5 CLI: `goblin run --follow` and friends

- `DaemonClient.stream_events` (stubbed in step 2) implemented: SSE parse → `RunEvent`
  iterator; reconnect once on dropped connection (resume is idempotent thanks to replay).
- `goblin run <path> --follow`: submit, stream, render a compact live view (phase
  transitions one-per-line; agent events one-line summaries when tier (b) active), then
  the same final output as non-follow mode. Default (non-follow) run keeps step-4 polling
  + final render — `--follow` is opt-in so scripts see no output change.
- `goblin runs <id> --follow`: attach to an in-flight run (the detach/reattach story from
  step 4 completed).
- `--quiet` suppresses event rendering as today it suppresses progress noise.

## 5.6 Tests

- Bus: subscribe/publish/close, bounded-queue drop policy, multi-subscriber fan-out.
- Watcher: fixture checkpoint file mutated through phase transitions → correct event
  sequence; no events after terminal close; tolerant of a `running`-phase reset on
  `Checkpoint.load()` [L4].
- Callbacks: unit — a stub callback receives SDK events through `run_conversation` (mock
  at the conversation boundary [L16]); plumbing test — `event_sink=None` default keeps
  existing tests untouched; redaction test — no secret values in payloads.
- SSE: ASGI test — replay-then-live ordering, heartbeat framing, 404, terminal replay.
- CLI: `--follow` renders phase lines and exits with the run's code; attach/detach cycle.
- Existing suites green unmodified (`event_sink` default-off is the compat guarantee).

## Acceptance criteria

- [ ] `goblin run --follow` against a daemon shows live phase transitions for prompt,
      load_project, and swarm blueprints (tier a), and agent events for prompt phases
      (tier b).
- [ ] Late attach (`runs <id> --follow`) reconstructs full history via replay.
- [ ] All event names logged/published in dot notation; `task lint typecheck test` green
      with zero changes to existing tests.
- [ ] Latency measurement recorded: phase transition → SSE frame on the client (< 500 ms).
