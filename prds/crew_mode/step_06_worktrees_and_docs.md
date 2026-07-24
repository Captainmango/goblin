# Step 6 — Worktree isolation and docs

**Goal:** Firstmate-grade hardening: disposable per-crewmate worktrees, and the
documentation/updates that make crew mode discoverable.

**Depends on:** steps 2–4 (step 5 optional — worktrees compose with ACP crewmates' `cwd`).
**Blocks:** nothing (final V1 step).

---

## 6.1 Per-crewmate worktrees

- Enable the reserved `worktree: bool` spawn field: when true, the runtime creates a git
  worktree under `<crew_home>/worktrees/<name>` using the existing worktree machinery
  (`goblin/tools/worktree_tool.py` and the phase `worktree:` config as the reference
  implementation [L9]) and points the crewmate's `LocalWorkspace` at it. For ACP crewmates
  the worktree simply becomes the harness session `cwd` [L10].
- Lead-facing guidance in `CREW_LEAD_PROMPT`: use worktrees when two crewmates could touch
  the same repo; parallel work on one checkout must never collide [S1, S2].
- Teardown policy (mirroring the swarm's `CleanupPolicy` [L1]): keep the worktree while the
  session is non-terminal or has undelivered messages; on terminal status, keep by default
  (the user may want the diff) and document `git worktree remove` — automatic cleanup is a
  config flag (`GOBLIN_CREW_CLEAN_WORKTREES`, default off) rather than V1 magic.
- Validation: `worktree: true` outside a git repo → typed spawn error returned to the lead.

## 6.2 Docs and repo updates

- New `docs/crew.md`: concepts (crew/lead/crewmate/session), `goblin crew` walkthrough,
  `crew sessions` reference, harness choice (native vs ACP), worktrees, cost limits, restart
  behavior, and a comparison table vs the swarm phase (when to use which). State plainly
  that crew mode is CLI-only. Link it from `docs/index.md` and the zensical nav.
- `docs/cli/goblin.md`: complete `crew` command reference (completes step 4's stub).
- Root `AGENTS.md`/`CLAUDE.md`: add crew mode to the goblin project description and document
  the new env vars (`GOBLIN_CREW_HOME`, `GOBLIN_CREW_MAX_CREWMATES`, `GOBLIN_CREW_MODEL`,
  `GOBLIN_CREW_CLEAN_WORKTREES`) [L12].
- `ROADMAP.md`: add a checked crew-mode entry linking this PRD folder.
- `goblin/README.md` / `VISION.md`: one paragraph noting interactive crew mode alongside
  blueprint runs (VISION's "between chat and maximalist frameworks" positioning [L14]).

## 6.3 Tests

- Worktree spawn: inside a `tmp_path` git repo, `spawn(worktree=True)` produces a valid
  worktree the crewmate writes into; non-repo spawn errors cleanly; cleanup flag honored.
- Two crewmates working the same repo with `worktree: true` produce non-colliding diffs in
  separate worktrees.
- Docs hygiene: links resolve; any example YAML in docs stays valid (crew mode itself has no
  blueprint surface).

## Acceptance criteria

- [ ] Two worktree'd crewmates on the same repo produce non-colliding diffs
      (integration-style unit test on `tmp_path`).
- [ ] `worktree: true` with `harness: acp` places the harness session `cwd` in the worktree
      (asserted at the mocked-agent boundary).
- [ ] Docs published under the zensical site; `task lint format typecheck test` green
      workspace-wide.
- [ ] V1 scope from `README.md` §6.1 (F1–F11) fully covered by tests or documented manual
      verification.
