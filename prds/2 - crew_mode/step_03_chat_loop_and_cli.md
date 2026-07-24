# Step 3 — `goblin crew` chat loop and CLI wiring

**Goal:** The user-facing interactive mode: a persistent crew lead conversation in a REPL,
with fleet events injected into the lead's turns and cost control across the whole crew.

**Depends on:** step 2. **Blocks:** step 4 (sessions are only worth viewing once the chat
can create them — though step 4 can be built in parallel against a hand-seeded registry).

---

## 3.1 Crew lead construction

In `goblin/goblin/crew/chat.py`:

- Agent: `build_llm(--model → crew_model → default_model)` [L8], tools = **only** the Crew
  tool (no Bash, no file tools — README F8), system prompt `CREW_LEAD_PROMPT` added to
  `prompts.py` [L11]. The prompt covers:
  - Role: you are the crew lead; the user (captain) talks only to you; you ship with a crew.
  - Decompose requests into self-contained briefs (crewmates never see this chat — include
    paths, constraints, acceptance criteria, and where to write outputs).
  - Supervise: check `list` before spawning duplicates; `read` a session before reporting on
    it; never claim work you haven't verified via the tool.
  - Escalate only real decisions (approvals, ambiguous goals, repeated failures); report
    outcomes plainly with cost when asked [S1].
  - Fleet events arrive prepended to user messages as a digest — acknowledge and act on them.
- Conversation: `LocalConversation(agent, LocalWorkspace(cwd),
  persistence_dir=<crew_home>/lead)` so the chat survives restarts (spike §1.1.2) [L5].

## 3.2 REPL

```
goblin crew [--name default] [--model MODEL] [--max-crewmates N] [--max-cost USD]
            [--log-config …] [--log-level …] [--log-file …] [--quiet]
```

Flow (async main, wired in `cli.py` via the existing `LazyGroup` lazy-subcommand pattern
[L6]; config loaded lazily inside the command per CLI conventions):

1. `load_dotenv_from_cwd()`, `configure_logging(logger_name="goblin-crew", …)` — log file
   default `goblin-crew.jsonl` [L6, L12].
2. Open crew home `<crew_home>/<name>/`, `init_schema()`, construct `CrewRuntime` and
   `start()` it (reconciliation events land in the queue — §5.6.5).
3. Build the lead conversation (§3.1) and the crew `CircuitBreaker` from `--max-cost` [L7].
4. Loop:
   - `line = await loop.run_in_executor(None, input, "crew> ")`; `exit`/EOF breaks.
   - Drain `consume_events()`; if any, prepend a rendered digest
     (`### Fleet events\n- [completed] fix-login: …`) to the user's text — the
     `_render_task_board` injection trick [L1].
   - `send_message(combined)` → `await arun()` → absorb lead turn spend into the breaker →
     print `get_agent_final_response(events)` [L4].
   - `LimitExceeded` → tell the user the crew budget is exhausted, stop accepting input
     cleanly (runtime still lets running crewmates finish or the user quits).
5. Shutdown: `runtime.shutdown()` — first attempt graceful (finish current turns), second
   Ctrl-C cancels; registry keeps truth either way.

Non-goals (V1): no prompt_toolkit, no slash commands, no streaming render — plain stdout.

## 3.3 CLI wiring

- `cli.py`: add `crew` to `LazyGroup.list_commands`/`get_command`, lazy-importing
  `goblin.crew.chat:crew` (a Click group holding the `crew` chat command now and
  `crew sessions` in step 4) [L6].
- Invocation shape: `goblin crew` starts the chat (the group with no subcommand invokes the
  chat by default, Click `invoke_without_command=True`); `goblin crew sessions …` lands in
  step 4.

## 3.4 Tests

- Unit: chat loop driven with an **injected input callable** and a mocked lead conversation —
  assert event digests are prepended, spend is absorbed, EOF exits, breaker trip message
  prints. CLI smoke: `--help` renders; lazy import doesn't slow `goblin validate`.
- Integration (vcr, no MCP [L12]): script the lead end-to-end — seed a crew on `tmp_path`,
  run one scripted user turn ("spawn a crewmate to write hello.txt"), let a real crewmate
  conversation run against the cassette, assert: `hello.txt` exists in the crewmate
  workspace, registry shows `done`, and the lead's next scripted turn receives the
  `completed` digest. This is the PRD's golden-path test.

## Acceptance criteria

- [ ] `goblin crew` starts, chats, spawns, reports; Ctrl-D exits cleanly; a second
      `goblin crew` against the same crew home resumes the lead conversation and reconciles.
- [ ] Golden-path integration test passes deterministically under vcr.
- [ ] `task goblin:test`, `task goblin:lint`, typecheck all green.
- [ ] Lead has no Bash: a scripted attempt to make it do file work itself cannot succeed
      (asserted via the tool list, not the prompt).
