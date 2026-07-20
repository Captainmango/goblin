# History

The `history` field injects prior phase prompts and outputs into later phases as context.

## Syntax

```yaml
phases:
  - id: analyse
    prompt: "Analyse the codebase architecture"
    allowed_tools: [Read, Glob]

  - id: plan
    prompt: "Create an implementation plan"
    allowed_tools: [Read]
    history: [analyse]

  - id: implement
    prompt: "Implement the plan"
    allowed_tools: [Bash, Read, Write, Edit]
    history: [analyse, plan]
```

## How History Works

When a phase declares `history`, Goblin builds a context string from the referenced phases' prompts and outputs:

```markdown
## Prior Phase Results

### Phase: analyse

**Prompt:** Analyse the codebase architecture

**Output:**
The codebase uses a layered architecture with...

---

### Phase: plan

**Prompt:** Create an implementation plan

**Output:**
1. Add the database migration...
2. Update the API layer...

---
```

This context is prepended to the phase's own prompt before sending it to the agent.

## Restrictions

- Referenced phases must be `prompt` phases defined before the current phase.
- You cannot reference `foreach` phases (because they produce multiple results).
- Phases referenced in `history` do not need to be direct dependencies; they just need to have run before the current phase.

## Use Cases

- **Multi-step reasoning** — Pass analysis results to a planning phase.
- **Code review** — Pass implementation output to a review phase.
- **Debugging** — Pass error output to a remediation phase.
