# PRD: Crew Mode — a Top-Level Agent that Creates and Assigns Work to Other Agents

**Status:** Proposed
**Author:** Investigation + planning (AI-assisted)
**Date:** 2026-07-24
**Inspiration:** [firstmate](https://github.com/kunchenguid/firstmate) — "Talk to one agent. Ship with a crew." [S1, S2]

---

## 1. Summary

Add an interactive **crew mode** to goblin: the user chats with a single top-level agent — the
**crew lead** — and the crew lead creates, assigns, supervises, and reports on **crewmate**
agent sessions that do the actual work. A separate `goblin crew sessions` command shows every
session the crew lead is managing, with status and transcripts, from a second terminal.

Crewmates run on one of two harnesses: goblin's native OpenHands agent loop, or a wrapped
third-party coding harness (Claude Code, Codex, Gemini CLI) over **ACP**, reusing the
integration designed in `prds/acp_for_alt_harnesses` [L10]. Both harness kinds run inside
ordinary persisted `LocalConversation`s, so supervision, session viewing, and cost control
behave identically.

Two hard constraints shape everything below:

- **Crew mode is CLI-only.** The `goblin crew` chat REPL and the `goblin crew sessions`
  views are the entire surface — there is no server, HTTP API, or TUI, and none is planned.
- **notifications-relay development is frozen.** Per `AGENTS.md`, that project must not be
  read or changed. Crew mode has **zero** references to it and **zero** dependencies on it.

The model is lifted from firstmate [S1]:

- **One liaison.** The user talks only to the crew lead. The lead decomposes requests into
  self-contained task briefs, spawns crewmates, and reports plain outcomes. The user never
  babysits N agent sessions — that cognitive load moves to the lead.
- **A visible crew.** Every crewmate runs in its own persisted conversation. A read-only CLI
  view lists all sessions and tails any session's event stream while work is in flight.
- **Restart-proof.** All crew state lives on disk (sqlite registry + SDK-persisted
  conversations). Kill the process anytime; the next `goblin crew` reconciles and carries on.

The key decision of this PRD: **build crew mode as a new interactive CLI surface that reuses
goblin's existing swarm machinery patterns — typed tools, a sqlite coordination DB, persisted
`LocalConversation`s, and the circuit breaker — and not as a blueprint phase, and not with
firstmate's tmux/script process model.** Crewmate sessions are in-process asyncio tasks whose
conversations persist to disk via the SDK's `persistence_dir` — whether the agent inside is a
native SDK `Agent` or an ACP-wrapped harness subprocess [L10] — which is what makes them
viewable from outside the chat process.

---

## 2. Problem statement

Today goblin has two execution shapes, neither of which fits interactive multi-agent work:

1. **Blueprint runs** (`goblin run <blueprint>`) — unattended, declarative pipelines. The user
   must decide the whole decomposition up front, encode it in YAML phases, and wait for the
   run to end. There is no way to converse with the orchestrator or redirect it mid-run [L1].
2. **The swarm phase** — a declarative, batch multi-agent phase. The agent roster is fixed in
   YAML, a stateless coordinator is re-invoked per round to create tasks on a shared board,
   and workers claim them in dependency-free waves. It is powerful but **non-interactive**:
   the user cannot talk to the coordinator, cannot inject a new goal mid-flight, and cannot
   see anything until the phase ends (or pick through logs) [L1, L2].

Meanwhile, running several agents by hand (the firstmate observation [S1]) means tab-juggling
sessions, copy-pasting context, and forgetting which terminal had the failing test. The user
wants:

1. To **talk to one agent** and have it create and assign tasks to other agents dynamically —
   no YAML, no up-front roster.
2. To **remove the cognitive load** of managing multiple agents: the top-level agent tracks
   who is doing what, notices completion/failure, and escalates only real decisions.
3. To **view all sessions** the top-level agent is managing — a live, at-a-glance list plus
   the ability to inspect any session's transcript.

Restated as a gap analysis against what exists:

| Need | Swarm phase | Blueprint run | **Crew mode (this PRD)** |
|---|---|---|---|
| Talk to the orchestrator | ✗ (stateless, per-round) | ✗ | ✓ persistent chat |
| Dynamic task creation at runtime | ✓ (coordinator tool) | ✗ | ✓ (lead's spawn tool) |
| Agent roster defined up front | required | n/a | not required |
| View all managed sessions live | ✗ (log files only) | ✗ | ✓ (`crew sessions`) |
| Survives process kill / resume | ✗ (phase-bound) | ✓ (checkpoints) | ✓ (registry + persisted conversations) |

---

## 3. Investigation

### 3.1 The firstmate model (what we are borrowing, and what we are not)

firstmate is an "agent distro": a directory of instructions, skills, and bash scripts that
turns a general-purpose terminal agent into a crew manager [S1]. Its relevant mechanics [S2]:

- **First mate / crewmates.** One liaison agent routes each request to a crewmate; crewmates
  work autonomously and report outcomes. The first mate is read-only over projects by
  construction — crewmates make every change.
- **Visible sessions.** Each crewmate lives in its own tmux window (or herdr/zellij/cmux
  tab). State is append-only status files + task metadata on disk; `fm-fleet-snapshot.sh`
  emits a structured snapshot for whole-fleet views.
- **Zero-token supervision.** A bash watcher sleeps on the fleet and wakes the first mate
  only when something is actionable; a durable wake queue survives process exits.
- **Disposable worktrees.** Each task gets a clean git worktree so parallel work never
  collides; teardown is fail-closed.
- **Restart-proof.** All state on disk; session start reconciles dead endpoints and resumes.

What transfers directly to goblin: the one-liaison UX, per-crewmate isolated workspaces,
disk-only state, startup reconciliation, and a fleet-wide status view.

What does **not** transfer: the tmux/script process model. firstmate needs external terminal
backends because it runs inside a general-purpose chat harness it doesn't control. Goblin
*is* the harness — it owns the agent loop, so crewmates can be in-process asyncio tasks with
SDK-persisted conversations, which are simpler to supervise and cheaper to view (read JSON
event files, no pane scraping). tmux-style visible terminals are a V2 option (§6.4).

### 3.2 What goblin already provides (verified in-repo)

| Building block | Where | Notes |
|---|---|---|
| Coordinator/worker pattern with typed tools and a shared sqlite board | `goblin/goblin/swarm.py` [L1] | `_run_coordinator`, `_run_single_agent`, `_run_agents` (TaskGroup), `_render_task_board` (status digest injected into prompts — the same trick crew mode uses for event injection) |
| Sync-sqlite coordination DB with WAL, safe from tool-executor threads | `goblin/goblin/swarm_db.py` [L2] | The documented deviation from async-by-default: OpenHands dispatches tool executors on a worker thread pool, so the DB driver is synchronous `sqlite3` with `check_same_thread=False`. Crew mode's registry follows this exactly (AGENTS.md sanctions the swarm exception) |
| Typed tool pattern (`ToolDefinition`/`ToolExecutor`, `db_config` as plain dict through tool params) | `goblin/goblin/tools/swarmdb_tool.py`, `swarm_agent_tool.py` [L3] | Executors open their own DB connection from a serializable config; coordinators get no Bash, so they cannot forge state. The crew lead gets the same posture: only the typed Crew tool, never Bash |
| Conversation runner | `goblin/goblin/conversation.py` [L4] | `run_conversation(agent, workspace, prompt, max_iterations=..., max_cost=...)` → returns the conversation for event/cost reading |
| Circuit breaker | `goblin/goblin/cost_control.py` [L7] | `CircuitBreaker.absorb(conversation)`; crew-level spend cap reuses it |
| Config singleton + env conventions | `goblin/goblin/utils/config.py` [L8] | `GOBLIN_*` env vars, `get_config()`/`reset_config()` |
| Click CLI with lazy subgroup | `goblin/goblin/cli.py` [L6] | Lazy subcommand loading already exists; the `crew` group follows the same pattern |
| Worktree machinery | `goblin/goblin/tools/worktree_tool.py`, phase `worktree:` config [L9] | Per-crewmate worktree isolation (step 6) |
| ACP harness wrapping | `prds/acp_for_alt_harnesses` + installed SDK ACP surface [L10] | The `harness` block design and the `ACPAgentSettings.create_agent()` build seam; crew mode reuses both per-spawn so a crewmate can be a Claude Code / Codex / Gemini CLI session (step 5) |
| Prompt conventions | `goblin/goblin/prompts.py` [L11] | `SWARM_COORDINATOR_PROMPT`/`SWARM_AGENT_PROMPT` are the template for `CREW_LEAD_PROMPT` |

### 3.3 SDK capabilities this design relies on (verified against installed `openhands-sdk` 1.29.3)

1. **Persisted conversations.** `LocalConversation(agent, workspace, persistence_dir=...)`
   persists `base_state.json` plus one JSON file per event under `events/event-NNNNN-<id>.json`,
   autosaving on every event [L5]. A crewmate's entire transcript is therefore a directory of
   small JSON files — readable from any other process with plain file reads (each event is its
   own file, so a partially-written tail can be skipped without a lock protocol).
2. **Multi-turn conversations.** `conversation.send_message(text)` + `await conversation.arun()`
   is the per-turn driver; the lead's chat loop repeats this per user input, and a crewmate
   follow-up (`message` command) is just another turn on the crewmate's conversation. The
   exact resume-after-`arun()` behavior is asserted in the step-1 spike.
3. **Concurrent conversations.** `arun()` is async, so N crewmate conversations run as N
   asyncio tasks in the crew process — the same shape as `SwarmPhase._run_agents` [L1], plus a
   semaphore for a configurable cap.
4. **Tool executors are sync and thread-pooled.** Spawn/list/read/message/stop commands must
   therefore (a) do DB reads/writes via the sync driver [L2], and (b) hand asynchronous work
   to the event loop via `loop.call_soon_threadsafe(...)` rather than creating tasks inline.
5. **ACP agents fit the same conversation path.** `ACPAgent` is an `AgentBase` subclass that
   `LocalConversation` accepts unchanged; it emits standard conversation events (tool-call
   cards, a terminal `FinishAction`) and converts harness usage updates into the same
   `llm.metrics` the circuit breaker reads [L10]. An ACP crewmate therefore persists to
   disk, is viewable via `crew sessions`, and is cost-capped exactly like a native crewmate.
   ACP sessions are keyed by `cwd`, so per-crewmate workspaces (and worktrees) compose.

### 3.4 What does not exist yet (the work this PRD covers)

- No interactive surface of any kind: goblin has no REPL, no persistent top-level agent, and
  no `input()` loop. `cli.py` is `run` / `validate` plus lazily-loaded subcommands only [L6].
- No session registry: a swarm's `agent_state` table is roster-bound and phase-scoped; nothing
  tracks dynamically-spawned, named, resumable sessions across process restarts [L1, L2].
- No spawn-at-runtime tool: `SwarmTasks.create_task` creates board rows for a fixed roster;
  nothing builds a new agent + conversation + workspace on demand [L3].
- No cross-process view of a live conversation: today events are only read in-process after
  `run_conversation` returns [L4].

---

## 4. Options analysis

### Option A — Extend the swarm phase into interactivity

Add `interactive: true` to the swarm phase: pause the state machine between rounds and accept
user chat.

- Pros: reuses the most code (SwarmDB, coordinator/worker prompts).
- Cons: the swarm's core assumptions fight interactivity — the coordinator is **stateless by
  design** (re-prompted with a rendered board each round [L1]), the roster is fixed at setup,
  rounds are batch waves, and a phase is scoped to one blueprint run, so "sessions the lead is
  managing" cannot outlive it. Bolting a chat loop onto the state machine forks its semantics.
- **Verdict: rejected.** The swarm stays what it is (declarative batch fan-out); crew mode is
  a different interaction shape sharing patterns, not code.

### Option B — firstmate's process model: crewmates as external terminal sessions

Spawn each crewmate as a `goblin run` (or raw SDK) subprocess in tmux windows; supervise by
scraping panes / status files.

- Pros: maximal isolation; literally visible terminals; mirrors firstmate 1:1.
- Cons: requires tmux (new hard runtime dependency, against goblin's Python-only footprint);
  supervision becomes string-scraping (firstmate dedicates hundreds of lines of bash to
  busy/idle classification [S2]); spawning a full blueprint per task forces YAML generation
  for ad-hoc work; and goblin gains nothing it can't get cheaper in-process, since it owns
  the agent loop.
- **Verdict: rejected for V1.** A `tmux` backend for visible crewmate terminals is a clean
  V2 addition behind the same registry (§6.4).

### Option C — New interactive crew mode; in-process asyncio sessions; disk-persisted state ✅

- New `goblin crew` command: a chat REPL driving one persistent **crew lead** conversation.
- The lead's only tool is a typed **Crew** tool (`spawn`/`list`/`read`/`message`/`stop`),
  following the swarm typed-tool pattern [L3]; the lead gets **no Bash** — crewmates do all
  project work (firstmate's "guarded by construction" [S1]).
- Each crewmate is an asyncio task running one `LocalConversation` — native SDK `Agent`
  **or** `ACPAgent` wrapping a local third-party harness subprocess [L10] — with a
  `persistence_dir` under the crew home [L4, L5]; a sqlite **CrewRegistry** (swarm_db
  conventions [L2]) tracks sessions, follow-up messages, and lead-visible events.
- `goblin crew sessions` reads the registry + event JSON files from a second terminal — WAL
  allows concurrent readers [L2], and per-event files make live transcripts tail-able [L5].
- Pros: no new required Python dependencies (the ACP library is already vendored by the
  pinned SDK, and harness CLIs are only needed when `harness: acp` is actually used [L10]);
  supervision is real (task handles, not pane scraping); transcripts viewable cross-process
  for free; reuses every goblin pattern above; blueprint spec untouched (crew mode is a CLI
  surface, not a phase — no `spec/v1.json` change).
- Cons: one process hosts all agents (a hard crash kills in-flight turns — mitigated by the
  registry + persisted conversations + startup reconciliation, §5.6); no literal terminal per
  crewmate (V2).
- **Verdict: selected.**

---

## 5. Target design

### 5.1 Concepts

- **Crew** — a named, persistent management context rooted at `<crew_home>/<name>/`
  (default name `default`). One crew = one lead + its crewmates + all state.
- **Crew lead** — the top-level conversational agent the user chats with. Persistent
  multi-turn conversation; only the Crew tool; read-only over projects by construction.
- **Crewmate** — a worker agent session spawned by the lead: one task brief, one workspace,
  one persisted conversation, one registry row.
- **Session** — a crewmate's lifecycle (`pending → running → done | failed | stopped |
  interrupted`), tracked in the registry and viewable via `goblin crew sessions`.

### 5.2 Architecture

```
                 you
                  │  chat (goblin crew)
                  ▼
 ┌─────────────────────────────────────────────┐
 │ crew lead  (persistent LocalConversation)   │  no Bash — Crew tool only
 │  system prompt: CREW_LEAD_PROMPT            │
 └──────────────┬──────────────────────────────┘
                │ spawn / list / read / message / stop   (typed tool, thread pool)
                ▼
 ┌─────────────────────────────────────────────┐        ┌──────────────────────────┐
 │ CrewRegistry  (sqlite, WAL)                 │◀───────│ goblin crew sessions     │
 │  sessions / messages / crew_events          │  read  │ (second terminal, RO)    │
 └──────────────┬──────────────────────────────┘        └───────────┬──────────────┘
                │ handoff via loop.call_soon_threadsafe             │ reads event JSON
                ▼                                                    ▼
 ┌─────────────────────────────────────────────┐        ┌──────────────────────────┐
 │ CrewRuntime (asyncio supervisor)            │        │ <crew_home>/sessions/    │
 │  semaphore-capped tasks, one per crewmate   │───────▶│   <name>/events/*.json   │
 │  writes completion events to crew_events    │persist │   <name>/base_state.json │
 └─────────────────────────────────────────────┘        └──────────────────────────┘
```

A crewmate task wraps either a native SDK `Agent` or an `ACPAgent` driving a local harness
subprocess (Claude Code, Codex, or Gemini CLI over stdio [L10]); both run inside the same
`LocalConversation` + `persistence_dir` path, so everything to the right of the runtime is
harness-agnostic.

### 5.3 Crew home layout

```
.goblin/crew/<name>/
├── crew.db                     # CrewRegistry (sqlite, WAL)
├── lead/                       # crew lead's own persistence_dir (resumable chat)
│   ├── base_state.json
│   └── events/…
├── sessions/
│   └── <crewmate>/             # crewmate persistence_dir
│       ├── base_state.json
│       └── events/event-00001-….json …
└── worktrees/                  # per-crewmate git worktrees (step 6, opt-in)
    └── <crewmate>/
```

`crew_home` defaults to `.goblin/crew` under the cwd (config `GOBLIN_CREW_HOME` [L8]).

### 5.4 CrewRegistry

New `goblin/goblin/crew/db.py`, modeled directly on `swarm_db.py` [L2]: synchronous `sqlite3`
driver, `check_same_thread=False` (tool-executor thread pool), WAL, `init_schema()`,
factory `CrewRegistry.open(path)`.

```sql
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    task TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',     -- pending|running|done|failed|stopped|interrupted
    workspace TEXT NOT NULL,
    persistence_dir TEXT NOT NULL,
    model TEXT,
    result TEXT,                                -- final agent response / error summary
    cost REAL NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS messages (           -- lead → crewmate follow-ups
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name TEXT NOT NULL,
    body TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS crew_events (        -- runtime → lead event queue
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_name TEXT,
    type TEXT NOT NULL,                         -- completed|failed|stopped|interrupted|info
    summary TEXT,
    consumed INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

Write paths are typed methods only (`create_session`, `set_status`, `post_message`,
`mark_delivered`, `emit_event`, `consume_events`, …) — no raw SQL escapes the registry,
matching the swarm rule that agents can't forge state [L2, L3].

### 5.5 The Crew tool (the lead's only tool)

New `goblin/goblin/crew/tools.py` — one typed tool, `command` literal, following
`swarmdb_tool.py` [L3]:

| Command | Args | Behavior |
|---|---|---|
| `spawn` | `name`, `task`, `harness?`, `server?`, `command?`, `model?`, `allowed_tools?`, `worktree?`, `max_cost?`, `max_iterations?` | INSERT session (`pending`) + hand spawn request to the event loop via `call_soon_threadsafe`; returns immediately ("spawned, pending start"). Rejects duplicate names. `harness: openhands` (default) runs the native agent loop; `harness: acp` wraps the named ACP `server` (`claude-code` default, `codex`, `gemini-cli`, `custom` — `custom` requires `command`) [L10]. |
| `list` | — | SELECT all sessions with status/cost/updated; rendered as the fleet table. |
| `read` | `name`, `tail?` | Registry row + last N events read from the session's persisted `events/*.json` (summarized: role/type/text preview). |
| `message` | `name`, `message` | INSERT undelivered message row; the runtime delivers it as the crewmate's next turn (after its current turn, or starting a new turn if `done`/`stopped`). |
| `stop` | `name` | Requests cancellation; runtime cancels the asyncio task; status → `stopped`. |

Harness semantics on spawn: under `harness: acp` the harness owns its tools and permissions,
so `allowed_tools` is **rejected** (native-only) and `model` maps to the harness's model
(`acp_model`); the SDK bridge auto-approves permission requests, matching unattended runs
[L10]. Under `harness: openhands`, `allowed_tools` behaves as in blueprint phases.

Tool description carries the same posture as the swarm coordinator's: *you cannot do project
work yourself; only crewmates touch the filesystem* [L3, L11].

### 5.6 CrewRuntime (supervision)

New `goblin/goblin/crew/runtime.py`. An asyncio supervisor task started beside the chat loop:

1. **Spawn queue.** The tool executor (thread pool) enqueues spawn requests with
   `loop.call_soon_threadsafe`; the runtime awaits them, builds the crewmate, and starts
   `conversation.arun()` under a `max_crewmates` semaphore (default 4). Crewmate build
   branches on the spawn's `harness`:
   - *native* (`harness: openhands`, the default): `build_llm` (spawn model → `crew_model`
     → `default_model` [L8]) + `resolve_tools(allowed_tools)` (the standard goblin tool set
     **without** the Crew tool);
   - *ACP* (`harness: acp`): `ACPAgentSettings(acp_server=server, acp_command=command,
     acp_model=model, acp_isolate_data_dir=True).create_agent()` [L10] — per-crewmate
     data-dir isolation so concurrent ACP sessions never race on shared CLI config
     (`~/.claude`, `CODEX_HOME`); `allowed_tools` is rejected (the harness owns its tools);
   - both get the `CREW_MATE_PROMPT` [L11] ("your brief is all the context you have; report
     a concise outcome as your final message") and a `LocalWorkspace` on the session
     workspace (`<crew_home>/sessions/<name>/ws`, or the session worktree from step 6),
     which for ACP becomes the harness session `cwd`.
2. **Lifecycle writes.** `pending → running` on start; on turn end, absorb spend into the
   crew **CircuitBreaker** [L7], store `result`/`cost`, set `done`/`failed`, and
   `emit_event(...)` for the lead. Exceptions follow `_run_single_agent`'s pattern: log
   `crew.crewmate_failed`, mark failed, never crash the supervisor [L1].
3. **Message delivery.** After each crewmate turn, undelivered `messages` for that session
   become the next turn; a `message` to a `done`/`stopped` session wakes it with that turn.
4. **Stop.** `stop` cancels the task (`LocalConversation` cancellation is an SDK capability
   [L5]) and records `stopped`.
5. **Reconciliation (restart-proofing).** On startup: sessions in `running`/`pending` have no
   live task (the process died) → mark `interrupted` + `emit_event`; the lead's first turn
   includes these events, so it can decide to respawn (`spawn` a replacement) or report.

### 5.7 The chat loop — `goblin crew`

New `goblin/goblin/crew/chat.py`; CLI wiring in `cli.py` via the `LazyGroup` pattern [L6]:

```
goblin crew [--name default] [--model MODEL] [--max-crewmates N] [--max-cost USD]
            [standard logging options]
```

1. Open/create the crew home; `init_schema()`; reconcile (§5.6.5).
2. Build the crew lead: `build_llm(model)` + Crew tool + `CREW_LEAD_PROMPT`
   (role, roster etiquette, brief-writing rules — briefs must be self-contained because
   crewmates never see the chat; escalation rules — only real decisions reach the user;
   outcome reporting format [S1, L11]). `LocalConversation(persistence_dir=<crew_home>/lead)`
   so the chat itself survives restarts [L5].
3. Start the CrewRuntime supervisor task.
4. REPL: read a user line (blocking `input()` via `run_in_executor`; `--quiet`/logging flags
   honored per CLI conventions [L6]) → drain `consume_events()` → send one message combining
   the fleet-event digest (the `_render_task_board` trick [L1]) with the user's text →
   `await conversation.arun()` → print the lead's final response.
5. `exit`/Ctrl-D stops the supervisor politely (running crewmates are asked to finish their
   turn; a second Ctrl-C cancels everything — registry keeps the state either way).

### 5.8 Viewing sessions — `goblin crew sessions`

Read-only commands, safe to run from a second terminal while `goblin crew` is live (WAL
readers [L2]; per-event JSON files [L5]):

```
goblin crew sessions [--name default]                 # fleet table
goblin crew sessions <session> [--tail 20]            # detail + transcript tail
```

- **Fleet table:** name, status, task (truncated), cost, updated — sorted by recency. This is
  the "view all sessions the lead is managing" requirement in one glance.
- **Detail:** registry row (task, workspace, model, cost, result) + the last N persisted
  events rendered as `role/type: text-preview`, reading `events/event-*.json` in lexical
  order and skipping an unparseable tail file (a turn in flight may be mid-write) [L5].

### 5.9 Cost control

- Per-crewmate: `spawn.max_cost` / `spawn.max_iterations` → `run_conversation` kwargs [L4].
- Per-crew: `--max-cost` on `goblin crew`; a `CircuitBreaker` absorbs each finished crewmate
  conversation and each lead turn [L7]; on trip, the runtime stops accepting spawns and the
  lead is told (it reports to the user instead of silently dying — swarm behavior [L1]).

### 5.10 Configuration

| Variable | Default | Purpose |
|---|---|---|
| `GOBLIN_CREW_HOME` | `.goblin/crew` | Crew home root |
| `GOBLIN_CREW_MAX_CREWMATES` | `4` | Concurrency semaphore |
| `GOBLIN_CREW_MODEL` | `GOBLIN_DEFAULT_MODEL` | Lead model (crewmates default to it too, overridable per spawn) |

All via `goblin/utils/config.py` conventions (`env()`, frozen dataclass, `reset_config()`)
[L8]; CLI flags override env.

ACP crewmates authenticate per the ACP design [L10]: provider keys (`ANTHROPIC_API_KEY`,
`OPENAI_API_KEY`, `GEMINI_API_KEY`) come from the ambient environment and are never logged;
the harness CLI itself (the SDK's pinned `npx` commands, or an explicit `command`) is a
runtime prerequisite only when `harness: acp` is actually used.

---

## 6. Requirements

### 6.1 Functional

| ID | Requirement |
|---|---|
| F1 | `goblin crew` starts an interactive chat with a persistent crew lead; the lead's conversation survives process restarts via its `persistence_dir`. |
| F2 | The lead can `spawn` a crewmate with a name + self-contained task brief (optional model/tools/cost limits); the runtime runs it as an asyncio task under the concurrency cap; duplicates rejected. |
| F3 | The lead can `list` all sessions with live status, `read` a session's recent activity, `message` a session (delivered as the crewmate's next turn), and `stop` a running session. |
| F4 | Crewmate completion/failure/stop posts an event the lead sees on its next turn, and is recorded in the registry with result + cost. |
| F5 | `goblin crew sessions` lists every session the lead manages (name, status, task, cost, updated) from a separate process, safe against a live crew. |
| F6 | `goblin crew sessions <name> [--tail N]` shows full task/status/result plus the last N transcript events, tolerant of in-flight writes. |
| F7 | Startup reconciliation marks orphaned `running`/`pending` sessions `interrupted` and surfaces them to the lead. |
| F8 | The lead has no Bash/filesystem tools — all project work happens inside crewmate sessions. |
| F9 | Per-crewmate and per-crew cost limits enforced via the existing circuit breaker. |
| F10 | (Step 5) `spawn(harness: acp, server: ...)` runs the crewmate on the named local ACP harness (Claude Code, Codex, Gemini CLI, or a custom command); the session persists, is viewable via `crew sessions`, and is cost-capped identically to native crewmates. `allowed_tools` is rejected for ACP spawns (the harness owns its tools). |
| F11 | (Step 6) `spawn(worktree: true)` isolates the crewmate in a git worktree under the crew home. |

### 6.2 Non-functional

- **CLI-only.** Crew mode ships exclusively as `goblin` CLI commands (the `goblin crew` chat
  REPL and the `goblin crew sessions` views). No HTTP server, no API, no TUI, no new
  entry-point processes beyond the harness subprocesses ACP spawns.
- **notifications-relay is frozen** (`AGENTS.md`): crew mode must not import, read,
  reference, or integrate with that project. Review checklist: zero matches for
  `notifications` / `relay` under `goblin/goblin/crew/` and in this PRD's steps.
- Repo conventions per `AGENTS.md`: `from __future__ import annotations`, type hints, pydantic
  where parsing, structured logging with dot-notation events (`crew.spawned`,
  `crew.crewmate_done`, `crew.crewmate_failed`, `crew.reconciled`, `crew.stopped`) [L12].
- Registry follows the sanctioned swarm DB exception: sync `sqlite3`, `check_same_thread=False`,
  WAL, typed write methods only [L2, L12].
- No changes to `spec/v1.json`, blueprint phases, or the swarm phase — crew mode is a new CLI
  surface; `goblin run`/`validate` behavior is untouched.
- No new required dependencies. The REPL uses stdlib (`input` via executor) — no
  prompt_toolkit in V1. ACP support uses the SDK-vendored `agent-client-protocol` library
  already present as a transitive dependency [L10]; harness CLIs are an opt-in runtime
  prerequisite, not a Python dependency.
- Secrets posture unchanged: `GOBLIN_MODEL_API_KEY` via existing config; ACP provider keys
  (`ANTHROPIC_API_KEY` etc.) ride the ambient environment per [L10]; never logged [L8].
- Tests per conventions: unit tests mock at the project boundary (conversation runner, tool
  executors with real registry on `tmp_path`); integration tests use vcr cassettes and no MCP
  servers [L12].

### 6.3 Changes by file (all in `goblin/` unless noted)

| File | Change |
|---|---|
| `goblin/crew/__init__.py` | New package. |
| `goblin/crew/db.py` | `CrewRegistry` (schema §5.4, typed methods). |
| `goblin/crew/runtime.py` | `CrewRuntime` supervisor (spawn queue, semaphore, lifecycle, message delivery, stop, reconciliation). |
| `goblin/crew/tools.py` | Typed `Crew` tool (§5.5) + registration. |
| `goblin/crew/acp.py` | Harness branch: map spawn args → `ACPAgentSettings(...).create_agent()` [L10]; ACP-only spawn validation (§5.5). |
| `goblin/crew/chat.py` | REPL + lead construction + CLI entry helpers. |
| `goblin/crew/sessions_view.py` | Read-only fleet/detail rendering for `crew sessions`. |
| `goblin/prompts.py` | `CREW_LEAD_PROMPT`, `CREW_MATE_PROMPT` [L11]. |
| `goblin/cli.py` | Lazy `crew` group: `goblin crew`, `goblin crew sessions [name]` [L6]. |
| `goblin/utils/config.py` | `crew_home`, `crew_max_crewmates`, `crew_model` + env vars [L8]. |
| `goblin/enums.py` | `CrewSessionStatus` (mirrors `TaskStatus` style). |
| `docs/cli/goblin.md`, `docs/` new `crew.md` | Command reference + crew mode guide. |
| `AGENTS.md` (root) / `CLAUDE.md` | Note crew mode + new env vars. |
| `goblin/tests/unit/crew/` | Registry, runtime (mocked conversation boundary), tools, chat-loop (injected input), sessions view tests. |
| `goblin/tests/integration/` | One vcr integration: spawn a real crewmate end-to-end, assert persistence + registry transitions. |

### 6.4 Out of scope (V2+)

- **Server, API, or TUI surfaces** — crew mode is CLI-only by design (§6.2). Any future
  remote hosting would be a separate PRD composing with `prds/agent_server_plan` [L13], not
  an extension of this one.
- **Visible terminal backends** (tmux/zellij panes per crewmate, firstmate-style [S1, S2]) —
  V1's view surface is `crew sessions`; the registry/backend seam allows a terminal backend
  later.
- **Blueprint crewmates** — spawning a full `goblin run <blueprint>` as a session (heavy tasks
  with phases); V1 crewmates are single-prompt conversations (native or ACP).
- **Remote ACP** — harness subprocesses on a remote host; same local-only boundary as the
  ACP PRD [L10].
- **Crewmate → user mid-run questions** — `AskUser` routed up through the lead's chat
  surface, and richer lead↔crewmate signal channels (the swarm's `signals` table idea [L2])
  beyond lead-initiated messages.
- **A dashboard/`--follow` live tailing** for sessions.

---

## 7. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Multi-turn `arun()`/`send_message()` or `persistence_dir` resume behaves differently than assumed | Chat loop or crewmate wake-up breaks | Step-1 spike asserts exact behavior on the pinned SDK before any feature code; integration surface kept to public conversation APIs [L5] |
| Hard crash mid-turn | Crewmate work lost; stale `running` rows | Registry + per-event persisted conversations bound the loss to the in-flight turn; reconciliation marks `interrupted`; lead can respawn from the persisted transcript (§5.6.5) |
| Reading live event files from `crew sessions` hits a partial write | Garbled tail in the view | Per-event files + lexical ordering; the view skips an unparseable newest file; WAL readers never block the writer [L2, L5] |
| Lead abuses `spawn` (runaway fleet, cost) | Spend blowout | Concurrency cap + per-spawn and per-crew circuit breakers (§5.9); spawn count surfaced in `list`; lead prompt requires justification per spawn [L11] |
| One process hosts all agents: a crewmate wedge (e.g. stuck tool) blocks the loop | Whole crew stalls | `arun()` is async and per-crewmate `max_iterations`/`max_cost` bound turns; `stop` cancels; SDK stuck-detector exists [L5]; V2 process isolation noted (§6.4) |
| Thread-pool executor ↔ event-loop handoff races (spawn before runtime ready) | Lost spawn | Spawn requests are durable registry rows *first* (`pending`); the runtime drains `pending` rows on startup as well as the live queue, so the queue is an optimization, not the source of truth |
| Lead conversation grows unbounded | Context blowout in long crews | Lead is instructed to keep briefs self-contained and summaries short; SDK compaction is a conversation capability to verify in the spike; `--max-cost` backstops [L5] |
| ACP harness subprocess leaks on crewmate failure/cancel | Orphan `node` processes, cost leak | Rely on SDK cleanup in `conversation.close()` [L10]; the runtime closes every crewmate conversation in a `finally`; step-5 tests assert process reaping on failure paths |
| Concurrent ACP crewmates race on shared CLI config (`~/.claude`, `CODEX_HOME`) | Corrupt auth/config state | `acp_isolate_data_dir=True` is forced per crewmate (§5.6); isolation is credential-blind and per-conversation [L10] |
| ACP PRD (`prds/acp_for_alt_harnesses`) not yet landed when step 5 starts | No shared harness-resolution helpers to reuse | Step 5 implements the minimal `ACPAgentSettings` build inline in `goblin/crew/acp.py` (a small, public seam per [L10]); if the ACP PRD lands first, reuse its resolution helpers instead |
| Harness CLI missing or `npx` first-run latency for ACP spawns | Spawn fails or stalls | Spawn-time validation returns a typed error the lead reports; provider commands are version-pinned by the SDK; `command` override points at preinstalled binaries [L10] |

---

## 8. Step plan

| Step | File | Goal |
|---|---|---|
| 1 | `step_01_spike_and_registry.md` | Spike SDK multi-turn/persistence/offline-read assumptions; land `CrewRegistry` + config + enums |
| 2 | `step_02_crew_runtime_and_tools.md` | `CrewRuntime` supervisor + typed `Crew` tool with lifecycle, message delivery, stop, reconciliation |
| 3 | `step_03_chat_loop_and_cli.md` | `goblin crew` REPL: lead prompt, event digest injection, circuit breaker, CLI wiring |
| 4 | `step_04_sessions_command.md` | `goblin crew sessions` fleet + detail views (cross-process, live-safe) |
| 5 | `step_05_acp_harness_crewmates.md` | ACP-wrapped crewmates: harness spawn branch, per-session isolation, provider verification |
| 6 | `step_06_worktrees_and_docs.md` | Per-crewmate worktrees; docs/AGENTS/ROADMAP updates |

Steps 1–3 are the MVP critical path (chat with the lead, spawn and supervise crewmates).
Step 4 completes the user's "view all sessions" requirement as a first-class surface.
Step 5 adds ACP harness wrapping; step 6 is hardening and polish. All six are V1 scope.

---

## 9. Sources

### External

- **[S1]** firstmate README — https://github.com/kunchenguid/firstmate
  ("Talk to one agent. Ship with a crew."; one liaison; visible crew; disposable worktrees;
  guarded-by-construction read-only first mate; restart-proof state on disk).
- **[S2]** firstmate `docs/architecture.md` —
  https://raw.githubusercontent.com/kunchenguid/firstmate/main/docs/architecture.md
  (event-driven zero-token supervision with durable wake queue; runtime session backends;
  fleet snapshot contract; worktree isolation and fail-closed teardown; startup
  reconciliation of dead endpoints).

### In-repo / installed (paths relative to repo root unless noted)

- **[L1]** `goblin/goblin/swarm.py` — `SwarmPhase` (`_run_coordinator`, `_run_single_agent`,
  `_run_agents` TaskGroup, `_render_task_board` status-digest injection, `CircuitBreaker`
  usage, exception → agent-failed logging pattern).
- **[L2]** `goblin/goblin/swarm_db.py` — sync `sqlite3`/turso backends, WAL,
  `check_same_thread=False` rationale (tool executors on thread pools), typed write paths
  (`create_task` INSERT-only), `has_outstanding_tasks` anti-forgery.
- **[L3]** `goblin/goblin/tools/swarmdb_tool.py`, `goblin/goblin/tools/swarm_agent_tool.py` —
  typed `ToolDefinition`/`ToolExecutor` pattern, serializable `db_config` through tool params,
  coordinator-has-no-Bash posture.
- **[L4]** `goblin/goblin/conversation.py` — `run_conversation(...)` (Local `.arun()` vs
  Remote `to_thread`, `max_iterations`/`max_cost` kwargs).
- **[L5]** Installed `openhands-sdk 1.29.3`
  (`.venv/lib/python3.13/site-packages/openhands/sdk/`): `conversation/impl/local_conversation.py`
  (`persistence_dir`, `send_message`/`arun`); `conversation/state.py` (`ConversationState`
  autosave, `persistence_dir` field); `conversation/event_store.py` (`EventLog`,
  thread/process-safe per-event files); `conversation/persistence_const.py`
  (`base_state.json`, `events/event-NNNNN-<id>.json` naming); `conversation/cancellation.py`.
- **[L6]** `goblin/goblin/cli.py` — Click `LazyGroup` (lazy subcommand loading pattern),
  global logging options, `run`/`validate` surface.
- **[L7]** `goblin/goblin/cost_control.py` — `CircuitBreaker.absorb()`, `LimitExceeded`.
- **[L8]** `goblin/goblin/utils/config.py`, `goblin/goblin/utils/env.py` — env-driven frozen
  config singleton, `resolve_env_vars`; `goblin/goblin/utils/llm.py` — `build_llm`.
- **[L9]** `goblin/goblin/tools/worktree_tool.py` + phase `worktree:` support —
  existing worktree isolation machinery.
- **[L10]** `prds/acp_for_alt_harnesses/README.md` — the ACP `harness` block design this PRD
  reuses for crewmates, plus the installed SDK ACP surface it documents
  (`.venv/lib/python3.13/site-packages/openhands/sdk/agent/acp_agent.py` — `ACPAgent`;
  `settings/model.py` — `ACPAgentSettings.create_agent()`; `settings/acp_providers.py` —
  provider registry, pinned `npx` commands, data-dir isolation levers).
- **[L11]** `goblin/goblin/prompts.py` — `BASE_SYSTEM_PROMPT`, `SWARM_COORDINATOR_PROMPT`,
  `SWARM_AGENT_PROMPT` (role/roster/board prompt conventions).
- **[L12]** `AGENTS.md` — repo conventions (typed tools, sync-DB exception, structured
  logging events, unit-over-integration testing, vcr integration rules, no MCP in
  integration tests; the notifications-relay development freeze).
- **[L13]** `prds/agent_server_plan/README.md` — sibling PRD (composition boundary
  referenced in §6.4; PRD format template).
- **[L14]** `goblin/VISION.md`, `ROADMAP.md` — project positioning (orchestration between
  chat and maximalist frameworks) this feature extends interactively.
