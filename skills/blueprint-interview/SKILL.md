---
name: blueprint-interview
description: Grill the user relentlessly about a plan to create a G.O.B.L.I.N. Blueprint. Clarify exactly how the user wants to do the work and what tools they want to use.
disable-model-invocation: true
---

Interview me relentlessly about every aspect of this until we reach a shared understanding. Walk down each branch of the decision tree, resolving dependencies between decisions one-by-one. For each question, provide your recommended answer.

Ask the questions one at a time, waiting for feedback on each question before continuing. Asking multiple questions at once is bewildering.

If a fact can be found by exploring the environment (filesystem, tools, etc.), look it up rather than asking me. The decisions, though, are mine — put each one to me and wait for my answer.

Do not act on it until I confirm we have reached a shared understanding.

## Reference material

This skill bundles a snapshot of the Goblin docs under `references/` (synced from the repo by `task skills:sync-references`). Consult it instead of guessing at blueprint syntax. Key pages:

| When you need... | Read |
| --- | --- |
| Blueprint keys, `defaults`, precedence | `references/docs/blueprints/structure.md` |
| `{vars}`, `$ENV`, Jinja2 in `condition`/`foreach` | `references/docs/blueprints/vars-and-interpolation.md` |
| All phase types and the execution model | `references/docs/phases/overview.md` |
| Prompt phase fields (`history`, `output_schema`, budgets) | `references/docs/phases/prompt.md` |
| Project loading (CLAUDE.md, hooks, agents) | `references/docs/phases/load-project.md` |
| Tool names, mapping, deduplication | `references/docs/tools/overview.md` |
| Built-in tools and their required config | `references/docs/tools/built-in.md` |
| MCP servers and `mcp__server__tool` naming | `references/docs/tools/mcp-servers.md` |
| Failure handling | `references/docs/advanced/on-failure.md` |
| DAGs and concurrency | `references/docs/advanced/depends-on.md` |
| Swarms | `references/docs/phases/swarm.md`, then `references/docs/swarm/` |
| Workspace (local/remote) | `references/docs/configuration/workspace.md` |
| Full JSON Schema | `references/spec/v1.json` |

## Interview plan

Work through the phases in order. Each lists the decisions to extract. If an earlier answer settles a later decision, confirm the implication rather than re-asking it.

### Phase 0 — The job

- What should the blueprint accomplish, in one sentence? (→ `blueprint.name` and overall goal)
- What does success look like — what artifact exists when it finishes (a commit, a PR, a report, a deployed service)?
- What is the starting state: an existing repo to clone, a local directory, a remote service, nothing?
- How will it be invoked: fixed inputs (→ `vars`) or per-run inputs (→ `--context`)?

### Phase 1 — Decomposition into phases

Walk the job step by step. For each step, decide:

- Does it need an LLM, or is it pure setup? (→ `prompt` vs `load_project`)
- Is it one agent or a team? (→ `prompt` vs `swarm`) Push back on swarms unless the work genuinely parallelises.
- Order: strictly sequential, or can steps overlap? (→ `depends_on`)
- Repetition: fixed count, per line of a file, or per item from a prior phase's output? (→ `foreach`)
- Conditionality: should it run only when a prior phase succeeded/failed/produced something? (→ `condition`)

Recommended default: a small number of sequential `prompt` phases. Add structure only when my answers demand it.

### Phase 2 — Data flow

- Which values does the blueprint need? Split them into fixed (`vars`), per-run (`--context`), and secrets (`$ENV` references — never literals).
- What must each phase know from earlier phases? (→ `history`)
- Does any phase's output feed a later `condition` or `foreach`? Then that phase needs an `output_schema` — pin down the exact fields and types.

### Phase 3 — Tool audit

See "The tool audit" below. This is the longest phase; do not rush it.

### Phase 4 — Execution environment

- Where does the work happen: current directory, a fresh clone, a remote agent server? (→ `workspace`)
- Is git isolation needed per run? (→ `Worktree` tool + `worktree:` config: `base_dir`, `deps`, `auto_link`)
- Which model? Any per-phase overrides? (→ `defaults.model` / `llm`)
- Budgets: `max_iterations`, `max_tokens`, `max_cost`. Recommend setting these for anything that runs unattended.

### Phase 5 — Failure handling and humans

- For each phase: on failure, skip, retry, or abort? How many retries? (→ `on_failure`)
- Should a human approve anything mid-run — deploys, deletions, anything that spends money? (→ handle via `condition`/`on_failure` or external orchestration, not a built-in tool)
- Any pre/post-tool hooks? (→ `hooks`)

### Phase 6 — Playback and confirmation

Assemble everything into the Blueprint Spec (below) and play it back to me. Do not write YAML until I confirm. After confirmation, author the blueprint and run `goblin validate <file>` on it.

## The tool audit

`allowed_tools` is a whitelist: anything not listed is unavailable to the phase. Never ask "which tools do you want?" — ask what the phase must *touch*, derive the smallest tool set from the answer, then confirm the derivation with me.

For each phase, ask:

1. **Does it only read and search files?** → `Read, Glob, Grep`
2. **Does it create or edit files?** → add `Write`, `Edit`
3. **Must it run shell commands** (build, test, git, package managers, curl)? → `Bash`. Flag this every time — it is arbitrary command execution — and ask whether the commands are known in advance (then bake them into the prompt) or open-ended (then interrogate the risk).
4. **Does it need isolated copies of a repo?** → `Worktree`, which requires a `worktree:` config block.
5. **Does it call an external service** (GitHub, a database, ticketing, a cloud API)? → an MCP server. Capture exactly:
   - server name (short; it becomes the `mcp__<name>__` prefix)
   - transport: stdio (`command`, `args`, `env`, `cwd`) or remote (`transport`, `url`, `headers`)
   - credentials as `$ENV_VAR` references — check the environment for which already exist
   - the exact tool names to whitelist, or `mcp__<name>__*` only with my explicit approval
6. **Must a human answer something mid-run?** → handle via `condition`/`on_failure` or external orchestration; there is no built-in human-in-the-loop tool.
7. **Is it a swarm?** → tools per agent, plus `SwarmTasks` / `SwarmAgent` where needed.

Record the result as a table and confirm it before moving on:

| Phase | Work | Touches | `allowed_tools` | Extra config required |
| --- | --- | --- | --- | --- |

Audit rules:

- Least privilege: every tool needs a justification from the work itself, never "just in case".
- A phase that only transforms prior phases' output needs `allowed_tools: []`.
- Tools with required config (`Worktree` → `worktree:`, MCP → `mcp_servers:`) are two decisions, not one — settle both before moving on.
- Look up facts before asking: the filesystem for the target project, `.env` for tokens, the repo for existing MCP usage.

## Blueprint Spec (the confirmation artifact)

Before authoring anything, present:

1. **Goal** and success criteria.
2. **Phases** — id, type, one-line purpose, order and dependencies.
3. **Vars and secrets** — names, sources, which are per-run.
4. **Tool table** from the tool audit.
5. **Failure policy** per phase.
6. **Open questions** — anything I deferred or waived.

Then stop and wait for my confirmation.
