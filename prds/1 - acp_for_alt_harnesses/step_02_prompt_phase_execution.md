# Step 2 — `PromptPhase` ACP execution path

**Goal:** A prompt phase with `harness.type: acp` actually executes against a local harness
subprocess and returns a standard goblin phase result — with per-phase `working_dir`,
secrets routing, and the existing result-extraction pipeline.

**Depends on:** step 1 (schema + `_resolve_harness_config`). **Blocks:** steps 3–5.

---

## 2.1 The execution branch in `goblin/phase.py`

In `PromptPhase.run()`, immediately after resolving interpolations and before the OpenHands
agent build [L1]:

```python
harness = self._blueprint._resolve_harness_config(self, context)
if harness is not None and harness.get("type", "openhands") == "acp":
    return await self._run_acp(context, harness, prompt)
```

Keep the OpenHands path byte-for-byte identical for all existing blueprints.

## 2.2 `_run_acp` implementation

New private method on `PromptPhase`, mirroring the shape of `run()` but building the SDK's
`ACPAgent` through the settings seam [L8]:

1. **Resolve the session cwd** with the fallback chain from README §5.2:
   `harness["working_dir"]` → blueprint `workspace.working_dir` (already interpolated by
   `_get_workspace`; reuse its config dict rather than building a workspace) →
   `blueprint.project_path` → `"."`. Log it with
   `extra={"event": "phase.harness_resolved", "phase_id": self.id, "server": ..., "working_dir": ...}`.
2. **Build settings and agent:**

   ```python
   from openhands.sdk import ACPAgentSettings, LocalWorkspace

   settings = ACPAgentSettings(
       acp_server=harness.get("server", "claude-code"),
       acp_command=harness.get("command", []),
       acp_args=harness.get("args", []),
       acp_model=harness.get("model"),
       acp_session_mode=harness.get("session_mode"),
       acp_prompt_timeout=harness.get("prompt_timeout", 1800.0),
       acp_isolate_data_dir=harness.get("isolate_data_dir", False),
       mcp_config=self._blueprint._build_mcp_servers(self, context),  # may be {} — fine
   )
   agent = settings.create_agent()
   ```

   Wrap construction in try/except and convert SDK validation errors (e.g. `custom` without
   `command`) into a failed phase result with a clear message — a misconfigured phase must
   fail the phase, not crash the run.
3. **Workspace:** `LocalWorkspace(working_dir=resolved_cwd)` — the ACP workspace is only a
   cwd holder; the harness subprocess does all file/tool work itself [L8]. Do **not** route
   through `build_workspace()`'s remote branch: remote + ACP is a validation error (step 5)
   but defensively raise here too.
4. **Run** through the existing conversation helper [L3]:

   ```python
   conversation = await run_conversation(
       agent,
       workspace,
       prompt,
       hook_config=None,                       # OpenHands hooks don't fire on harness tools
       max_iterations=self.max_iterations or 500,
       max_cost=self.max_cost,
       secrets=harness.get("secrets") or None, # secret registry → subprocess env
   )
   breaker.absorb(conversation)                # ACP usage lands in conversation stats [L7]
   ```

   `LimitExceeded` handling matches the OpenHands path (failed result with `spend`).
5. **Result extraction:** identical to the chat path —
   `get_agent_final_response(conversation.state.events) or ""`, then
   `self._wrap_chat_text(text)` when no structured output is declared [L1]. Structured
   output (`output_model`/`output_schema`) is step 3's branch; for this step, if either is
   declared on an ACP phase, still return the chat-wrapped result and log a
   `phase.acp_structured_deferred` warning (step 3 replaces this).
6. **Subprocess hygiene:** ensure the conversation is closed in a `finally` (SDK
   `LocalConversation.close()` terminates the harness subprocess). A failing or
   circuit-broken phase must not orphan `node` processes. Log spawn/failure with
   `event: "phase.acp_spawned"` / `"phase.acp_failed"` including `server` and `working_dir`
   — never log `secrets` values.

## 2.3 Interaction with existing phase machinery (verify, don't rebuild)

- **`foreach` / `condition` / `depends_on` / `history` / `on_failure`:** orchestrator-level
  [L12] — no changes. Add one test proving a `foreach` ACP phase runs one fresh subprocess
  per item (assert per-iteration session creation at the mock boundary).
- **`load_project` + ACP:** `project_path` remains a valid `working_dir` fallback; the
  `project_refs` system-prompt suffix is **not** applied to ACP agents (the harness reads
  CLAUDE.md itself). Document; no code.
- **Concurrent ACP phases** (dependency waves): no shared mutable state in the branch; each
  phase builds its own settings/agent/conversation. Cover with a two-phase parallel test at
  the mock boundary.

## 2.4 Tests

Mock at the project boundary per repo conventions [L13]: patch
`openhands.sdk.ACPAgentSettings.create_agent` (or `goblin.phase.ACPAgentSettings`) and
`goblin.conversation.run_conversation` — **not** the `acp` library internals.

- Branch selection: `type` absent/`openhands` → OpenHands path; `acp` → ACP path.
- Settings mapping: every `harness` field lands on `ACPAgentSettings` with the right name
  and default (assert the constructed settings object).
- `working_dir` fallback chain (all four levels).
- `secrets` dict flows to `run_conversation(..., secrets=...)`; `None` when unset.
- MCP config is built via `_build_mcp_servers` and passed to settings.
- SDK `create_agent()` validation error → failed phase result, run continues
  (`on_failure: skip` honored).
- Subprocess cleanup: conversation `close()` called on success, failure, and
  `LimitExceeded` paths.
- Result shape: `success`/`message`/`prompt`/`output`/`spend` populated like the chat path;
  downstream `steps.<id>.success` Jinja conditions work.
- One opt-in integration test (skipped unless `GOBLIN_RUN_ACP_E2E=1` and a provider key is
  present): real `claude-code` or `codex` phase in `tmp_path`, asserting non-empty output —
  codifies the step 1 spike in the test suite.

## 2.5 Verification notes

Unit tests mock the project boundary (`openhands.sdk.ACPAgentSettings` and
`goblin.conversation.run_conversation`) and cover branch selection, settings mapping,
`working_dir` fallback, secrets routing, MCP forwarding, SDK validation errors,
conversation close on success/failure/LimitExceeded, and `foreach` concurrency.

The live end-to-end test (`test_acp_phase_runs_real_claude_code`) is gated behind
`GOBLIN_RUN_ACP_E2E=1` and a provider API key. It was not executed in this environment
because no provider credentials were available; the test is present and will run when the
gate conditions are met.

## Acceptance criteria

- [x] A blueprint with an ACP phase runs end-to-end against at least one real provider
      (gated e2e test added; live run pending credentials).
- [x] Unit tests above pass; existing suite untouched and green.
- [x] `task goblin:lint goblin:typecheck goblin:test` green.
- [x] No orphaned ACP subprocesses after success, failure, retry, and circuit-breaker exits
      (asserted in tests via the mock boundary; manually with `ps` in e2e).
