# PRD: Step Conversation History

## Problem

Blueprint steps are fully isolated — each step launches a fresh agent session with no awareness of prior steps' responses. This means:

1. A step cannot build on the output of a previous step beyond what's captured in the structured `{success, message}` result.
2. Blueprint authors who want multi-step reasoning chains must manually embed prior results into prompts via `{variable}` interpolation or Jinja conditions, which is brittle and lossy.
3. The full text output from a step (reasoning, analysis, summaries) is discarded — only the final JSON result is kept.

## Goals

- Allow a step to receive prior steps' text output as context injected into its prompt.
- Keep the default behavior unchanged — steps remain isolated unless explicitly opted in.
- Support selective history inclusion (cherry-pick which prior steps to include).
- Remain an orchestrator, not a chat application — history is prompt context, not session continuity.

## Non-Goals

- Session continuity or shared Claude Code sessions across steps. Each step is its own agent run.
- Replaying tool calls or making the agent believe it performed prior steps' work.
- Real-time inter-step communication.
- Conversation branching or tree structures.
- Output size limits or summarization (defer to future work if context limits become a problem in practice).

## Current Architecture

| Component | Behavior |
|---|---|
| `V1Blueprint.run()` | Iterates steps sequentially. Stores `results[step.id] = {success, message}` after each step. Results feed conditions and foreach only. |
| `PromptStep.run()` | Calls `query(prompt=str, options=...)` — a stateless, one-shot SDK call. Collects `AssistantMessage` text blocks, parses the last one as JSON, discards the rest. |
| `query()` (SDK) | Stateless. Each call is independent. Accepts `prompt: str` or `AsyncIterable[dict]`. |
| Step results | `{"success": bool, "message": list[str]}` — only the parsed JSON. The full agent text output is lost. |

### What gets lost today

`PromptStep.run()` collects all `TextBlock.text` from assistant messages into `text_blocks`, but only uses `text_blocks[-1]` for JSON parsing. Everything else — the agent's reasoning, intermediate analysis, summaries — is discarded.

### Why not use the SDK's `AsyncIterable[dict]` prompt format?

The SDK's `query()` accepts `prompt: AsyncIterable[dict]` for streaming multiple user messages into a single CLI session. This was considered as a mechanism for history injection, but it doesn't fit:

1. **Messages execute.** Each user message in the stream triggers a full agent turn — tool calls, reasoning, the works. Sending a prior step's prompt as message 1 would re-execute that step's work, paying full cost and time for work already done, with potentially different results.
2. **Single set of options.** All messages in a stream share the same `ClaudeAgentOptions` — same `allowed_tools`, `hooks`, `system_prompt`. Steps with different tool permissions can't be combined.
3. **User messages only.** The format only supports `"type": "user"`. There's no way to inject a synthetic assistant response — you can only send user prompts and let the agent respond to each.
4. **Unidirectional.** All messages are sent upfront, then all responses come back. You can't gate later messages on earlier results.

The `AsyncIterable` is designed for multi-turn conversations where all user inputs are known ahead of time, not for replaying prior session outputs. Prompt-level context injection is the right approach for an orchestrator.

## Design

### Core Idea

Capture the full text output from each step. When a subsequent step declares `history`, format the referenced steps' prompts and outputs and inject them into the prompt as context. The agent reads prior work as background information, not as a conversation it participated in.

### Blueprint Schema

Add an optional `history` field to prompt steps:

```yaml
steps:
  - id: analyze_code
    prompt: "Analyze the codebase and summarize the key modules."
    allowed_tools: [Read, Glob, Grep]

  - id: write_tests
    prompt: "Write unit tests for the modules described above."
    allowed_tools: [Read, Write, Bash]
    history:
      - analyze_code
```

#### Schema Addition (`spec/v1.json`)

```json
{
  "history": {
    "description": "List of prior step IDs whose prompt and text output should be included as context in this step's prompt. Context is prepended in order before the step's own prompt. Cannot reference foreach steps.",
    "type": "array",
    "items": {
      "type": "string"
    },
    "examples": [["step_one"], ["analyze", "plan"]]
  }
}
```

### What Gets Captured

Change `PromptStep.run()` to store the full text output alongside the parsed result:

```python
# Current: only stores parsed JSON
results[step.id] = {"success": True, "message": [...]}

# Proposed: also stores raw text output and the resolved prompt
results[step.id] = {
    "success": True,
    "message": [...],
    "prompt": "the interpolated prompt that was sent to the agent",
    "output": "full concatenated text from all assistant text blocks"
}
```

The `output` field captures everything the agent said — reasoning, analysis, tool call narratives, and the final JSON response. The `prompt` field captures the interpolated prompt that produced the output. Together, these are the raw material that `history` injects into subsequent prompts.

### How History Enters the Prompt

When a step has `history: [step_a, step_b]`, the blueprint constructs a context block from those steps' `prompt` and `output` fields and **prepends** it to the step's prompt:

```
## Prior Step Results

### Step: analyze_code

**Prompt:** Analyze the codebase and summarize the key modules.

**Output:**
<full text output from analyze_code>

### Step: plan_changes

**Prompt:** Based on the analysis, create a plan for the refactor.

**Output:**
<full text output from plan_changes>

---

<the step's own prompt>
```

This is straightforward string concatenation — no SDK changes, no message format hacking, no session management. The agent sees the prior output as part of its instructions.

#### Why prepend, not a template variable?

Both options are viable:

| Approach | Pros | Cons |
|---|---|---|
| **Auto-prepend** | Zero friction — just list step IDs. Works without modifying existing prompts. | Author can't control placement or framing. |
| **Template variable `{history}`** | Full control over where history appears and how it's framed. | Requires the author to remember to include `{history}` — easy to forget, silent failure if omitted. |

**Decision: auto-prepend.** It's simpler, less error-prone, and matches the orchestrator philosophy — the blueprint declares dependencies, the runner handles the wiring. If a future need arises for fine-grained placement, a `{history}` variable can be added without breaking the prepend behavior.

### Data Flow

```
V1Blueprint.run()
  |
  +- step: analyze_code (no history)
  |   +- query(prompt="Analyze the codebase...")
  |   +- Capture all text blocks -> results["analyze_code"]["output"]
  |   +- Store resolved prompt -> results["analyze_code"]["prompt"]
  |   +- Parse last block as JSON -> results["analyze_code"]["success"], ["message"]
  |
  +- step: write_tests (history: [analyze_code])
  |   +- Build history context from results["analyze_code"]["prompt"] + ["output"]
  |   +- Prepend to prompt: history_context + "\n---\n" + step prompt
  |   +- query(prompt=augmented_prompt)
  |   +- Capture output, parse result as usual
  |
  +- step: deploy (no history)
      +- query(prompt="Deploy...") -- no change, fully isolated
```

### Implementation

#### 1. Capture full output and prompt in `PromptStep.run()` (`goblin/step.py`)

```python
async def run(self, context: dict) -> Dict:
    prompt = self.prompt_template.format(**context)

    # Prepend history context if provided
    history_context = context.get("_history_context", "")
    if history_context:
        prompt = history_context + prompt

    # ... build options ...

    text_blocks: list[str] = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if hasattr(block, "text"):
                    text_blocks.append(block.text)

    if not text_blocks:
        return {
            "success": False,
            "message": ["Agent produced no text output"],
            "prompt": prompt,
            "output": "",
        }

    full_output = "\n".join(text_blocks)
    parsed = await self._parse_response(text_blocks[-1])
    return {**parsed, "prompt": prompt, "output": full_output}
```

#### 2. Build history context in `V1Blueprint` (`goblin/blueprint.py`)

```python
def _build_history_context(self, history_ids: list[str], results: dict) -> str:
    """Build a context string from prior steps' prompts and outputs."""
    sections = []
    for step_id in history_ids:
        if step_id not in results:
            continue  # Step was skipped or hasn't run
        step_result = results[step_id]
        output = step_result.get("output", "")
        step_prompt = step_result.get("prompt", "")
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

#### 3. Inject history before running the step (`V1Blueprint.run()`)

```python
# In the step execution loop, before step.run(context):
if hasattr(step, 'history') and step.history:
    history_context = self._build_history_context(step.history, results)
    step_context = {**context, "_history_context": history_context}
else:
    step_context = context
result = await step.run(step_context)
```

#### 4. Parse `history` in `PromptStep.__init__()` (`goblin/step.py`)

```python
self.history: list[str] | None = raw.get("history")
```

#### 5. Update schema (`spec/v1.json`)

Add `history` to the prompt step's properties with the schema definition above.

### Changes Required

| File | Change |
|---|---|
| `spec/v1.json` | Add `history` property to prompt step schema |
| `goblin/step.py` | `PromptStep.__init__` parses `history`. `PromptStep.run()` captures full output and prompt, prepends history context. |
| `goblin/blueprint.py` | Add `_build_history_context()`. Update `run()` loop to pass history context when step has `history`. |
| Tests | `tests/test_history.py` — history context building, prompt augmentation, skipped step handling, output capture, foreach+history mutual exclusivity. |

### Validation Rules

- Every ID in `history` must reference a step defined *before* the current step in the `steps` list. Violation raises at parse time.
- Referenced steps must be prompt steps (not `load_project` or `swarm`). `load_project` has no meaningful text output; swarm steps could be supported later.
- **Foreach steps cannot be referenced in `history`.** A step with `foreach` cannot appear in another step's `history` list. This avoids the complexity of presenting N iterations' worth of output and the context size explosion that comes with it.
- A step with `foreach` **can** declare `history` to consume prior context — each iteration receives the same history. This is the permitted direction of the combination.
- If a referenced step was skipped (condition not met), its history entry is silently omitted — the step still runs with whatever history is available.
- `history` is only valid on prompt steps.

### Foreach + History Interaction

These two features interact in one direction only:

| Combination | Allowed | Behavior |
|---|---|---|
| Step with `foreach` declares `history` | Yes | Each iteration receives the same history context from the referenced steps. Iterations are independent — they don't accumulate history from sibling iterations. |
| Step references a `foreach` step in its `history` | No | Raises a validation error at parse time. Foreach steps produce multiple iterations of output and including all of them would explode context size unpredictably. |

This keeps the feature simple while covering the useful case (fan-out with shared context).

### Blueprint Examples

#### Basic: analysis -> action chain

```yaml
workflow:
  name: code_review_and_fix
  steps:
    - id: load_project
      load_project: /path/to/repo

    - id: review
      prompt: >
        Review the codebase for bugs and code quality issues.
        List each issue with file path and line number.
      allowed_tools: [Read, Glob, Grep]

    - id: fix
      prompt: >
        Fix the issues identified in the review. Make minimal, targeted changes.
      allowed_tools: [Read, Write, Edit, Bash]
      history:
        - review

    - id: verify
      prompt: >
        Run the test suite and verify the fixes didn't break anything.
      allowed_tools: [Bash, Read, Edit]
      history:
        - review
        - fix
```

#### Multi-stage reasoning

```yaml
workflow:
  name: architecture_proposal
  steps:
    - id: inventory
      prompt: "Catalog all modules, their dependencies, and public APIs."
      allowed_tools: [Read, Glob, Grep]

    - id: analyze
      prompt: "Identify architectural problems: tight coupling, circular deps, god objects."
      allowed_tools: [Read, Glob, Grep]
      history:
        - inventory

    - id: propose
      prompt: >
        Propose a refactoring plan. For each change, explain the motivation,
        the before/after, and any migration steps needed.
      allowed_tools: [Read, Write]
      history:
        - inventory
        - analyze
```

#### Foreach consuming history

```yaml
workflow:
  name: per_module_tests
  steps:
    - id: analyze
      prompt: "Analyze the codebase and list all modules with their public APIs."
      allowed_tools: [Read, Glob, Grep]

    - id: write_tests
      prompt: "Write tests for the module at {item}."
      allowed_tools: [Read, Write, Bash]
      foreach: modules.txt
      history:
        - analyze
```

Each iteration of `write_tests` sees the full output from `analyze` prepended to its prompt, giving the agent context about the broader codebase while it writes tests for a specific module.

## Success Criteria

- A step with `history: [prior_step_id]` receives the prior step's prompt and full text output prepended to its own prompt.
- The agent can reference specific details from prior output without them being restated in the step's own prompt.
- Steps without `history` behave identically to today (no output capture overhead visible in results beyond the new `output` and `prompt` fields).
- Blueprint validation rejects `history` references to nonexistent or out-of-order step IDs.
- Blueprint validation rejects `history` references to foreach steps.
- A foreach step with `history` works: each iteration gets the same prepended context.
- Full text output and resolved prompt are captured and stored in step results for all prompt steps.
