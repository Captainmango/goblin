# Step 5 — Worktree isolation, notifications, docs

**Goal:** Firstmate-grade hardening: disposable per-crewmate worktrees, completion alerts via
the notifications-relay, and the documentation/updates that make crew mode discoverable.

**Depends on:** steps 2–4. **Blocks:** nothing (final V1 step).

---

## 5.1 Per-crewmate worktrees

- Extend `spawn` with `worktree: bool` (action field already reserved in step 2): when true,
  the runtime creates a git worktree under `<crew_home>/worktrees/<name>` using the existing
  worktree machinery (`goblin/tools/worktree_tool.py` and the phase `worktree:` config as the
  reference implementation [L9]) and points the crewmate's `LocalWorkspace` at it.
- Lead-facing guidance in `CREW_LEAD_PROMPT`: use worktrees when two crewmates could touch
  the same repo; parallel work on one checkout must never collide [S1, S2].
- Teardown policy (mirroring the swarm's `CleanupPolicy` [L1]): keep the worktree while the
  session is non-terminal or has undelivered messages; on terminal status, keep by default
  (the user may want the diff) and document `git worktree remove` — automatic cleanup is a
  config flag (`GOBLIN_CREW_CLEAN_WORKTREES`, default off) rather than V1 magic.
- Validation: `worktree: true` outside a git repo → typed spawn error returned to the lead.

## 5.2 Notifications

- On terminal crewmate events (`done`/`failed`), if relay env is configured (`RELAY_HOST` /
  `RELAY_PORT` / `RELAY_API_KEY`), fire a relay notification via the existing client
  (`goblin/notifications/client.py` [L10]): `"crew <name>: crewmate <session> done — <first
  line of result>"`. Failures of the relay itself are logged (`crew.notify_failed`) and
  swallowed — notifications must never break supervision.
- This is the away-from-keyboard half of "removing cognitive load": the user can leave the
  crew running and get pinged on Telegram when sessions finish, instead of polling
  `crew sessions`.

## 5.3 Docs and repo updates

- New `docs/crew.md`: concepts (crew/lead/crewmate/session), `goblin crew` walkthrough,
  `crew sessions` reference, worktrees, cost limits, notifications, restart behavior,
  comparison table vs the swarm phase (when to use which). Link it from `docs/index.md` and
  the zensical nav.
- `docs/cli/goblin.md`: complete `crew` command reference (completes step 4's stub).
- Root `AGENTS.md`/`CLAUDE.md`: add crew mode to the goblin project description and the three
  new env vars (`GOBLIN_CREW_HOME`, `GOBLIN_CREW_MAX_CREWMATES`, `GOBLIN_CREW_MODEL`,
  `GOBLIN_CREW_CLEAN_WORKTREES`) to any env-var documentation [L12].
- `ROADMAP.md`: tick the crew-mode line if added there, or add a checked entry linking the
  PRD folder.
- `goblin/README.md` / `VISION.md`: one paragraph noting interactive crew mode alongside
  blueprint runs (VISION's "between chat and maximalist frameworks" positioning [L14]).

## 5.4 Tests

- Worktree spawn: inside a `tmp_path` git repo, `spawn(worktree=True)` produces a valid
  worktree the crewmate writes into; non-repo spawn errors cleanly; cleanup flag honored.
- Notifications: relay client mocked at the client boundary [L12] — terminal events fire
  exactly one notification each; relay exceptions don't affect session status.
- Docs: `goblin validate`-style sanity — any example YAML in docs stays valid (crew mode
  itself has no blueprint surface, so this is just link/lint hygiene).

## Acceptance criteria

- [ ] Two crewmates working the same repo with `worktree: true` produce non-colliding diffs
      in separate worktrees (integration-style unit test on `tmp_path`).
- [ ] Relay alert received (mocked client) on crewmate completion; relay down → nothing
      breaks.
- [ ] Docs published under the zensical site; `task lint format typecheck test` green
      workspace-wide.
- [ ] V1 scope from `README.md` §6.1 (F1–F10) fully covered by tests or documented manual
      verification.
