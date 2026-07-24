# Step 5 — ACP harness crewmates

**Goal:** Let the crew lead spawn crewmates that run on wrapped third-party coding harnesses
(Claude Code, Codex, Gemini CLI) over ACP — same session lifecycle, persistence, viewing, and
cost control as native crewmates.

**Depends on:** step 2 (runtime + tool). **Blocks:** nothing (step 6 composes with it —
an ACP crewmate's `cwd` can be a worktree).

---

## 5.1 Harness build branch (`goblin/crew/acp.py`)

New module owning the spawn-args → agent mapping, per README §5.5/§5.6:

- `build_acp_agent(spawn, session_dir) -> ACPAgent` [L10]:
  `ACPAgentSettings(acp_server=spawn.server or "claude-code",
  acp_command=spawn.command, acp_model=spawn.model,
  acp_isolate_data_dir=True).create_agent()`.
- **Isolation is forced per crewmate**: the isolated data dir lives under
  `<crew_home>/sessions/<name>/` so concurrent ACP crewmates never share `~/.claude` /
  `CODEX_HOME` state [L10].
- The returned agent is handed to the same step-2 conversation path
  (`LocalConversation(agent, workspace, persistence_dir=...)` → `send_message`/`arun`) —
  result extraction, `breaker.absorb()`, and transcript persistence are unchanged [L4, L7].
- If `prds/acp_for_alt_harnesses` has landed, reuse its resolution helpers; otherwise this
  module is the minimal inline build (a small public SDK seam — README §7).
- Provider credentials come from the ambient environment (`ANTHROPIC_API_KEY` /
  `OPENAI_API_KEY` / `GEMINI_API_KEY`) per [L10]; never logged.

## 5.2 Spawn surface changes

Extend the step-2 `Crew` tool action and runtime spawn request:

- New fields: `harness: Literal["openhands", "acp"] = "openhands"`, `server: str | None`,
  `command: list[str] | None`.
- Validation (typed errors returned to the lead, README F10):
  - `server: custom` requires `command`;
  - `allowed_tools` with `harness: acp` is rejected (the harness owns its tools/permissions);
  - `harness: acp` requires the server CLI to be launchable — do a cheap spawn-time check
    (e.g. resolve the executable) so a missing CLI fails the spawn immediately instead of
    wedging a session;
  - `model` under ACP maps to `acp_model` (the harness's own model identity).
- `CREW_LEAD_PROMPT` gains harness guidance: default to native; reach for `harness: acp`
  when the task suits a specific coding harness or its native project config; note that ACP
  crewmates auto-approve permissions (unattended posture [L10]).

## 5.3 Lifecycle hardening for subprocesses

- The runtime closes every crewmate conversation in a `finally` (SDK cleanup terminates the
  harness subprocess [L10]); `stop` cancellation follows the same path.
- ACP inactivity watchdog: rely on the SDK's `acp_prompt_timeout` default (1800 s); a wedged
  harness turn ends as `failed` with the timeout reason, not a hung session.
- Status/result recording is unchanged: the harness's final text lands in `result`; usage
  flows into the crew circuit breaker via `llm.metrics` [L7, L10].

## 5.4 Tests

Repo testing rules apply: integration tests use vcr for HTTP, and vcr cannot capture ACP's
subprocess stdio — so ACP execution stays at the unit boundary [L12]:

- Unit: spawn-arg validation matrix (custom-without-command, `allowed_tools` + acp, missing
  CLI) → typed errors; `build_acp_agent` maps fields onto `ACPAgentSettings` correctly
  (spy at `create_agent`); isolation dir is under the session dir and unique per crewmate.
- Unit: runtime with a **mocked `ACPAgent`** (fake `arun` writing canned events): an ACP
  crewmate goes `pending → running → done`, result/cost persisted, conversation closed in
  `finally` on both success and exception paths (assert close called).
- Manual verification (recorded in this file, not CI): one real `harness: acp` spawn per
  locally available provider, asserting the session appears in `crew sessions` and the
  harness subprocess is reaped afterward — mirroring the ACP PRD's own spike [L10].

## Acceptance criteria

- [ ] `spawn(harness: acp, server: ...)` runs a crewmate end-to-end (mocked agent) with
      identical registry/viewing/cost behavior to native crewmates.
- [ ] Validation rejects the incoherent spawn combinations with lead-readable errors.
- [ ] Two concurrent mocked ACP crewmates get distinct isolated data dirs.
- [ ] Conversation close (subprocess reaping) asserted on success, failure, and stop paths.
- [ ] Manual per-provider verification recorded above; `task goblin:test`, lint, typecheck
      green.
