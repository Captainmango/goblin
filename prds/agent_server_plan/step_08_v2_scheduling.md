# Step 8 — V2 design note: scheduled & triggered blueprint runs

**Goal:** Capture the design for "run blueprint X on a schedule / on an event" so V1 choices
don't paint us into a corner. **Not committed for V1.**

**Depends on:** V1 complete (steps 1–6). Related ROADMAP items: "eventbus? Webhooks for
timing stuff? ... a Blueprint is to run on a schedule/event trigger ... consume from an issue
tracker" [L7].

---

## 8.1 Requirements sketch

- Register a blueprint with a **cron schedule** (`schedule: "0 9 * * 1-5"`) and/or an
  **event trigger** (webhook: GitHub/Linear issue events, Slack, generic HTTP) [L7].
- Runs created by triggers are ordinary runs (same registry, same Canvas visibility,
  same notifications).
- Missed-run policy, overlap policy (`skip` | `queue` | `allow`), and per-schedule
  max concurrency must be explicit.

## 8.2 Options

| Option | Notes | Verdict |
|---|---|---|
| **A. Built-in scheduler in goblin-server** | New `schedules` table + an asyncio scheduler loop (APScheduler or hand-rolled croniter) in the lifespan; triggers call `RunService.submit_run`. Webhooks: `POST /api/hooks/{hook_id}` with HMAC secret → maps to blueprint + context template. | **Recommended** — small, owns the blueprint semantics, no new moving parts; fits the existing service shape. |
| B. OpenHands Automation Server | Canvas README pairs agent servers with an "Automation Server" (`OpenHands/automation`) for schedule/event-driven agents [S5]. | Rejected as primary: it schedules *conversations*, not blueprints — our trigger payload still needs blueprint semantics; also Canvas automations are scoped to whichever backend is active [S6]. Could be a source of event intake later. |
| C. External cron (k8s CronJob) calling `POST /api/runs` | Zero code. | Good interim stopgap; doesn't cover event triggers or overlap policy; document as the pre-V2 workaround. |

## 8.3 Data & API sketch (Option A)

```sql
CREATE TABLE schedules (
    schedule_id TEXT PRIMARY KEY,
    blueprint_path TEXT NOT NULL,
    context_template_json TEXT NOT NULL,   -- Jinja over trigger payload
    cron TEXT,                              -- NULL when webhook-only
    overlap_policy TEXT NOT NULL DEFAULT 'skip',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_fired_at TEXT, next_fire_at TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE webhooks (
    hook_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL REFERENCES schedules(schedule_id),
    secret TEXT NOT NULL                    -- HMAC
);
```

- `POST/GET/DELETE /api/schedules`, `POST /api/hooks/{hook_id}` (public but HMAC-signed).
- Scheduler loop lives in `main.py` lifespan; fires `submit_run` with rendered context.
- Blueprint schema: optionally allow `blueprint.schedule:` sugar in a future spec version —
  keep out of `spec/v1.json` until the API settles.

## 8.4 V1 forward-compat checklist (verify before closing V1)

- [ ] `RunService.submit_run` is callable with no HTTP context (scheduler reuses it). ✅ by design (step 2).
- [ ] Registry has room for a `source` column addition (`api|schedule|webhook`) — additive migration pattern already established (`_migrate()`).
- [ ] Events pub/sub is source-agnostic. ✅ (step 2).
- [ ] Overlap concerns: semaphore already bounds concurrency (step 2); overlap *policy* is scheduler-local.

## 8.5 Open questions for V2 kickoff

- Eventbus vs. direct webhooks (ROADMAP [L7]) — start direct; introduce a bus only when >1
  consumer of triggers exists.
- Issue-tracker consumption: first-class GitHub/Linear pollers, or just document webhook
  setup? Start with generic webhooks + context templates.
- Multi-replica scheduling (leader election) once the server scales out — single replica
  keeps this trivial for now.
