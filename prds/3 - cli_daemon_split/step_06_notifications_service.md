# Step 6 — Notification service: the daemon earns the "N" in GOBLIN

**Goal:** Give notifications the long-running home the relay used to provide — but
daemon-internal this time, not a separate service. A pluggable **dispatcher** consumes the
step-5 event bus and delivers run/phase lifecycle notifications to configured providers
(log, generic webhook, ntfy). Also fix `on_failure.notify` so daemon-registered hooks
actually fire. Human-in-the-loop (the removed relay's ask-user flow [L13]) is designed
but **not built** — it needs the reply channel this step deliberately defers.

**Depends on:** step 5 (event bus). **Blocks:** nothing downstream; parallel to step 7.

---

## 6.1 Scope discipline

- **In:** one-way notifications on run/phase lifecycle; provider config via daemon env;
  `on_failure.notify` bridge; docs.
- **Out (V2 design note in §6.6):** bidirectional/HITL (ask-and-block), inbound channels
  (Telegram bot, Slack interactivity), per-blueprint notification routing rules beyond a
  single optional flag, scheduling.

## 6.2 Dispatcher — `goblin_daemon/core/notify/`

```
core/notify/
├── __init__.py
├── dispatcher.py      # subscribes to EventBus; filters; fans out to providers; retries
├── models.py          # Notification(kind, title, body, run_id, phase_id?, metadata)
└── providers/
    ├── __init__.py    # provider registry: name -> factory
    ├── log.py         # structured log lines (always available; the default)
    ├── webhook.py     # POST JSON to a configured URL (Slack/Discord/ntfy-compatible shapes)
    └── ntfy.py        # ntfy.sh / self-hosted ntfy (title/priority/tags headers)
```

- The dispatcher subscribes to the **run-level** event stream (all runs, not per-run
  queues): maps `run.completed | run.failed | run.interrupted` (always notifiable) and
  `run.phase_failed` (notifiable when the phase's `on_failure.notify` names a daemon
  hook — §6.4) to `Notification`s.
- Delivery: fire-and-forget tasks per provider, **3 attempts with backoff** (1 s, 5 s,
  30 s) — a long-running process can retry, which is the entire point of doing this
  daemon-side; permanent failure → `notify.failed` structured log, never a run failure
  [L16]. Notifications are best-effort side effects; runs must never block on them.
- Redaction: notification bodies contain run ids, blueprint names, phase ids, status, and
  a short error summary — never prompts, outputs, env values, or keys.

## 6.3 Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GOBLIN_NOTIFY_PROVIDERS` | `log` | Comma list: `log`, `webhook`, `ntfy` |
| `GOBLIN_NOTIFY_WEBHOOK_URL` | unset | Generic webhook target (secret_env) |
| `GOBLIN_NOTIFY_NTFY_URL` | unset | ntfy base URL incl. topic, e.g. `https://ntfy.sh/my-topic` |
| `GOBLIN_NOTIFY_NTFY_TOKEN` | unset | ntfy access token (secret_env) |
| `GOBLIN_NOTIFY_EVENTS` | `completed,failed,interrupted` | Which run lifecycle events notify |

All daemon env (`goblin_daemon/config.py` singleton, `secret_env` for tokens [L9, L16]).
Per-run opt-out: `SubmitRunRequest.notify: bool = True` additive wire field (CLI flag
`goblin run --no-notify`) — additive protocol change per step-2 evolution rules.

## 6.4 The `on_failure.notify` bridge

Today `on_failure.notify` resolves a callable in the hook registry and fires it
in-process mid-run [L5]; for `HookDefinition` entries it silently does nothing [L19].
With the daemon:

- The daemon registers **real callables** into the process-global hook registry at
  lifespan (e.g. `notify_daemon`), which publish a phase-failure `Notification` through
  the dispatcher. Step-1 scoped registries read-through to the global, so blueprints can
  name these hooks from `on_failure.notify` and validation resolves them [L2, L5].
- Keep semantics honest: the hook fires from the run process as today (orchestrator is
  in-process in the daemon), but delivery/retry is the dispatcher's — the best of both.
- Update `docs/advanced/custom-hooks.md` to document the daemon-provided notify hooks and
  remove the broken example (completes the step-1.3.6 hygiene).

## 6.5 Blueprint surface (optional, additive)

Add an **optional** `notifications:` block to `spec/v1.json` (both copies — repo `spec/`
and packaged `goblin/goblin/spec/` stay byte-identical [L17]):

```yaml
notifications:
  on: [completed, failed]       # subset of run lifecycle events
  providers: [webhook]          # subset of daemon-configured providers
```

- Purely additive schema change; validation warns (not errors) when a named provider
  isn't daemon-configured (can't know at validate time — the warning is daemon-side at
  submit).
- If scoping pressure appears, defer this block to V2 — the env-only configuration in
  §6.3 already delivers the feature. Decide at implementation time; the schema change is
  the only part of this step that touches blueprint spec.

## 6.6 V2 design note (write as `step_06_hitl_design_note.md` appendix in this file)

One page, captured for the future, **not implemented**:

- The removed relay's ask-user flow [L13] maps onto daemon primitives as:
  `run.question` bus event (new SDK tool or hook emits it) → dispatcher routes to a
  **bidirectional provider** (Telegram bot long-poll in the daemon, Slack modal webhook)
  → reply correlates via a `question_id` → daemon answers the waiting tool call.
- Blocking primitive: the phase's tool call awaits an `asyncio.Future` keyed by
  `question_id` in the daemon (the run is an asyncio task in the daemon, so this is
  native — no polling process needed).
- Note the security questions the relay had (pairing tokens, question-mapping TTLs) and
  that a V2 PRD must re-derive them; do not resurrect `RELAY_*` env names.

## 6.7 Tests

- Dispatcher: event→notification mapping per event type; provider fan-out; retry/backoff
  schedule (fake clock); failure isolation (a hanging provider doesn't delay others or
  the run); redaction (no prompt/output/secret strings in bodies).
- Providers: webhook payload shape + headers against an `httpx.MockTransport`; ntfy
  header construction; log provider emits `notify.dispatched` [L16].
- Bridge: blueprint with `on_failure: {action: skip, notify: notify_daemon}` → phase
  failure produces exactly one notification; validation of that blueprint passes
  daemon-side.
- Wire: `notify=false` submission → dispatcher skips; schema round-trip if §6.5 lands.
- `task goblin-daemon:test` + root tasks green; zero changes to existing tests.

## Acceptance criteria

- [ ] A run failing against a daemon configured with the webhook provider delivers a
      redacted failure notification (recorded by a test double), with retries on 5xx.
- [ ] `on_failure.notify: notify_daemon` works end-to-end and is documented.
- [ ] Env-only configuration works with zero blueprint changes; §6.5 either landed
      additively (both schema copies byte-identical, `task validate` green) or explicitly
      deferred with the reason recorded here.
- [ ] HITL design note (§6.6) written; no HITL code.
