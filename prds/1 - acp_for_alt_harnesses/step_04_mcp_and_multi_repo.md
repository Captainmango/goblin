# Step 4 — MCP forwarding + multi-repo execution

**Goal:** Prove and productise the two capabilities that motivate ACP support: blueprint
`mcp_servers` forwarded into harness sessions, and one run working across multiple repos via
per-phase `working_dir` — including concurrent phases.

**Depends on:** step 2 (execution path). **Blocks:** step 5 (docs need verified behavior).

---

## 4.1 MCP forwarding verification

The branch already passes `_build_mcp_servers(self, context)` into `ACPAgentSettings` [L2,
L8]. The SDK translates the `{"mcpServers": ...}` dict into ACP `session/new` servers —
stdio always forwarded; http/sse only when the harness advertises that transport at
`initialize` [L8]. Work items:

1. **Unit test:** a blueprint with defaults-level `mcp_servers` + phase-level additions
   produces the merged, env-resolved, interpolated dict on the constructed
   `ACPAgentSettings.mcp_config` (assert at the step 2 mock boundary).
2. **Gated e2e:** an ACP phase with a trivial stdio MCP server (e.g. the filesystem or
   fetch reference server via `uvx`/`npx`) answers a question requiring that server — proves
   forwarding end-to-end and proves the harness (not goblin) owns the MCP connection.
3. **Document the ownership split:** under ACP, MCP servers run as children of the harness
   subprocess in the session cwd, not of the goblin process — so relative `cwd`/`args` in a
   server config resolve against the harness's environment. Recommend absolute paths in
   docs; keep goblin's existing parent-env injection behavior unchanged (it already applies
   inside `_build_mcp_servers` [L2]).
4. **Capability gating note:** if a remote (http/sse) server is silently dropped because the
   harness didn't advertise the transport, the SDK logs it — surface this in docs
   troubleshooting (symptom: "server not visible to the harness").

## 4.2 Multi-repo execution model

No new code is expected — this is the emergent property of per-phase `working_dir` plus
harness-owned, cwd-keyed sessions (README §3.3.6, §5.4). This step proves and documents it:

1. **Sequential multi-repo blueprint** (new example `blueprints/acp_example.yaml`):

   ```yaml
   version: v1
   blueprint:
     name: acp_cross_repo
     defaults:
       harness:
         type: acp
         server: claude-code
         session_mode: bypassPermissions
     phases:
       - id: api_change
         prompt: "Add a health endpoint returning the service version; cover it with a test."
         harness: { working_dir: "{api_dir}" }
       - id: ui_change
         prompt: "Show the API version on the status page, read from the health endpoint."
         depends_on: [api_change]
         harness: { working_dir: "{ui_dir}" }
   ```

   (`api_dir`/`ui_dir` supplied via `--context` or `vars`.) Validate it in
   `task goblin:validate` so the example stays honest.
2. **Concurrent multi-repo:** a test at the mock boundary with two independent
   `working_dir`s in the same dependency wave, asserting both conversations build with
   distinct `LocalWorkspace` cwds and neither phase reads the other's state.
3. **Concurrency hazard guidance:** concurrent phases sharing one provider race on the CLI's
   global config dir (`~/.claude`, `CODEX_HOME`) — the exact scenario
   `acp_isolate_data_dir` exists for [L8]. Docs rule of thumb:
   - parallel ACP phases with the same provider → `isolate_data_dir: true`;
   - sequential phases, or users who *want* shared harness config/auth → `false` (default);
   - gemini-cli: isolation relocates `HOME` (wide blast radius — affects `git`, `npm`
     configs); document, don't automate.
4. **`foreach` across repos:** covered by step 2's per-iteration subprocess test; add a docs
   snippet showing `foreach: repos.txt` + `harness: { working_dir: "{item}" }` for the
   "roll a change out across N repos" use case.
5. **Interaction with blueprint `workspace:`:** local blueprint workspace merely defaults
   `working_dir`; remote + ACP is rejected (step 5 validation). Docs matrix:

   | Blueprint `workspace` | Phase `harness.type` | Result |
   |---|---|---|
   | (absent / local) | `openhands` | Today, unchanged |
   | (absent / local) | `acp` | ACP subprocess, cwd = harness → workspace → project → `.` |
   | `remote` | `openhands` | Today, unchanged |
   | `remote` | `acp` | Validation error |

## 4.3 Tests

- Merged `mcp_servers` → settings (unit); stdio MCP e2e (gated).
- Example blueprint validates in CI (`task goblin:validate` picks up `blueprints/`).
- Concurrent distinct-`working_dir` wave test; `foreach` + `{item}` working_dir test.
- `isolate_data_dir` maps to `ACPAgentSettings.acp_isolate_data_dir` (unit; no real
  isolation behavior test — that's SDK-covered).

## 4.4 Verification notes

- Added unit test proving defaults + phase `mcp_servers` merge into `ACPAgentSettings.mcp_config`.
- Added concurrent-wave test asserting distinct `LocalWorkspace` cwds per phase.
- Added `foreach` + `{item}` `working_dir` test.
- Added gated e2e tests for stdio MCP forwarding and multi-repo execution; both skipped here
  because no provider credentials were available.
- Created `examples/acp_example.yaml` (the repo's existing examples directory) instead of
  `blueprints/acp_example.yaml`; it validates with `task goblin:validate`.
- Added `docs/configuration/harness.md` covering MCP ownership, capability gating,
  concurrency guidance, the workspace×harness matrix, and provider credentials.

## Acceptance criteria

- [x] Gated e2e: ACP phase answers using a forwarded stdio MCP server (test present; live
      run pending credentials).
- [x] Example blueprint validates and is ready for a manual cross-repo run with a provider key.
- [x] Docs include the MCP ownership split, capability-gating troubleshooting, concurrency
      rule of thumb, and the workspace×harness matrix.
- [x] `task goblin:lint goblin:typecheck goblin:test goblin:validate` green.
