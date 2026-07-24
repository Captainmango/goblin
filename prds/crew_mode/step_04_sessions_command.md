# Step 4 — `goblin crew sessions`: viewing the managed fleet

**Goal:** The "view all sessions the top-level agent is managing" surface: a read-only,
cross-process fleet list and per-session transcript view.

**Depends on:** step 2 (registry + persisted sessions); can be built in parallel with step 3
against a hand-seeded crew home. **Blocks:** nothing (step 5 is independent).

---

## 4.1 `sessions_view.py`

New `goblin/goblin/crew/sessions_view.py` — pure read-only rendering helpers:

- `load_fleet(crew_home, name) -> list[SessionRow]`: open the registry read-only
  (`sqlite3.connect(f"file:{path}?mode=ro", uri=True)` on top of the step-1 registry — WAL
  readers never block the live writer [L2]) and return sessions ordered by `updated_at`
  descending.
- `render_fleet(rows) -> str`: aligned table — `NAME STATUS TASK COST UPDATED` with the task
  truncated to fit; empty crew renders a friendly "no sessions yet" line.
- `load_transcript_tail(crew_home, session, tail) -> list[EventPreview]`: read
  `sessions/<name>/events/event-*.json` in lexical order [L5], parse each, and project to
  `(kind, preview)` — role/type plus a one-line text preview (first 160 chars). Skip the
  newest file if it fails to parse (in-flight write, §1.1.3); never raise on a live session.
- `render_session(row, events) -> str`: full detail block — task, status, workspace,
  persistence dir, model, cost, result — followed by the transcript tail.

## 4.2 CLI

In the step-3 `crew` Click group:

```
goblin crew sessions [--name default]              # fleet table
goblin crew sessions <session> [--tail 20]         # detail + transcript tail
```

- `--name` selects the crew (same default as the chat command).
- Unknown crew home → friendly error (`no crew '<name>' at <path>`), exit 2 usage-style.
- Unknown session → list valid names in the error.
- Honor the global logging flags for consistency, but these commands should be near-silent:
  output goes to stdout, nothing mutates [L6].

## 4.3 Concurrency contract

Documented and tested behavior while `goblin crew` runs in another terminal:

- The fleet table always succeeds (read-only sqlite connection) [L2].
- A session mid-turn shows its last fully-persisted event; the view never blocks, locks, or
  corrupts the writer (per-event files [L5]).
- A session that finished between `list` and `read` simply shows newer data — no caching.

## 4.4 Tests

- Seed a registry + hand-written `events/event-00000-….json` files on `tmp_path`; assert
  fleet rendering (order, truncation) and detail rendering (tail length, previews).
- Live-safety: hold an open writer connection (WAL) while `load_fleet` runs — no error.
- Partial tail: append a truncated JSON file as the newest event; `load_transcript_tail`
  returns all prior events and skips it without raising.
- CLI: both command shapes against a seeded crew home (`CliRunner`), including the
  unknown-crew and unknown-session error paths.
- Cross-process smoke: a subprocess running `crew sessions` while the test process mutates
  the registry — output reflects committed state, exit 0.

## Acceptance criteria

- [ ] `goblin crew sessions` against a live crew prints the fleet table from a second
      terminal with the chat running (manually verified + subprocess test).
- [ ] `goblin crew sessions <name> --tail 5` shows status/cost/result plus the last 5 events
      of a real session produced by the step-3 golden-path test.
- [ ] All §4.4 tests green; lint/typecheck green.
- [ ] `docs/cli/goblin.md` gains the `crew sessions` reference (full guide lands in step 5).
