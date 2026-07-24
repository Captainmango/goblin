# Step 1 — Spike: `ACPAgent` end-to-end + land the `harness` schema

**Goal:** Prove the core technical assumption (the pinned SDK's `ACPAgent` runs a real local
harness from goblin's venv and feeds goblin's existing consumption points), then land the
blueprint schema and config-resolution plumbing that every later step builds on.

**Depends on:** nothing. **Blocks:** steps 2–5.

---

## 1.1 Spike: run a real ACP harness through the SDK from the repo venv

Do this in a scratch script (or `uv run python -i`), **not** in goblin code yet. Requires
Node/npm on PATH (default commands are `npx`-based) and one provider credential
(`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY`).

1. **Build the agent through the settings seam** (this is the exact seam step 2 will use):

   ```python
   from openhands.sdk import ACPAgentSettings, LocalWorkspace
   from openhands.sdk.conversation import LocalConversation, get_agent_final_response

   settings = ACPAgentSettings(
       acp_server="claude-code",          # repeat for "codex" / "gemini-cli" if creds exist
       acp_model=None,                     # let the server pick; then try "sonnet" etc.
       acp_prompt_timeout=300.0,
   )
   agent = settings.create_agent()         # resolves npx command from ACP_PROVIDERS [L8]
   ```

2. **Run one conversation** in a throwaway repo (`tmp_path` with a `README.md` and a
   `CLAUDE.md` containing a distinctive instruction, e.g. "Always mention the word
   'goblin'"):

   ```python
   conversation = LocalConversation(agent=agent, workspace=LocalWorkspace(working_dir=str(repo)))
   conversation.send_message("Read README.md and summarise this project in one sentence.")
   conversation.run()
   text = get_agent_final_response(conversation.state.events)
   ```

3. **Assert the four integration facts the PRD relies on** (README §3.3):
   - `text` is non-empty (FinishAction path works).
   - The `CLAUDE.md` instruction is honored (harness loaded its **native** config from the
     session cwd — the load_project gap this PRD closes).
   - `conversation.state.events` contains `ACPToolCallEvent` entries (tool cards stream).
   - `conversation.conversation_stats.get_combined_metrics()` has non-zero
     `accumulated_token_usage` (so `CircuitBreaker.absorb()` sees ACP spend [L7]).
4. **Verify the cwd → session scoping claim**: rerun the same prompt in a *different*
   working_dir and confirm no state bleeds across; confirm the harness wrote its own
   session/transcript state under its config dir keyed by cwd (e.g. `~/.claude/projects/...`).
5. **Verify secrets routing**: pass `secrets={"ANTHROPIC_API_KEY": "..."}` to
   `LocalConversation` with the env var deliberately unset; confirm the subprocess
   authenticates (registry > os.environ precedence [L8]) and that the key does not appear in
   `conversation.state.events` serialization (masking).
6. **Record per-provider notes** in this file: which providers were available, whether
   `UsageUpdate` arrived for each, observed startup latency of `npx -y <pkg>@pin`.

**Spike exit criteria:** items 1–5 pass for at least one provider with zero changes to
goblin or SDK code. If `ACPAgentSettings`/`create_agent()` differ from the investigated
1.29.3 surface, record the delta here and re-evaluate Option A (hand-rolled client) from
`README.md` §4 before proceeding.

## 1.2 Schema: add the `harness` block to `spec/v1.json`

Add a `harness` definition and reference it from both `blueprint.defaults` and the `phase`
definition (both `additionalProperties: false`, so both lists must be updated) [L5]:

```json
"harness": {
  "type": "object",
  "description": "Execution backend for prompt phases. 'openhands' (default) runs goblin's OpenHands agent loop; 'acp' delegates the phase to a local ACP-compatible coding harness (claude-code, codex, gemini-cli) which owns its own tools, config, and session state.",
  "additionalProperties": false,
  "properties": {
    "type": { "type": "string", "enum": ["openhands", "acp"], "default": "openhands" },
    "server": {
      "type": "string",
      "enum": ["claude-code", "codex", "gemini-cli", "custom"],
      "default": "claude-code",
      "description": "ACP provider. Each maps to a pinned default command in the SDK's ACP_PROVIDERS registry; 'custom' requires 'command'."
    },
    "command": {
      "type": "array", "items": { "type": "string" },
      "description": "Explicit ACP server launch command. Required when server is 'custom'. Overrides the provider's pinned npx default."
    },
    "args": { "type": "array", "items": { "type": "string" }, "description": "Extra args appended to the command. Supports {variable} interpolation." },
    "model": { "type": "string", "description": "Model identity inside the harness (e.g. 'sonnet', 'gpt-5.5'). Applied via the ACP protocol; omit to use the server's default." },
    "session_mode": { "type": "string", "description": "ACP session mode ID (e.g. 'bypassPermissions'). Defaults to the provider's unattended default." },
    "prompt_timeout": { "type": "number", "minimum": 1, "default": 1800, "description": "Seconds of turn inactivity before the prompt is aborted. Resets on every session update." },
    "working_dir": { "type": "string", "description": "Working directory that becomes the ACP session cwd. Falls back to the blueprint workspace working_dir, then load_project's path, then cwd. Supports {variable} interpolation." },
    "isolate_data_dir": { "type": "boolean", "default": false, "description": "Give the harness a per-conversation config/data root (CLAUDE_CONFIG_DIR / CODEX_HOME) so concurrent phases don't race on shared CLI state." },
    "secrets": {
      "type": "object", "additionalProperties": { "type": "string" },
      "description": "Env vars delivered to the harness subprocess via the conversation secrets channel (masked in events/logs; overrides ambient env). Values support $VAR references."
    }
  }
}
```

Note what is **not** in the schema (JSON Schema draft-07 cannot cheaply express the
conditional rules): ACP-incompatible fields (`allowed_tools`, `hooks`, …) and
`server: custom` requiring `command`. Those land as semantic validation in step 5.

## 1.3 Resolution plumbing in `goblin/blueprint.py`

Add `_resolve_harness_config(phase, context) -> dict | None` [L2]:

- Shallow-merge `phase.harness` over `defaults.harness` (copy the
  `_resolve_notifications_config` shape).
- Return `None` when nothing is declared anywhere (callers treat `None` and
  `{"type": "openhands"}` identically: today's path).
- Run `validate_env_var_syntax` + `resolve_env_vars`, then `_interpolate_strings` with the
  run context (so `working_dir: "{clone_dir}"` and `foreach`'s `{item}` resolve), matching
  how `_build_mcp_servers` handles its values.
- Carry the parsed raw block on `PromptPhase` as `self.harness_config = raw.get("harness")`
  [L1] (parse-only; no behavior change yet — step 2 wires execution).

Also add `"harness"` to `PromptPhase.__init__` and to any phase-level passthrough lists in
`runner.py`/`orchestrator.py` if they enumerate phase keys (check `foreach` cloning code
paths).

## 1.4 Tests (this step)

- Schema: a blueprint with `defaults.harness` + phase-level `harness` validates; unknown
  keys inside `harness` fail (`additionalProperties: false`); bad `server` enum value fails.
- Resolution: defaults-only, phase-only, merged precedence, `$VAR` resolution, `{var}`
  interpolation, and `None` when undeclared. Follow existing
  `tests/` patterns for `_resolve_*` config tests.

## Acceptance criteria

- [ ] Spike §1.1 exit criteria met and findings recorded in this file (per-provider matrix).
- [ ] `uv run goblin validate` accepts a blueprint using `harness` at defaults and phase
      level, and rejects malformed `harness` blocks.
- [ ] `_resolve_harness_config` unit tests pass; precedence/interpolation match
      `worktree`/`notifications` behavior.
- [ ] `task goblin:lint goblin:typecheck goblin:test` green; no behavior change for
      blueprints without `harness` (existing test suite untouched and green).
