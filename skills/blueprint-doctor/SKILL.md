---
name: blueprint-doctor
description: Validate a Goblin blueprint against the JSON Schema using the goblin CLI, then review it and recommend improvements. Expects a blueprint path as an argument.
disable-model-invocation: true
---

# Blueprint Doctor

Expected argument: the path to a blueprint YAML file, e.g. `/blueprint-doctor blueprints/deploy.yaml`. If no path was supplied, ask for one and stop. Assume the CLI is installed already. If it isn't, tell the user to install it and stop.

Do not edit the blueprint file. Present findings and proposed fixes, then offer to apply them at the end.

## Reference material

This skill bundles a snapshot of the Goblin docs under `references/`. Consult it instead of guessing at blueprint syntax — especially `references/docs/blueprints/structure.md`, `references/docs/phases/`, and `references/spec/v1.json` when explaining a schema error.

## Step 1 — Syntax validation

Run the CLI validator against the supplied path from the repository root:

```sh
uv run goblin validate <path>
```

(`goblin` is a uv workspace member, so this resolves from the repo root. If the user has goblin installed via pipx, plain `goblin validate <path>` works too — use whichever is available.)

- Exit code 0 (`<path>: valid`) → proceed to Step 2.
- Exit code 1 → report every error verbatim. For each error, explain in plain language what the schema expects and show a minimal YAML snippet that fixes it. Group errors by phase. Then proceed to Step 2 anyway — a schema-invalid blueprint still benefits from the review.

## Step 2 — Review

Read the blueprint in full and work through both checklists. Schema validation only catches shape; most of these checks are things `goblin validate` cannot see.

### Will it run? — runtime failures the schema can't catch

1. **Dangling references** — every phase id named in `depends_on`, `history`, or `condition`/`foreach` (`steps.<id>`) must exist and must belong to an *earlier* phase.
2. **`history` on a `foreach` phase** — not allowed; `history` cannot reference foreach phases.
3. **Unresolved `{vars}`** — every `{placeholder}` must have a matching entry in `vars` or be documented as a required `--context` input. List which is which.
4. **`$ENV_VAR` references** — check each one against the environment and `.env`; flag any that are missing. Flag any literal secrets that should be `$ENV_VAR` references instead.
5. **Tool/config pairing** — a tool that needs config without its block fails at runtime:
   - `Worktree` in `allowed_tools` → `worktree:` block required.
   - `AskUser` → `notifications:` label and a running relay.
   - `mcp__<server>__<tool>` → an `mcp_servers:` entry named `<server>`.
6. **`foreach` file paths** — if `foreach` names a file, confirm the path (after interpolation) will exist when the phase runs.

### Is it well-built? — recommendations

7. **Budgets** — recommend `max_iterations` (default is 500), `max_tokens`, and `max_cost` on any blueprint that runs unattended.
8. **Least-privilege tools** — challenge every entry in `allowed_tools`. Flag `Bash` every time (arbitrary command execution): if the commands are known in advance, recommend baking them into the prompt. Flag `mcp__<server>__*` wildcards. A phase that only transforms prior phases' output should have `allowed_tools: []`.
9. **Failure policy** — every phase should have a deliberate `on_failure`, not the implicit `abort` default. Recommend `retry` with `max_retries` for flaky operations and `skip` for optional ones.
10. **Data flow** — if a phase needs an earlier phase's output, it should wire `history` explicitly rather than hoping the prompt re-derives it. If a phase's output feeds a later `condition` or `foreach`, it needs an `output_schema` pinning the fields and types.
11. **Parallelism** — independent phases run sequentially by default. If phases only depend on a common ancestor, recommend `depends_on` so they run concurrently.
12. **Reproducibility** — recommend pinning `defaults.model` (and `defaults.llm.api_key` as a `$ENV` reference) rather than relying on per-phase or ambient configuration.
13. **Inputs split** — fixed values belong in `vars`; per-run values should come from `--context`. Flag values baked into prompts that will vary between runs.
14. **IDE support** — suggest the `# yaml-language-server: $schema=<path-to>/spec/v1.json` header comment if absent.

## Report format

1. **Verdict** — `goblin validate` output, verbatim.
2. **Schema errors** — one section per error: message, explanation, fix snippet. Omit if valid.
3. **Runtime risks** — numbered findings from the first checklist, each with the offending YAML and the fix.
4. **Recommendations** — numbered findings from the second checklist, ordered by impact.
5. **Clean bill** — for any checklist section with no findings, say so in one line rather than omitting it.

Then stop and offer to apply the fixes. Do not rewrite the blueprint until the user confirms which findings to act on.
