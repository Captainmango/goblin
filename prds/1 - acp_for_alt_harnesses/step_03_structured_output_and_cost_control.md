# Step 3 — Structured outputs + cost control for ACP phases

**Goal:** Make `output_model` / `output_schema` work on ACP phases via goblin-side coercion,
and verify the circuit breakers (`max_cost`, `max_tokens`) against the SDK's ACP usage
accounting.

**Depends on:** step 2. **Blocks:** step 5 (validation must know the final rules).

---

## 3.1 Why a coercion path (recap from README §5.5)

The OpenHands structured-output machinery is in-loop: a typed Finish tool replaces the
agent's finish call, and a `response_format` follow-up repairs misses [L1]. Under ACP the
harness owns its LLM loop and its finish semantics — goblin cannot inject tools into it.
What goblin *can* do is coerce the harness's final text after the turn ends.

## 3.2 Implementation in `_run_acp`

Replace step 2's deferred-warning branch with the coercion chain:

```python
text = get_agent_final_response(conversation.state.events) or ""
if structured_configured:
    output_model = self._resolve_output_model()
    structured = coerce_to_model(text, output_model)                       # 1. cheap parse
    if structured is None:
        structured = await self._request_structured_output(                # 2. LLM repair
            prompt, output_model, llm_config=llm_config
        )
    if structured is None:
        structured = self._fallback_result(text, output_model)             # 3. synthetic fail
else:
    structured = self._wrap_chat_text(text)
```

Notes:

- All three stages already exist on `PromptPhase` [L1] — this step wires them into
  `_run_acp` and extracts the shared tail (structured post-processing + result dict
  assembly, including extra-field surfacing for Jinja) so the OpenHands and ACP paths share
  one implementation rather than diverging.
- `llm_config` comes from `self._blueprint._resolve_llm_config(self, context)` as today.
  **Document the redefinition:** for ACP phases, `model`/`llm` (phase or defaults) configure
  the goblin-side coercion LLM and cost attribution, *not* the harness's model — that's
  `harness.model`. If no coercion LLM is configured and the direct coerce fails, the
  synthetic-failure fallback still returns a well-formed phase result.
- `output_schema` (inline JSON Schema) validation runs unchanged against the coerced dump,
  including the failure path that returns `success: False` with schema errors [L1].
- The typed Finish tool spec, `build_system_prompt(output_model)`, and
  `filter_tools_regex` must **not** be built for ACP agents — they are OpenHands-agent
  constructor concepts. The ACP branch never touches them.

## 3.3 Circuit breakers against ACP usage

The spike (step 1) confirmed `ACPAgent` converts `UsageUpdate` notifications into the
attribution LLM's metrics, which `conversation_stats.get_combined_metrics()` aggregates —
exactly what `SpendTracker.absorb()` reads [L7, L8]. This step hardens that into contract:

1. **Unit test** with a mocked conversation stats object shaped like the SDK's ACP output:
   `breaker.absorb(conversation)` accumulates tokens and cost; `max_cost`/`max_tokens`
   produce `LimitExceeded` with the expected `reason`.
2. **E2E verification (gated, same flag as step 2):** run a tiny ACP phase with a low
   `max_cost` and assert the phase fails with `Circuit breaker: max_cost limit exceeded` —
   proving the wiring end-to-end per available provider.
3. **Gap policy:** if a provider proves to emit no usage data (spike finding), document
   `max_cost`/`max_tokens` as best-effort for that provider in
   `docs/configuration/harness.md`, and rely on `harness.prompt_timeout` as the hard guard.
   Record the per-provider outcome in step 1's spike notes and link them here.
4. `max_iterations` stays meaningless under ACP (one prompt turn per phase execution); the
   warning is a step 5 validation rule — no runtime code here.

## 3.4 Tests

- Coercion: harness text that is already valid JSON → model populated without an LLM call
  (assert `_request_structured_output` not awaited).
- Repair path: prose answer → follow-up LLM (mocked) returns the structured payload →
  validated model returned.
- Total failure: both stages fail → synthetic `success: False` result, phase fails cleanly.
- `output_schema` errors surface as the phase's `message` list with `success: False`.
- Extra model fields flow into the result dict for `steps.<id>.<field>` access.
- Circuit breaker unit + gated e2e as in §3.3.
- Regression: OpenHands-path structured output tests still pass unchanged after the shared
  tail extraction.

## Acceptance criteria

- [ ] ACP phases with `output_model` return validated structured results (all three chain
      stages covered by tests).
- [ ] `max_cost` breaker trips on a real ACP phase in the gated e2e test for at least one
      provider.
- [ ] Shared result-assembly tail has no behavior delta for OpenHands phases (existing
      tests green, unmodified).
- [ ] `task goblin:lint goblin:typecheck goblin:test` green.
