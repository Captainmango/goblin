# Step 7 — CLI client + documentation

**Goal:** Make the server reachable from the goblin CLI (so "everything the CLI can do" also
works through the server), and bring repo docs up to date.

**Depends on:** steps 3–5. **Blocks:** none (polish track, can parallel step 6).

---

## 7.1 `goblin run --server` client

Dependency direction forbids putting the client in `goblin-server` (it will depend on
`goblin`, and `goblin` must not depend back). Add a thin client **inside goblin**:

- New module `goblin/goblin/server_client.py` using `httpx` (add `httpx` to
  `goblin/pyproject.toml` explicitly — it currently arrives transitively).
- CLI changes in `goblin/goblin/cli.py`:
  - `goblin run --server URL [--api-key KEY] [--detach] <blueprint>`:
    - Local blueprint file → read YAML → `POST /api/runs` (inline source); else submit by
      server-side path when `--server-path` given.
    - Default: follow `WS /api/runs/{id}/events` and render progress; `--detach` prints the
      run id and exits (the run continues entirely server-side — the PRD's core requirement).
  - `goblin runs list|status|results|cancel|resume` against `--server`.
  - `goblin validate` gains `--server` to validate remotely (keeps local default).
  - Env fallback: `GOBLIN_SERVER_URL`, `GOBLIN_SERVER_API_KEY` (via `env()`/`secret_env()`).
- Keep CLI import-time budget: the client imports lazily inside commands
  (`task goblin:check-imports` must stay green [L1]).

## 7.2 Docs

- `docs/` (Zensical site): new section "Agent Server" — architecture (two processes, shared
  volume), API reference (from step 3 contracts), compose quickstart, k8s guide, Canvas
  backend setup (screenshots from step 4), auth model (two keys explained).
- `README.md`: one-paragraph pointer.
- `AGENTS.md` / `CLAUDE.md`: add `goblin-server` to the project list, its env vars to the
  table, and `task goblin-server:*` to the command lists (required by repo policy when
  structure changes).
- `ROADMAP.md`: check off "Agent Server ... ASGI app / Needs all the GOBLIN stuff / Test with
  Compose set up" as the steps land [L7]; adjust the "fork" note to record the composition
  decision (link this PRD).

## Acceptance criteria

- [ ] `goblin run --server http://localhost:8100 blueprints/test.yaml` works attached and
      `--detach`; `goblin runs ...` subcommands work.
- [ ] `task goblin:test goblin:check-imports` green.
- [ ] Docs build (`task` docs target) and new pages render; CLAUDE.md/AGENTS.md accurate.
