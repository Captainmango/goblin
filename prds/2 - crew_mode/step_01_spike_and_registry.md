# Step 1 — Spike: SDK conversation assumptions + land `CrewRegistry`

**Goal:** Prove the three SDK assumptions the whole design rests on, and land the crew's
storage layer (registry, enums, config) with unit tests.

**Depends on:** nothing. **Blocks:** steps 2–5.

---

## 1.1 Spike: verify SDK behavior on the pinned `openhands-sdk` 1.29.3

Write a throwaway script under `/tmp` (not committed) that asserts, from the repo venv:

1. **Multi-turn local conversation.** Build a minimal `Agent` (`build_llm` with a cheap
   model [L8]), a `LocalWorkspace` on a `tmp_path`, and a
   `LocalConversation(agent, workspace, persistence_dir=...)` [L5]. Run
   `send_message("...")` → `await arun()` → then a **second** `send_message` → `arun()`.
   Assert: both turns complete, events accumulate across turns, and
   `get_agent_final_response(state.events)` reflects the latest turn.
2. **Cold resume.** In a *fresh* `LocalConversation` pointed at the same `persistence_dir`,
   assert prior events are loaded (cold-load path in `ConversationState` [L5]) and a third
   turn can run. This is what makes lead chat and crewmate wake-up restart-proof.
3. **Offline event read.** While a conversation is *paused between turns* (not mid-`arun`),
   read `<persistence_dir>/events/event-*.json` from a *separate* plain file read, parse each
   as JSON, and confirm event payloads contain enough to render a transcript tail
   (role/type/text). Confirm the failure mode of a truncated newest file is a catchable
   `json.JSONDecodeError` (the §5.8 skip rule) [L5].
4. **Thread-safe spawn handoff.** From a plain worker thread (simulating the SDK tool
   executor pool [L2, L3]), call `loop.call_soon_threadsafe(queue.put_nowait, req)` into a
   running loop and assert the consumer coroutine receives it in order.

**Spike exit criteria:** items 1–4 pass unmodified. Record findings (including exact
`persistence_dir` constructor signature and any compaction flags discovered) in this file.
If item 1 or 2 fails, re-evaluate Option B in `README.md` §4 before proceeding.

## 1.2 `CrewRegistry` (storage layer)

New `goblin/goblin/crew/db.py`, modeled on `swarm_db.py` [L2]:

- Sync `sqlite3`, `check_same_thread=False` (tool-executor thread pool), WAL pragma,
  `init_schema()` creating the three tables from `README.md` §5.4 (`sessions`, `messages`,
  `crew_events`).
- Typed methods only: `create_session(...)`, `get_session(name)`, `list_sessions()`,
  `set_status(name, status, *, result=None, cost=None)`, `post_message(name, body)`,
  `undelivered_messages(name)`, `mark_delivered(ids)`, `emit_event(name, type, summary)`,
  `consume_events() -> list[dict]` (returns and marks consumed), `reconcile_orphans() ->
  list[str]` (running/pending → interrupted, emits events, returns affected names).
- `CrewSessionStatus` enum in `goblin/enums.py` (`PENDING`, `RUNNING`, `DONE`, `FAILED`,
  `STOPPED`, `INTERRUPTED`) mirroring `TaskStatus` style.
- `CrewRegistry.open(path)`: create parent dirs (`exist_ok=True`), connect, return instance;
  `close()`.

## 1.3 Config additions

In `goblin/goblin/utils/config.py` [L8], add to the existing frozen config dataclass:

| Field | Env var | Default |
|---|---|---|
| `crew_home` | `GOBLIN_CREW_HOME` | `.goblin/crew` |
| `crew_max_crewmates` | `GOBLIN_CREW_MAX_CREWMATES` | `4` |
| `crew_model` | `GOBLIN_CREW_MODEL` | `None` (falls back to `default_model`) |

Use `goblin_common.config.env()` coercion per conventions; `reset_config()` keeps working
(the autouse test fixture depends on it [L12]).

## 1.4 Tests

New `goblin/tests/unit/crew/` (with `__init__.py`, `conftest.py` providing a `tmp_path`
registry):

- Schema creation is idempotent (`init_schema()` twice).
- Session CRUD + status transitions persist across `close()`/`open()`.
- Duplicate `create_session(name)` raises a domain error.
- Message post/undelivered/mark-delivered round-trip.
- `emit_event`/`consume_events` returns each event exactly once, in order.
- `reconcile_orphans` flips `running`/`pending` → `interrupted` and emits one event each.
- Config: env vars coerce (`int` for max crewmates), defaults hold, `reset_config` re-reads.

## Acceptance criteria

- [ ] Spike §1.1 items 1–4 pass; findings (signatures, compaction notes) recorded here.
- [ ] `task goblin:test` green including the new `tests/unit/crew/` suite.
- [ ] `task goblin:lint` and repo typecheck green; files start with
      `from __future__ import annotations`; structured logging via `getLogger(__name__)`.
- [ ] No changes to `spec/v1.json`, `swarm.py`, `swarm_db.py`, or any blueprint behavior.
