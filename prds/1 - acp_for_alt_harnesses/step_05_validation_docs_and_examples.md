# Step 5 — Semantic validation, docs, and examples

**Goal:** Fail fast on incoherent blueprint combinations (alpha software — no silent
ignores), and give users complete documentation of the `harness` block.

**Depends on:** steps 1–4 (final behavior is what gets validated and documented).
**Blocks:** nothing (final V1 step).

---

## 5.1 Semantic validation in `goblin/validation.py`

JSON Schema (draft-07) already validates the `harness` block's shape (step 1). The
conditional rules need a small semantic pass that runs **after** schema validation, when the
blueprint parses cleanly [L6]. The current validator has no severity concept — every finding
is an error and `goblin validate` exits 1 on any finding [L14] — so this step introduces
one: extend the finding dict to `{"path", "message", "validator", "severity"}` with
`severity: "error" | "warning"` (existing schema findings default to `"error"`), give the
semantic rules `validator: "semantic"`, and make the CLI print warnings but exit non-zero
only on errors. Rules (from README §3.5):

| # | Rule | Severity |
|---|---|---|
| 1 | `harness.type: acp` (phase or inherited from defaults) + `allowed_tools` on the phase | Error — the harness owns its tools; the list would silently do nothing |
| 2 | ACP + `hooks` (phase or defaults-level with an ACP phase) | Error — OpenHands tool hooks never fire inside the harness |
| 3 | ACP + `worktree` | Error — the Worktree tool is an OpenHands tool |
| 4 | ACP + blueprint `workspace.type: remote` | Error — ACP spawns a local subprocess |
| 6 | `harness.server: custom` without `harness.command` | Error — SDK raises otherwise; catch it at validate time |
| 7 | `harness.command` set together with a non-`custom` `server` | Warning — overrides the provider's pinned command; intentional but worth surfacing |
| 8 | ACP + `max_iterations` on the phase | Warning — one prompt turn per execution; `harness.prompt_timeout` is the guard |

Implementation notes:

- The pass must evaluate the **effective** harness per phase (defaults merged) — reuse
  `_resolve_harness_config`'s merge logic minus interpolation (validation has no run
  context; treat strings as opaque).
- Only prompt phases can carry `harness` semantics today; a `harness` key on
  `load_project`/`swarm` phases is a schema-level error already (additionalProperties), so
  the semantic pass only handles prompt phases.
- `format_errors` groups warnings under the blueprint path alongside errors; warnings never
  affect the exit code [L6, L14].

## 5.2 Tests for validation

One test per rule above (trigger + negative case), plus: defaults-inherited ACP + phase
`allowed_tools` (rule 1 via inheritance), and `type: openhands` explicitly set with
`allowed_tools` (must pass — the guard only fires for ACP).

## 5.3 Documentation

- **New `docs/configuration/harness.md`:** full field reference (schema table from README
  §5.1); provider registry and pinned CLI versions; credential model (ambient env vs
  `harness.secrets` vs reserved file secrets, masking); session semantics (cwd-keyed,
  harness-owned, fresh per phase execution); permission posture (`session_mode`,
  auto-approval); concurrency guidance (`isolate_data_dir`); MCP ownership split; Node/npx
  prerequisite and `command` override; troubleshooting (harness not found, MCP transport
  dropped, usage stats missing).
- **`docs/phases/prompt.md`:** `harness` in the phase-field list; the §3.5
  field-compatibility table; pointer to the harness page.
- **`docs/phases/load-project.md`:** when `load_project` is still useful with ACP (setting
  `project_path` as the `working_dir` fallback; OpenHands phases in a mixed blueprint) vs
  what the harness loads natively (making `load_project` unnecessary for pure-ACP runs).
- **`docs/configuration/environment-variables.md`:** no new goblin env vars are introduced —
  but document the provider variables the harness subprocesses consume
  (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`) and that
  `GOBLIN_DEFAULT_MODEL`/llm config now also backs the structured-output coercion LLM for
  ACP phases.
- **`blueprints/acp_example.yaml`:** the multi-repo example from step 4, kept valid by
  `task goblin:validate`.

## 5.4 Repo bookkeeping

- Root `AGENTS.md`: add a bullet under the goblin project notes — blueprint `harness` block,
  ACP providers, and the Node/npx runtime prerequisite. (No new env vars to add to the
  table.)
- `goblin/README.md` / docs index: link the new harness page where phase docs are indexed.
- `ROADMAP.md`: if an ACP line item exists or a nearby "alternative harnesses" item, mark
  it; otherwise leave the roadmap untouched (this PRD is the record).

## Acceptance criteria

- [ ] All eight validation rules implemented with tests; `goblin validate` fails on rule
      1–6 violations and warns on 7–8.
- [ ] `docs/configuration/harness.md` published and cross-linked; every field in the schema
      is documented with defaults.
- [ ] Example blueprint validates; docs build (`task` docs target if present) shows no
      broken links to the new page.
- [ ] `AGENTS.md` updated; `task lint typecheck test validate` green repo-wide.
