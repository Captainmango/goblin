# PRD: ACP Support for Alternative Coding Harnesses

**Status:** Proposed
**Author:** Investigation + planning (AI-assisted)
**Date:** 2026-07-21

---

## 1. Summary

Teach goblin to execute prompt phases through **local, third-party coding harnesses** —
Claude Code, Codex, and Gemini CLI — using the **Agent Client Protocol (ACP)** instead of the
built-in OpenHands agent loop. A new `harness` block, declarable at **blueprint `defaults`
level or per phase**, selects the execution backend (`openhands` — today's behavior — or
`acp`) and carries ACP-specific settings.

Two motivations:

1. **Harness-native configuration.** The `load_project` phase re-implements a narrow slice of
   Claude Code's config surface (`.claude/settings.json` hooks, `CLAUDE.md` references,
   `.claude/agents/*.md`) [L2]. Real harnesses load far more natively — settings hierarchy,
   permissions, skills, subagents, `.mcp.json`, output styles. Delegating the phase to the
   harness itself makes all of that configuration work without goblin having to re-implement
   (and perpetually chase) each harness's config surface.
2. **Multi-repo runs.** ACP sessions are scoped to a `cwd` and owned by the harness
   subprocess — not by goblin's blueprint-level workspace or by OpenHands. A per-phase
   `harness.working_dir` therefore lets one blueprint run work on several repositories at
   once, with each harness managing its own session state per repo.

The key decision of this PRD: **reuse the `ACPAgent` that already ships inside the pinned
`openhands-sdk` 1.29.3 — do not hand-roll an ACP client.** The investigation below shows the
SDK contains a complete, production-grade ACP integration (subprocess management, auth,
session lifecycle, permission brokering, MCP forwarding, usage accounting) plus a vendored
`agent-client-protocol` 0.11.0 library, and that goblin's existing conversation/result/cost
machinery consumes ACP conversations unchanged.

---

## 2. Problem statement

Today every prompt phase runs through the same path: `PromptPhase.run()` builds an
`openhands.sdk.Agent` (goblin-owned LLM, goblin-resolved tools, goblin-built system prompt)
and drives it via `run_conversation()` against a `LocalWorkspace` or `RemoteWorkspace` [L1,
L3, L4]. Consequences:

- **Only goblin's interpretation of project config is available.** `load_project` reads three
  things from a project root: `.claude/settings.json` (shell hooks only), `CLAUDE.md` /
  `.claude/CLAUDE.md` (concatenated into the system prompt), and `.claude/agents/*.md` [L2].
  Everything else a Claude Code user relies on — `.claude/settings.local.json`, permission
  rules, skills, `.mcp.json`, keybindings, output styles, nested `CLAUDE.md` hierarchy — is
  invisible to goblin. Users who already have a fully-configured harness must duplicate
  config into blueprint YAML (`mcp_servers`, `hooks`) and accept the gaps.
- **One workspace per blueprint run.** `_get_workspace()` builds a single workspace from the
  blueprint-level `workspace:` block (defaulting to `project_path` or cwd) [L2, L4]. A
  blueprint that must touch two repos can only do it by having the agent `cd` around inside
  one workspace — no per-phase repo scoping, no harness-native per-repo context.
- **Session state is goblin/OpenHands-owned.** Conversation persistence, compaction, and
  resume live in the SDK's conversation state. Users cannot continue a phase's work in their
  own harness (`claude --resume`-style), because the harness never owned the session.

The user's requirements, restated:

1. Call **local** coding agents over ACP from a blueprint phase.
2. Let the harness own session/config concerns (the things `load_project` cannot cover).
3. Support working on **multiple repos in one run** via per-phase session `cwd`.
4. Configurable at **phase level and `defaults` level** in blueprints.
5. No backwards-compatibility constraints — goblin is alpha; break shapes freely.

---

## 3. Investigation

### 3.1 What ACP is

The **Agent Client Protocol** (agentclientprotocol.com, by Zed Industries) is a JSON-RPC 2.0
protocol spoken between a **client** (typically an editor; here: goblin) and an **agent**
(a coding assistant, typically spawned as the client's subprocess over stdio) [S1]:

- **Handshake:** `initialize` (version/capability negotiation) → optional `authenticate`.
- **Session setup:** `session/new` (carries `cwd`, `mcpServers`) or `session/load` to resume.
- **Prompt turn:** `session/prompt` → agent streams `session/update` notifications (message
  chunks, thoughts, tool-call start/progress, plan updates, usage) → prompt response carries
  a stop reason. `session/cancel` interrupts.
- **Client callbacks the agent may invoke:** `session/request_permission` (tool-call
  authorization), `fs/read_text_file` / `fs/write_text_file`, `terminal/*` — all gated on
  capabilities advertised during `initialize`.
- Session modes (`session/set_mode`, e.g. `bypassPermissions`) and model selection
  (`set_config_option("model")` / `session/set_model`) are server-advertised capabilities.

ACP-compatible coding harnesses include Claude Code (via
`@agentclientprotocol/claude-agent-acp`), OpenAI Codex (via `@zed-industries/codex-acp`), and
Gemini CLI (`--acp` flag) [L8]. The Python client library is `agent-client-protocol` —
**0.11.0 is both the latest on PyPI and already installed in goblin's venv** as a declared
dependency of `openhands-sdk>=0.10.1` [S2, L9].

### 3.2 Headline finding: the pinned OpenHands SDK already ships a complete ACP integration

`openhands-sdk 1.29.3` (goblin's exact pin [L12]) contains [L8]:

| Component | Location | What it provides |
|---|---|---|
| `ACPAgent` | `openhands/sdk/agent/acp_agent.py` | `AgentBase` subclass that delegates to an ACP subprocess server. Handles spawn, stdout JSON-RPC filtering, `initialize`, auth-method selection, `session/new` + `session/load` (resume via `state.agent_state["acp_session_id"]`, cwd-guarded), session mode, model application, prompt turns with inactivity watchdog (default 1800 s) and transient retries (3). |
| `ACPAgentSettings` | `openhands/sdk/settings/model.py` | Settings model with `acp_server` (`"claude-code" \| "codex" \| "gemini-cli" \| "custom"`), `acp_command`, `acp_args`, `acp_model`, `acp_session_mode`, `acp_prompt_timeout`, `acp_isolate_data_dir`, `acp_file_secrets`, `mcp_config`; `create_agent()` resolves the provider's default command and builds the `ACPAgent`. |
| Provider registry | `openhands/sdk/settings/acp_providers.py` | `ACP_PROVIDERS` with pinned default commands: `npx -y @agentclientprotocol/claude-agent-acp@0.44.0`, `npx -y @zed-industries/codex-acp@0.16.0`, `npx -y @google/gemini-cli@0.46.0 --acp`; per-provider auth env vars, session-mode defaults (`bypassPermissions`, `full-access`), model lists, data-dir relocation levers (`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `HOME`). |
| Client bridge | `_OpenHandsACPBridge` (same file) | Implements the `acp` `Client` protocol: accumulates `session/update` chunks, auto-approves `session/request_permission`, answers `fs/read_text_file`/`fs/write_text_file`, live-emits `ACPToolCallEvent`s, masks secrets in output. |
| Event type | `openhands/sdk/event/acp_tool_call.py` | `ACPToolCallEvent` — tool-call cards in the conversation event stream. |
| ACP library | `acp` (site-packages) | `ClientSideConnection`, `spawn_agent_process`, full pydantic schema models (schema v1.16.0, protocol version 1) [L9]. |

### 3.3 Why goblin's existing machinery already fits ACP conversations

Verified against the installed SDK [L8]:

1. **`LocalConversation` accepts `ACPAgent` unchanged.** Its constructor takes
   `agent: AgentBase`; `ACPAgent` is an `AgentBase` subclass whose `llm` field is a dummy
   sentinel (`model="acp-managed"`) used only for metrics attribution. `ACPAgent.init_state`
   reads `state.workspace.working_dir` and passes it as the `cwd` of `session/new` — **the
   workspace is just a cwd holder for ACP; all tool execution happens inside the harness
   subprocess.** `run_conversation()` needs zero changes [L3].
2. **Final-response extraction works unchanged.** At turn end, `ACPAgent` joins the
   accumulated `AgentMessageChunk` text and emits a terminal `ActionEvent` with
   `FinishAction(message=...)` (`tool_name="finish"`) plus a `FinishObservation` — exactly
   the shape `get_agent_final_response(events)` pattern-matches first [L8]. Goblin's output
   pipeline (`PhaseResult` wrapping, message surfaces, Jinja `steps.<id>` access) is
   unaffected [L1].
3. **Cost/token accounting flows into the same stats.** `ACPAgent` converts `UsageUpdate`
   notifications into `self.llm.metrics` entries (`add_token_usage`, `add_cost`,
   `add_response_latency`) on the attribution LLM — which is precisely what
   `conversation_stats.get_combined_metrics()` aggregates, and what goblin's
   `CircuitBreaker.absorb()` reads (`accumulated_token_usage`, `accumulated_cost`) [L7, L8].
   `max_cost`/`max_tokens` circuit breakers keep working (spike confirms in step 1).
4. **MCP forwarding is built in.** `ACPAgentSettings.mcp_config` takes the same
   `{"mcpServers": {...}}` dict goblin already merges from defaults+phase [L2]; the SDK
   translates it to ACP `session/new` servers (stdio always; http/sse gated on advertised
   server capabilities) [L8]. Blueprint `mcp_servers` remain meaningful under ACP.
5. **Credentials ride channels goblin can already drive.** The subprocess env is assembled
   as `default_environment() < os.environ < conversation secret_registry` (registry wins);
   reserved file-content secrets (Codex `auth.json`, Gemini service-account JSON) are
   materialised to disk with `0600`/`0700` perms [L8]. `run_conversation()` already accepts
   `secrets=` — goblin just never passes it today [L3].
6. **Sessions are harness-owned and cwd-keyed — the multi-repo primitive.** ACP servers
   persist sessions keyed by `cwd`; `ACPAgent` resumes via `session/load` only when the cwd
   matches, else starts fresh [L8]. Two phases with different `working_dir`s therefore get
   independent, harness-managed sessions in independent repos — concurrently if the
   dependency graph allows.

### 3.4 What the SDK does *not* do for goblin (the work this PRD covers)

- No blueprint concept: nothing maps YAML to `ACPAgentSettings`; nothing resolves
  defaults-vs-phase precedence; nothing interpolates `{variables}` or `$ENV_VARS`.
- No semantic validation: nothing tells a user that `allowed_tools` or `hooks` are
  meaningless under ACP (the harness owns its tools and permission flow; the bridge
  auto-approves).
- Structured outputs: the typed Finish tool / `response_format` machinery is OpenHands-agent
  machinery; the harness owns its own LLM loop, so goblin's in-loop structured-output path
  cannot apply. A coercion path is needed (§5.5).
- `acp_prompt_timeout`, session mode, data-dir isolation, and provider selection need
  blueprint-facing names and docs.

### 3.5 Compatibility of existing phase fields under `harness.type: acp`

| Phase/defaults field | Under ACP | Disposition |
|---|---|---|
| `prompt`, `condition`, `foreach`, `depends_on`, `history`, `on_failure`, `vars` | Works unchanged (orchestrator-level concerns) | Keep |
| `harness.working_dir` | Becomes the ACP session `cwd` (per-phase repo) | **New** |
| `mcp_servers` | Forwarded to `session/new` by the SDK | Keep |
| `model` / `llm` | Harness owns its LLM; goblin's `llm` config is only used for the structured-output coercion follow-up (§5.5) | Redefined, documented |
| `max_cost`, `max_tokens` | Read from ACP usage accounting (§3.3.3) | Keep (spike-verified) |
| `max_iterations` | Meaningless — a phase is one prompt turn; `harness.prompt_timeout` is the guard | Validation warning, ignored |
| `allowed_tools`, `hooks` | Harness owns tools/permissions; bridge auto-approves; OpenHands hooks never fire on harness-internal tool calls | Validation error |
| `worktree` | OpenHands-tool-specific | Validation error |
| `output_model` / `output_schema` | Via goblin-side coercion after the turn (§5.5) | Keep (new path) |
| `workspace: {type: remote}` | ACP spawns a **local** subprocess; remote workspaces don't apply | Validation error when combined |

---

## 4. Options analysis

### Option A — Hand-roll an ACP client on the vendored `acp` library

Build a goblin-native `ACPConversation` directly on `ClientSideConnection` /
`spawn_agent_process` [L9], then adapt `session/update` streams into `PhaseResult`s.

- Pros: full control over the event model; no dependency on SDK ACP internals.
- Cons: re-implements what the SDK already ships and maintains: subprocess lifecycle, stdout
  log-line filtering, auth-method selection per provider, permission brokering, session
  resume semantics, MCP capability gating, file-secret materialisation, usage→metrics
  plumbing, prompt inactivity watchdogs, transient retries. It would also bypass
  `conversation.state.events` — goblin's result/cost/history pipeline expects an SDK
  conversation [L1, L3, L7] — forcing a second, parallel consumption path.
- **Verdict: rejected.** Duplication of ~3 800 lines of battle-tested SDK code, plus a fork
  in goblin's result-consumption path.

### Option B — Reuse `openhands.sdk.ACPAgent` inside `PromptPhase` ✅

Resolve a new `harness` block in the blueprint; when `type: acp`, build
`ACPAgentSettings(...).create_agent()` and run it through the **existing**
`run_conversation()` → `LocalConversation` path [L3, L8].

- Pros: ~all protocol mechanics inherited (§3.2); result extraction, circuit breakers, MCP
  config, and secrets channels already line up (§3.3); upgrade path is just the existing
  SDK pin bump; tiny goblin-side surface (config resolution + a build branch + validation).
- Cons: ACP behavior is coupled to the SDK's implementation choices (e.g. permission
  auto-approval, `npx`-based default commands); the vendored `acp` library version moves
  with the SDK pin, not independently.
- **Verdict: selected.**

### Option C — Shell out to harness CLIs directly (`claude -p`, `codex exec`, …)

- Pros: zero protocol work; CLIs are stable.
- Cons: no structured session model (no cwd-scoped resume, no permission brokering, no
  cancellation semantics), brittle stdout scraping, per-CLI output formats, and it re-solves
  exactly what ACP standardises. Loses the multi-repo session story (§3.3.6).
- **Verdict: rejected.**

---

## 5. Target design

### 5.1 Blueprint surface: the `harness` block

New optional key `harness` in `blueprint.defaults` and in each phase (merges shallowly,
phase over defaults — the established `worktree`/`notifications` pattern [L2]):

```yaml
version: v1
blueprint:
  name: cross_repo_fix
  vars:
    api_dir: /home/dev/repos/api-service
    ui_dir: /home/dev/repos/web-ui

  defaults:
    harness:
      type: acp                    # openhands (default when omitted) | acp
      server: claude-code          # claude-code | codex | gemini-cli | custom
      model: sonnet                # model identity inside the harness (acp_model)
      session_mode: bypassPermissions
      prompt_timeout: 1800         # seconds of turn inactivity before abort
      secrets:                     # forwarded into the harness subprocess env
        ANTHROPIC_API_KEY: $ANTHROPIC_API_KEY   # via the SDK secret registry

  phases:
    - id: fix_api
      prompt: "Fix the null-pointer in the payments endpoint; add a regression test."
      harness:
        working_dir: "{api_dir}"   # ACP session cwd → this repo

    - id: fix_ui
      prompt: "Update the checkout form for the new payments error shape."
      depends_on: [fix_api]
      harness:
        working_dir: "{ui_dir}"    # different repo, different harness session
```

`harness` fields (schema definition `harness`, all strings supporting `$VAR` env references;
`working_dir`/`args` additionally `{variable}` interpolation, resolved at phase-build time
like `mcp_servers` [L2]):

| Field | Type | Default | Maps to (`ACPAgentSettings`) | Notes |
|---|---|---|---|---|
| `type` | `openhands \| acp` | `openhands` | — | `openhands` = today's path, untouched. |
| `server` | `claude-code \| codex \| gemini-cli \| custom` | `claude-code` | `acp_server` | Selects the provider registry entry (default command, auth env var, session-mode default). |
| `command` | `list[str]` | `[]` | `acp_command` | Explicit launch command; **required** when `server: custom`. Overrides the provider default (e.g. a pre-installed binary instead of `npx`). |
| `args` | `list[str]` | `[]` | `acp_args` | Extra args appended to the command. |
| `model` | `str` | server default | `acp_model` | Applied via the protocol (`set_config_option`/`set_session_model`). |
| `session_mode` | `str` | provider default (`bypassPermissions` / `full-access`) | `acp_session_mode` | Permission posture for unattended runs. |
| `prompt_timeout` | `number` (s) | `1800` | `acp_prompt_timeout` | Inactivity watchdog per prompt turn; resets on every session update. |
| `working_dir` | `str` | blueprint workspace `working_dir` → `project_path` → cwd | workspace `working_dir` → ACP `session/new` `cwd` | The multi-repo knob. |
| `isolate_data_dir` | `bool` | `false` | `acp_isolate_data_dir` | Per-conversation harness config root; avoids `~/.claude` / `CODEX_HOME` races between concurrent phases. |
| `secrets` | `map[str, str]` | `{}` | conversation `secrets=` | Routed through the SDK secret registry (masks output; wins over ambient env) [L3, L8]. Provider keys (e.g. `ANTHROPIC_API_KEY`) may also simply live in the ambient environment — `os.environ` is inherited. |

### 5.2 Resolution and precedence

New `V1Blueprint._resolve_harness_config(phase, context) -> dict | None` [L2]:

1. `phase.harness` shallow-merged over `defaults.harness` (same pattern as
   `_resolve_worktree_config` / `_resolve_notifications_config`).
2. `None`/absent or `type: openhands` → today's behavior, zero changes.
3. `$VAR` env resolution via `resolve_env_vars`, then `{variable}` interpolation via
   `_interpolate_strings` with the run context (so `working_dir: "{clone_dir}"` and
   per-`foreach` `{item}` values work).
4. `working_dir` fallback chain: `harness.working_dir` → blueprint `workspace.working_dir`
   → `project_path` (from `load_project`) → `"."`.

### 5.3 Execution flow (phase with `harness.type: acp`)

```
PromptPhase.run(context)
  └─ harness = blueprint._resolve_harness_config(self, context)
  └─ if harness.type == "openhands": → existing code path, unchanged
  └─ else (acp):
       1. Build settings: ACPAgentSettings(
            acp_server=harness.server, acp_command=harness.command,
            acp_args=harness.args, acp_model=harness.model,
            acp_session_mode=harness.session_mode,
            acp_prompt_timeout=harness.prompt_timeout,
            acp_isolate_data_dir=harness.isolate_data_dir,
            mcp_config=blueprint._build_mcp_servers(self, context))   # reuse [L2]
       2. agent = settings.create_agent()                              # [L8]
       3. workspace = LocalWorkspace(working_dir=resolved_working_dir) # cwd → session/new
       4. conversation = await run_conversation(agent, workspace, prompt,
                hook_config=None,              # OpenHands hooks don't apply (§3.5)
                max_iterations=..., max_cost=self.max_cost,
                secrets=harness.secrets)       # secret registry → subprocess env [L3]
       5. breaker.absorb(conversation)         # unchanged — ACP usage lands in stats [L7]
       6. text = get_agent_final_response(conversation.state.events)   # unchanged [L1]
       7. Structured output (only if output_model/output_schema set): coercion path (§5.5)
```

Process model: **one ACP subprocess + one fresh session per phase execution** (each
`foreach` iteration is its own execution). The harness persists its own transcripts keyed by
cwd — session continuity across goblin runs is the harness's business, matching the
"harness manages its own sessions" goal. Concurrent ACP phases (dependency waves,
`depends_on` parallelism) each get their own subprocess; `isolate_data_dir` covers shared
`HOME`-config races for claude-code/codex [L8].

### 5.4 Multi-repo model

Because the ACP session `cwd` is per-phase and the harness keys its own session/config state
by `cwd`, a blueprint can fan out across repos without any goblin-side workspace juggling:

- Each phase's harness loads **that repo's** native config (`CLAUDE.md` hierarchy,
  `.claude/settings.json`, `.mcp.json`, permissions) — no `load_project` needed for ACP
  phases (it remains available for OpenHands phases and for setting `project_path`).
- `depends_on` waves run phases against **different repos concurrently**; each harness
  subprocess is independent.
- Blueprint `workspace:` (local/remote) and `harness.working_dir` interact as follows:
  blueprint `workspace.type: remote` + any ACP phase = validation error (§3.5); local
  blueprint workspace merely supplies the default `working_dir`.

### 5.5 Structured outputs under ACP

The in-loop typed Finish tool cannot apply — the harness owns its LLM and its Finish
semantics. For ACP phases declaring `output_model`/`output_schema`:

1. Take the harness's final text (§3.3.2).
2. Try `coerce_to_model(text, output_model)` directly (cheap, no LLM) [L1].
3. Else reuse the existing Layer-2 follow-up: `_request_structured_output(prompt,
   output_model, llm_config=resolved goblin llm)` — a stateless goblin-side LLM call with
   `response_format` [L1]. This is why `defaults.model`/`llm` stay relevant for ACP
   blueprints.
4. Else the existing synthetic-failure fallback [L1].
5. `output_schema` (inline JSON Schema) validation still runs against the coerced dump.

### 5.6 Credentials

Precedence for the harness subprocess env (SDK behavior [L8]):
`harness.secrets` (secret registry) **>** ambient `os.environ` **>** SDK defaults. So:

- `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`/`GEMINI_API_KEY` exported in the shell "just work".
- `harness.secrets` exists for per-blueprint overrides and for values that must be masked in
  logs/events (the registry's masker is bound for the conversation's lifetime [L8]).
- `GOBLIN_MODEL_API_KEY`/`GOBLIN_DEFAULT_MODEL` remain for the OpenHands path and the
  structured-output coercion follow-up.
- Codex `auth.json` / Gemini SA JSON file credentials are SDK-materialised when supplied as
  the reserved secret names [L8]; document the names rather than re-implementing.

---

## 6. Requirements

### 6.1 Functional

| ID | Requirement |
|---|---|
| F1 | A prompt phase with `harness.type: acp` executes its prompt against a local ACP harness subprocess and returns a standard phase result (`success`/`message`/`output`/`spend`). |
| F2 | `harness` is declarable in `blueprint.defaults` and per phase; phase shallow-merges over defaults. `type` omitted/absent = `openhands` = today's exact behavior. |
| F3 | `server` selects among `claude-code`, `codex`, `gemini-cli` (SDK default commands) and `custom` (requires `command`). |
| F4 | `harness.working_dir` scopes the phase's ACP session `cwd`; two phases in one run may target different repos, including concurrently via `depends_on` waves. |
| F5 | Blueprint `mcp_servers` (defaults+phase) are forwarded to the ACP session via the SDK's translation. |
| F6 | `harness.secrets` are passed to the conversation secrets channel and reach the subprocess env; values are masked in events/logs. |
| F7 | `max_cost` / `max_tokens` circuit breakers apply to ACP phases using the SDK's usage accounting. |
| F8 | `output_model`/`output_schema` on ACP phases produce validated structured results via the coercion path (§5.5). |
| F9 | `goblin validate` rejects incoherent combinations (§3.5): ACP + `allowed_tools`, `hooks`, `worktree`, `notifications`, remote blueprint `workspace`; `server: custom` without `command`. Warns on `max_iterations` under ACP. |
| F10 | `foreach`, `condition`, `depends_on`, `history`, `on_failure` (incl. retry) work identically for ACP phases. |

### 6.2 Non-functional

- Repo conventions per `AGENTS.md`: type hints, `from __future__ import annotations`,
  `goblin_common` config/logging, structured `extra={"event": ...}` logging
  (`phase.harness_resolved`, `phase.acp_spawned`, `phase.acp_failed`), atomicity of existing
  checkpoint writes untouched [L13].
- Alpha software: no migration shims; schema changes are breaking by design.
- No new required dependencies: `agent-client-protocol` is already a transitive dep of the
  pinned SDK [S2, L9]. Runtime prerequisite: the harness CLI itself (default commands use
  `npx`; document the Node requirement and the `command` override for preinstalled binaries).
- Never log `harness.secrets` values; rely on the SDK secret masker [L8].
- ACP subprocesses must not outlive a failed/cancelled run: rely on SDK cleanup in
  `close()`; verify in tests that phase failure terminates the child process.

### 6.3 Changes by file (all in `goblin/` unless noted)

| File | Change |
|---|---|
| `spec/v1.json` | Add `harness` definition; reference it from `defaults` and `phase`; bump schema description. |
| `goblin/blueprint.py` | Add `_resolve_harness_config()`; expose resolved value to phases. |
| `goblin/phase.py` | `PromptPhase`: read `raw.get("harness")`; branch to ACP build/run path (§5.3); structured-output coercion branch (§5.5). |
| `goblin/validation.py` | Semantic checks beyond JSON Schema: ACP-incompatible field combinations (F9). |
| `goblin/conversation.py` | No change expected (`secrets=` param already exists) — confirmed in step 2. |
| `docs/configuration/harness.md` | New page: block reference, credential model, session semantics. |
| `docs/phases/prompt.md`, `docs/phases/load-project.md` | Cross-reference; document §3.5 field interactions. |
| `blueprints/acp_example.yaml` | New example: two-repo run with defaults-level harness. |
| `goblin/tests/` | Unit tests for resolution/validation/branching; ACP execution tests mocked at `ACPAgent`/`run_conversation` boundary per repo testing conventions [L13]. |
| `AGENTS.md` (root) | Note the new harness capability + any env vars in the table. |

### 6.4 Out of scope (V2+)

- **Remote ACP** (harness subprocess on a remote host / inside `goblin-server`'s runtime) —
  V1 is local-only by design.
- **Swarm-phase ACP workers** (`swarm.agents[].harness`) — the swarm's coordinator/task
  machinery is OpenHands-native; ACP fan-out needs its own design.
- **Explicit session resume** (`acp_resume_session_id` plumbing through blueprints),
  `session/list` browsing, and human-in-the-loop permission prompts (V1 auto-approves,
  matching unattended runs).
- ACP support in the `goblin-server` PRD's remote runtime (compose with
  `prds/agent_server_plan` later; the two PRDs are independent).
- `isolate_data_dir` for gemini-cli (SDK relocates `HOME` — wide blast radius; document,
  don't automate).

---

## 7. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| SDK ACP internals shift on pin bumps (`ACPAgent` is newer SDK surface) | ACP path breaks on upgrade | Keep goblin's integration surface to `ACPAgentSettings.create_agent()` + `run_conversation()` (public-ish seams); step 1 spike asserts the exact 1.29.3 behavior; bump pins deliberately with the spike re-run. |
| `npx -y <pkg>@pin` first-run latency / no Node on host | Phase startup slow or fails | Document Node requirement; `command` override points at preinstalled binaries; provider pins make downloads reproducible [L8]. |
| Permission auto-approval is the SDK's only mode | Harness runs tools without asking — surprising users | This matches unattended blueprint runs; surface `session_mode` prominently in docs; default to each provider's SDK default and let blueprints pin stricter modes. |
| A harness emits no parseable final text | Structured-output phases degrade | §5.5 coercion chain (direct coerce → LLM follow-up → synthetic failure) reuses proven goblin machinery [L1]. |
| Concurrent ACP phases race on shared CLI config (`~/.claude`, `CODEX_HOME`) | Corrupt auth/config state | `isolate_data_dir: true` documented for parallel blueprints; SDK relocation is per-conversation and credential-blind [L8]. |
| Harness CLI versions drift from SDK pins (`@0.44.0` etc.) | Protocol mismatch | SDK pins exact npm versions in default commands; `command` override is the escape hatch; validation warns when `server` + custom `command` diverge from the pin. |
| Cost accounting missing for some harness (no `UsageUpdate`) | `max_cost` silently unlimited | Spike (step 1) verifies per provider; if a provider emits no usage, document `max_cost` as best-effort for it and keep `prompt_timeout` as the hard guard. |
| Subprocess leak on phase failure/cancel | Orphan `node` processes, cost leak | Tests assert `conversation.close()`/process reaping on failure paths; `on_failure: retry` always starts a fresh subprocess. |

---

## 8. Step plan

| Step | File | Goal |
|---|---|---|
| 1 | `step_01_spike_and_schema.md` | Spike `ACPAgent` end-to-end from the repo venv (one real prompt per available provider); land the `harness` schema + resolution plumbing |
| 2 | `step_02_prompt_phase_execution.md` | `PromptPhase` ACP execution branch: agent build, per-phase `working_dir`, secrets, result extraction, tests |
| 3 | `step_03_structured_output_and_cost_control.md` | Coercion path for `output_model`/`output_schema`; verify circuit breakers against ACP usage stats |
| 4 | `step_04_mcp_and_multi_repo.md` | MCP forwarding verification; multi-repo example blueprint; concurrency + `isolate_data_dir` guidance |
| 5 | `step_05_validation_docs_and_examples.md` | Semantic validation rules (F9); docs pages; example blueprints; AGENTS.md update |

Steps 1–2 are the V1 critical path (a phase can run on Claude Code/Codex/Gemini). Steps 3–5
complete the contract; all five are V1 scope.

---

## 9. Sources

### External

- **[S1]** Agent Client Protocol — *Protocol Overview*: https://agentclientprotocol.com/protocol/overview
  (JSON-RPC 2.0 model; `initialize`/`authenticate`; `session/new` with `cwd` + `mcpServers`;
  `session/prompt` + `session/update`; `session/cancel`; `session/request_permission`;
  `fs/*`; `terminal/*`; capability negotiation; session modes).
- **[S2]** `agent-client-protocol` on PyPI: https://pypi.org/project/agent-client-protocol/
  (latest release 0.11.0 — identical to the version vendored in goblin's venv; "A Python
  implement[ation] of Agent Client Protocol (ACP, by Zed Industries)").

### In-repo / installed (paths relative to repo root unless noted)

- **[L1]** `goblin/goblin/phase.py` — `PromptPhase.run()` (agent build, structured-output
  layers, `_request_structured_output`, `_wrap_chat_text`, `_fallback_result`),
  `LoadProjectPhase`; `goblin/goblin/output_schema.py` (`coerce_to_model`).
- **[L2]** `goblin/goblin/blueprint.py` — `_load_project()` (settings/CLAUDE.md/agents
  subset), `_resolve_worktree_config`/`_resolve_notifications_config` (defaults+phase
  shallow-merge pattern), `_build_mcp_servers`, `_get_workspace`, `_interpolate_strings`.
- **[L3]** `goblin/goblin/conversation.py` — `run_conversation(..., secrets=None)`;
  LocalConversation vs RemoteConversation selection.
- **[L4]** `goblin/goblin/workspace.py` — `build_workspace()` (local/remote).
- **[L5]** `spec/v1.json` — blueprint JSON Schema (`defaults`, `phase`, `workspace`,
  `mcpServers` definitions).
- **[L6]** `goblin/goblin/validation.py` — schema validator + error shape.
- **[L7]** `goblin/goblin/cost_control.py` — `CircuitBreaker.absorb()` reads
  `conversation_stats.get_combined_metrics()` (`accumulated_token_usage`, `accumulated_cost`).
- **[L8]** Installed `openhands-sdk 1.29.3` (`.venv/lib/python3.13/site-packages/openhands/sdk/`):
  `agent/acp_agent.py` (`ACPAgent`, `_OpenHandsACPBridge`, env assembly
  `default_environment() < os.environ < secret_registry`, `_materialise_file_secrets`,
  `_isolate_acp_data_dir`, cwd-keyed session resume, `FinishAction` emission, usage→`llm.metrics`);
  `settings/model.py` (`ACPAgentSettings`, `ACPServerKind`, `create_agent()`);
  `settings/acp_providers.py` (`ACP_PROVIDERS`: claude-agent-acp 0.44.0, codex-acp 0.16.0,
  gemini-cli 0.46.0; session-mode defaults; data-dir levers);
  `conversation/impl/local_conversation.py` (accepts `AgentBase`);
  `conversation/response_utils.py` (`get_agent_final_response`);
  `event/acp_tool_call.py` (`ACPToolCallEvent`);
  `workspace/base.py` (`BaseWorkspace`).
- **[L9]** Installed `agent-client-protocol 0.11.0`
  (`.venv/lib/python3.13/site-packages/acp/`): `interfaces.py` (`Client`/`Agent` protocols),
  `client/connection.py` (`ClientSideConnection`), `stdio.py` (`spawn_agent_process`),
  `schema.py` (protocol v1, schema v1.16.0), `meta.py`.
- **[L10]** `docs/phases/`, `docs/configuration/workspace.md`, `blueprints/test.yaml` —
  documentation and example conventions.
- **[L11]** `goblin/pyproject.toml` + `uv.lock` — `openhands-sdk~=1.29.3`,
  `openhands-tools~=1.29.3` pins.
- **[L12]** `goblin/goblin/orchestrator.py`, `goblin/goblin/dependency_graph.py` — phase
  waves, `foreach`/`condition`/`depends_on` execution (unchanged by this PRD).
- **[L13]** `AGENTS.md` — repo conventions (logging events, testing, config).
- **[L14]** `goblin/goblin/cli.py` — `validate` command (findings → exit 1; no severity
  concept today).
