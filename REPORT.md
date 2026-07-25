# PRD 1 Completion Review — ACP Support for Alternative Coding Harnesses

**Date:** 2026-07-25
**Reviewer:** OpenCode
**Scope:** Review the success criteria in `prds/1 - acp_for_alt_harnesses/` and assess general code quality now the feature is present.

---

## Executive Summary

PRD 1 is **functionally complete**. The `harness` block has been landed in the blueprint schema, config resolution, `PromptPhase` execution, structured-output coercion, cost control, semantic validation, documentation, and examples. All automated quality gates pass:

- `task lint` — passed
- `task typecheck` — passed (0 errors)
- `task goblin:test` — 751 unit tests passed
- `task validate` — example blueprints valid; VCR cassettes clean
- `uv run pytest tests/integration/test_acp_harness.py` — 3 integration tests passed

The implementation closely follows the PRD design and reuses the OpenHands SDK's `ACPAgent`/`ACPAgentSettings` seam as intended. There are a handful of minor code-quality findings that should be tidied before the feature is considered fully polished.

---

## 1. Success Criteria Review

### 1.1 Functional Requirements

| ID | Requirement | Status | Evidence |
|---|---|---|---|
| F1 | ACP phase executes against a local harness subprocess and returns a standard phase result (`success`/`message`/`output`/`spend`). | **Met** | `goblin/phase.py::_run_acp`; integration tests `test_acp_phase_runs_real_opencode` and `test_acp_phase_uses_forwarded_mcp_server` pass against recorded cassettes. |
| F2 | `harness` declarable in `defaults` and per phase; phase shallow-merges over defaults; omitted/`openhands` = today's path. | **Met** | `goblin/blueprint.py::resolve_harness_config`; `tests/unit/test_harness_schema_resolution.py`. |
| F3 | `server` selects `claude-code`/`codex`/`gemini-cli`/`custom`. | **Met** | `spec/v1.json` enum; `goblin/phase.py::_run_acp` maps to `ACPAgentSettings.acp_server`. |
| F4 | `harness.working_dir` scopes ACP session `cwd`; multi-repo/concurrent support. | **Met** | `goblin/phase.py::_run_acp` resolves fallback chain; `tests/unit/test_harness_execution.py::TestACPConcurrency`. |
| F5 | Blueprint `mcp_servers` forwarded to ACP session. | **Met** | `goblin/blueprint.py::build_mcp_servers` passed into `ACPAgentSettings.mcp_config`; integration test passes. |
| F6 | `harness.secrets` routed via conversation secrets channel and masked. | **Met** | `goblin/phase.py::_run_acp` passes `secrets=` to `run_conversation`; provider keys are also forwarded as secrets for masking. |
| F7 | `max_cost`/`max_tokens` circuit breakers apply to ACP usage. | **Met** | `goblin/cost_control.py::CircuitBreaker.absorb`; unit tests `TestACPCircuitBreaker`. |
| F8 | `output_model`/`output_schema` produce validated structured results via coercion. | **Met** | `goblin/phase.py::_run_acp` calls `_coerce_structured_output`; unit tests cover direct coercion, LLM repair, fallback, and schema validation. |
| F9 | `goblin validate` rejects incoherent ACP combinations. | **Met** | `goblin/validation.py::_validate_acp_semantics` implements rules 1–4 and 6–8 with severity; CLI exits non-zero only on errors. |
| F10 | `foreach`/`condition`/`depends_on`/`history`/`on_failure` work identically. | **Met** | Orchestrator-level, unchanged; unit tests cover `foreach` and concurrent phases with distinct `working_dir`s. |

### 1.2 Non-Functional / Step-Level Requirements

| Step | Requirement | Status | Evidence |
|---|---|---|---|
| Step 1 | Spike findings recorded; `harness` schema + resolution plumbing landed. | **Met** | `spec/v1.json` definitions/harness and phase refs; `goblin/blueprint.py::resolve_harness_config`; `tests/unit/test_harness_schema_resolution.py`. |
| Step 2 | `PromptPhase` ACP execution branch with `working_dir`, secrets, result extraction, cleanup. | **Met** | `goblin/phase.py::_run_acp`; `tests/unit/test_harness_execution.py`. |
| Step 3 | Structured-output coercion + cost control. | **Met** | Shared `_coerce_structured_output`, `_assemble_result`, `_validate_output_schema`; breaker tests pass. |
| Step 4 | MCP forwarding, multi-repo example, concurrency guidance. | **Met** | `examples/acp_example.yaml`; `docs/configuration/harness.md`; concurrent-wave test. |
| Step 5 | Semantic validation, docs, examples, AGENTS.md update. | **Met** | `goblin/validation.py::_validate_acp_semantics`; `docs/configuration/harness.md`, `docs/phases/prompt.md`, `docs/phases/load-project.md`, `docs/configuration/environment-variables.md`; `AGENTS.md` project-capabilities note. |

---

## 2. Code Quality Findings

### 2.1 Strengths

- **Clean seam choice.** The implementation reuses `openhands.sdk.ACPAgentSettings.create_agent()` and `run_conversation()` exactly as the PRD recommended, keeping goblin-side surface small.
- **Good separation of concerns.** Config resolution lives in `blueprint.py`, execution in `phase.py`, semantic rules in `validation.py`, and docs/examples are complete.
- **Comprehensive unit tests.** The harness tests mock at the project boundary (`ACPAgentSettings` and `run_conversation`) and cover branch selection, settings mapping, fallback chains, secrets, MCP merging, failure paths, cleanup, structured output, circuit breakers, and concurrency.
- **Defensive cleanup.** `conversation.close()` is called in a `finally` block, helping prevent orphaned subprocesses on success, `LimitExceeded`, or unexpected exceptions.
- **Documentation is complete.** The new `docs/configuration/harness.md` covers the field reference, provider registry, credentials, session semantics, permission posture, MCP ownership, multi-repo execution, concurrency rules, workspace matrix, troubleshooting, and field-compatibility table.
- **Validation severity model.** Introducing `severity` on findings lets warnings surface without failing `goblin validate`, which is the right UX for the alpha "fail fast on errors, warn on grey areas" policy.

### 2.2 Issues and Observations

#### A. Monkeypatch of `ACPAgent._do_acp_prompt` (`goblin/phase.py`)

The implementation includes a global monkeypatch `_patch_acp_prompt_call()` that rewrites `openhands.sdk.agent.acp_agent.ACPAgent._do_acp_prompt` to pass keyword arguments to `_conn.prompt(...)`. This was apparently needed because openhands-sdk 1.29.3 and the vendored `acp` package disagree on positional argument order.

- **Risk:** This is undocumented in the PRD and fragile on SDK upgrades. It couples goblin to internal SDK method signatures.
- **Recommendation:** Add a code comment explaining the exact SDK/acp version mismatch, the upstream issue/PR to watch, and a plan to remove the patch once the SDK ships a fix. Consider gating the patch behind a version check so it fails loudly rather than silently if the signature changes.

#### B. `max_iterations` behavior under ACP is inconsistent with PRD wording

The PRD states that `max_iterations` is "meaningless" under ACP and should only trigger a validation warning. However, `goblin/cost_control.py::SpendTracker.absorb` increments `iterations` once per `arun()` call, and `CircuitBreaker.check` enforces the limit after absorb. The integration test `test_acp_phase_max_iterations_breaker_trips` deliberately uses `max_iterations: 1` to trip a breaker.

- **Risk:** Users who set a low `max_iterations` on an ACP phase will see a circuit-breaker failure even though the docs/validation tell them the field is ignored. This is confusing.
- **Recommendation:** Either (1) skip the `max_iterations` check in `CircuitBreaker` for ACP phases, or (2) update the PRD/docs/validation message to say `max_iterations` still limits the number of `arun()` executions (relevant for `foreach`/retries) rather than being "meaningless." Option (1) aligns better with the PRD intent.

#### C. JSON schema formatting inconsistency (`spec/v1.json`)

Lines 553–558 in `spec/v1.json` indent `worktree`, `harness`, and `model` with extra whitespace relative to the surrounding phase properties:

```json
            "worktree": { "$ref": "#/definitions/worktree" },
            "harness": { "$ref": "#/definitions/harness" },
            "model": {
              "type": "string",
          "description": ...
```

- **Risk:** The file is valid JSON and passes validation, but the inconsistent formatting is a readability/maintainability issue.
- **Recommendation:** Re-indent the block to match the surrounding properties.

#### D. Typo in validation message (`goblin/validation.py`)

Line 299 says `"OpenHooks tool hooks do not fire inside an ACP harness"` instead of `"OpenHands tool hooks ..."`.

- **Recommendation:** Fix the typo.

#### E. Comment indentation in integration test (`goblin/tests/integration/test_acp_harness.py`)

Line 43 is a comment inside a `with` block but is indented less than the statement it annotates:

```python
    with use_cassette(cassette_path, record_mode="once"):
    # Override working_dir so the harness sees our temp repo.
        bp._workspace_config = {"type": "local", "working_dir": str(tmp_path)}
```

- **Recommendation:** Indent the comment to match the block body.

#### F. Provider key auto-injection into `secrets`

`_run_acp` reads the provider's API key from `os.environ` and injects it into the `secrets` dict passed to `run_conversation`. This diverges slightly from the PRD, which says ambient env is inherited and `harness.secrets` overrides it. The implementation does this for masking purposes, which is reasonable, but it means the provider key always travels through the secret registry even when the user only set it in the shell.

- **Risk:** Low; the behavior is still correct (registry wins over ambient env) and improves output masking. It is just not what the PRD literally described.
- **Recommendation:** Document the behavior in `docs/configuration/harness.md` or a code comment so future maintainers understand why the auto-injection exists.

#### G. Structured-output repair injects ACP provider key as the coercion LLM key

When an ACP phase declares `output_model` and the direct coerce fails, `_run_acp` injects the ACP provider's API key into the goblin-side LLM config used for repair. This is a pragmatic default but conflates the harness provider with the coercion LLM provider. For example, a Codex harness may repair via OpenAI even if the user intended a different coercion model.

- **Risk:** Users may be surprised that their ACP provider key is used for the repair LLM without explicit opt-in.
- **Recommendation:** Document the fallback clearly and consider requiring an explicit `model`/`llm` config for ACP structured-output repair rather than silently reusing the harness key.

### 2.3 Suggestions (Not Blockers)

- **Schema conditional validation.** JSON Schema draft-07 cannot express "`server: custom` requires `command`," so the semantic pass handles it. Consider upgrading to draft-2019-09/`if-then` in a future cleanup if the project is comfortable requiring a newer JSON Schema validator.
- **Integration test cassettes.** The three `.b64` cassettes are large single-line blobs. They pass the secret-check script, but reviewing diffs is hard. Consider a policy to pretty-print or split cassettes where the VCR helper supports it.
- **E2E gate.** The PRD originally imagined live provider e2e tests gated by credentials. The current integration suite uses a custom `opencode acp` command and VCR cassettes. This is a good deterministic compromise, but the PRD's credential-gated provider matrix is not present. If live provider coverage is still desired, it should be added as a separate, explicitly gated suite.

---

## 3. Test Results

| Suite | Command | Result |
|---|---|---|
| Lint | `task lint` | Passed (ruff + import-time check) |
| Typecheck | `task typecheck` | Passed — 0 errors (goblin: 1 suppressed, 3 warnings; goblin-common: 8 warnings) |
| Unit tests | `task goblin:test` | **751 passed** |
| Validation | `task validate` | `examples/acp_example.yaml` valid; cassettes clean |
| Integration tests | `uv run pytest tests/integration/test_acp_harness.py` | **3 passed** |

No regressions were observed in the existing OpenHands path; existing unit tests were untouched and green.

---

## 4. Conclusion

PRD 1 has been delivered successfully. The ACP harness feature is complete against the stated success criteria, well-tested, and well-documented. The codebase remains green across lint, typecheck, unit tests, and validation.

The remaining work is polish:

1. Document or remove the `ACPAgent._do_acp_prompt` monkeypatch.
2. Resolve the `max_iterations` semantic mismatch under ACP.
3. Fix formatting in `spec/v1.json`, the typo in `validation.py`, and the comment indentation in the integration test.
4. Clarify provider-key behavior for the ACP harness and structured-output repair.

None of these are functional blockers, but addressing them will improve maintainability and reduce user confusion.
