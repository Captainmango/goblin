# Implementation Plan: Step Conversation History

## Overview

Allow blueprint steps to receive prior steps' full text output as context injected into their prompt via a new `history` field.

---

## Step 1: Update the JSON Schema (`spec/v1.json`)

**File:** `spec/v1.json`

Add `history` to the `step` definition's `properties` object (alongside `prompt`, `condition`, etc.):

```json
"history": {
  "description": "List of prior step IDs whose prompt and text output should be included as context in this step's prompt. Context is prepended in order before the step's own prompt. Referenced steps must be prompt steps defined before this step. Cannot reference foreach steps.",
  "type": "array",
  "items": { "type": "string" },
  "examples": [["step_one"], ["analyze", "plan"]]
}
```

**Acceptance criteria:**
- `history` is an optional array of strings on the step object
- Blueprint validation still passes for steps without `history`
- Blueprint validation accepts steps with a valid `history` array

---

## Step 2: Parse `history` in `PromptStep.__init__()` and add validation

**File:** `goblin/step.py`

In `PromptStep.__init__`, add:

```python
self.history: list[str] | None = raw.get("history")
```

**File:** `goblin/blueprint.py`

Add a `_validate_history` method to `V1Blueprint` and call it from `__init__` after steps are parsed. This performs parse-time validation of all history references:

```python
def _validate_history(self):
    """Validate history references at parse time."""
    step_ids_seen: list[str] = []
    foreach_step_ids: set[str] = set()

    for step in self.steps:
        history = getattr(step, "history", None)
        if history:
            # history is only valid on prompt steps
            if not isinstance(step, PromptStep):
                raise ValueError(
                    f"Step '{step.id}': history is only valid on prompt steps"
                )
            for ref_id in history:
                # Must reference a previously defined step
                if ref_id not in step_ids_seen:
                    raise ValueError(
                        f"Step '{step.id}': history references '{ref_id}' "
                        f"which is not defined before this step"
                    )
                # Cannot reference foreach steps
                if ref_id in foreach_step_ids:
                    raise ValueError(
                        f"Step '{step.id}': history cannot reference foreach "
                        f"step '{ref_id}'"
                    )
                # Cannot reference non-prompt steps
                referenced = next(s for s in self.steps if s.id == ref_id)
                if not isinstance(referenced, PromptStep):
                    raise ValueError(
                        f"Step '{step.id}': history can only reference prompt "
                        f"steps, but '{ref_id}' is a {type(referenced).__name__}"
                    )

        step_ids_seen.append(step.id)
        if step.foreach:
            foreach_step_ids.add(step.id)
```

Call in `__init__` after `self.steps` is built:

```python
self._validate_history()
```

**Acceptance criteria:**
- `PromptStep` stores `history` as `list[str] | None`
- Validation raises `ValueError` if history references an undefined step
- Validation raises `ValueError` if history references a step defined *after* the current step
- Validation raises `ValueError` if history references a foreach step
- Validation raises `ValueError` if history references a non-prompt step (load_project, swarm)
- Validation raises `ValueError` if history is used on a non-prompt step

---

## Step 3: Capture full output and resolved prompt in `PromptStep.run()`

**File:** `goblin/step.py`

Modify `PromptStep.run()` to:

1. Store the resolved (interpolated) prompt
2. Capture all text blocks into a single `output` string
3. Return `prompt` and `output` alongside the existing `success`/`message` fields

Changes to `run()`:

```python
async def run(self, context: dict) -> Dict:
    prompt = self.prompt_template.format(**context)

    # Prepend history context if provided by the blueprint
    history_context = context.get("_history_context", "")
    if history_context:
        prompt = history_context + prompt

    # ... existing kwargs/options setup (unchanged) ...

    text_blocks: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    text_blocks.append(block.text)

    full_output = "\n".join(text_blocks)

    if not text_blocks:
        return {
            "success": False,
            "message": ["Agent produced no text output"],
            "prompt": prompt,
            "output": "",
        }

    parsed = await self._parse_response(text_blocks[-1])
    return {**parsed, "prompt": prompt, "output": full_output}
```

**Acceptance criteria:**
- Every prompt step result now includes `"prompt"` (the interpolated prompt sent to the agent)
- Every prompt step result now includes `"output"` (all text blocks joined with newlines)
- Existing `success` and `message` fields are unchanged
- Failed steps (no text output) still include `prompt` and `output` (empty string)

---

## Step 4: Build history context in `V1Blueprint`

**File:** `goblin/blueprint.py`

Add a `_build_history_context` method:

```python
def _build_history_context(self, history_ids: list[str], results: dict) -> str:
    """Build a context string from prior steps' prompts and outputs."""
    sections = []
    for step_id in history_ids:
        if step_id not in results:
            continue  # Step was skipped (condition not met)
        step_result = results[step_id]
        step_prompt = step_result.get("prompt", "")
        output = step_result.get("output", "")
        if not output and not step_prompt:
            continue
        section = f"### Step: {step_id}\n"
        if step_prompt:
            section += f"\n**Prompt:** {step_prompt}\n"
        if output:
            section += f"\n**Output:**\n{output}\n"
        sections.append(section)

    if not sections:
        return ""

    return "## Prior Step Results\n\n" + "\n".join(sections) + "\n---\n\n"
```

**Acceptance criteria:**
- Returns empty string when no referenced steps have results (all skipped)
- Formats each referenced step with its prompt and output
- Preserves ordering from the `history_ids` list
- Silently omits steps that were skipped or have no data

---

## Step 5: Inject history context into the step execution loop

**File:** `goblin/blueprint.py`

Modify the `run()` method's step execution loop. Before calling `step.run(context)`, check if the step has `history` and build the context:

In the **non-foreach branch** (currently line ~329):

```python
# Before:
result = await step.run(context)

# After:
step_context = context
if hasattr(step, "history") and step.history:
    history_context = self._build_history_context(step.history, results)
    step_context = {**context, "_history_context": history_context}
result = await step.run(step_context)
```

In the **foreach branch** (currently line ~314-315):

```python
# Before:
step_context = {**context, **item}
result = await step.run(step_context)

# After:
step_context = {**context, **item}
if hasattr(step, "history") and step.history:
    history_context = self._build_history_context(step.history, results)
    step_context = {**step_context, "_history_context": history_context}
result = await step.run(step_context)
```

**Acceptance criteria:**
- Steps without `history` behave identically to today
- Steps with `history` receive `_history_context` in their context dict
- Foreach steps with `history` give every iteration the same history context
- The `_history_context` key does not leak into `results`

---

## Step 6: Write tests

**File:** `tests/test_history.py`

### Test cases for `_validate_history`:

1. **Valid history reference** — step B references step A (defined before it). No error.
2. **Reference to undefined step** — raises `ValueError`.
3. **Reference to later step** — step A references step B (defined after it). Raises `ValueError`.
4. **Reference to foreach step** — raises `ValueError`.
5. **Reference to load_project step** — raises `ValueError`.
6. **History on non-prompt step** — raises `ValueError`.
7. **Foreach step with history is allowed** — step with both `foreach` and `history` parses without error.

### Test cases for `_build_history_context`:

8. **Single step reference** — returns formatted section with prompt and output.
9. **Multiple step references** — returns sections in order.
10. **Skipped step** — referenced step not in results, silently omitted.
11. **Step with empty output** — only prompt shown if output is empty.
12. **No valid results** — returns empty string.

### Test cases for `run()` integration (using mock steps):

13. **History context prepended to prompt** — mock step A returns with `prompt` and `output` fields. Step B has `history: [step_a]`. Verify step B's `run()` receives `_history_context` in context.
14. **Output and prompt captured in results** — verify step results contain `prompt` and `output` fields.
15. **Foreach + history** — foreach step with history, each iteration gets the same `_history_context`.

Use `_make_blueprint` for validation tests and `_make_blueprint_with_mock_steps` for integration tests per the testing rules.

**Acceptance criteria:**
- All tests pass
- Tests do not import or call the Claude Agent SDK
- Tests use `_make_blueprint` / `_make_blueprint_with_mock_steps` helpers

---

## Step 7: Lint, format, typecheck

Run `task lint`, `task format`, `task typecheck` and fix any issues.

---

## File Change Summary

| File | Change |
|------|--------|
| `spec/v1.json` | Add `history` property to step schema |
| `goblin/step.py` | Parse `history` in `PromptStep.__init__`. Modify `run()` to prepend `_history_context` and return `prompt`+`output` fields. |
| `goblin/blueprint.py` | Add `_validate_history()` (called from `__init__`). Add `_build_history_context()`. Update `run()` loop to inject history context. |
| `tests/test_history.py` | New file — validation, context building, and integration tests |

## Order of Operations

Steps 1-3 can be done in parallel (schema, parsing, output capture are independent). Step 4 depends on nothing. Step 5 depends on steps 2-4. Step 6 can be written alongside any step but should be run after step 5. Step 7 is always last.
