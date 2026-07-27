# Step 3 — Admission, cancellation, hardening, and the V2 scheduling note

**Goal:** Complete the resource lifecycle (delete → cancel), reject invalid blueprints at
admission, harden the deployment (RBAC/HA/docs), and capture the scheduling design that
answers the roadmap's eventbus questions — as a **design note only**. After this step the
operator PRD is done; scheduling becomes its own future PRD.

**Depends on:** step 2. **Blocks:** nothing (terminal step of this PRD).

---

## 3.1 Delete → cancel

- `@kopf.on.delete("goblin.dev", "v1", "blueprintruns")`: if `status.runId` exists and
  the CR isn't terminal, `cancel_run(runId)` — best-effort: swallow `DaemonUnavailable`
  / already-terminal with a warning Event; the CR is going away regardless [S1].
- Decide and document finalizer semantics: with no finalizer, the CR vanishes instantly
  and cancellation is fire-and-forget (chosen default — simple, and the daemon-side run
  still lands in `interrupted`). A finalizer that blocks deletion on cancel
  acknowledgement is the alternative; implement only if the e2e shows a real race
  (record the decision here).
- e2e: apply CR for a slow run → `kubectl delete blueprintRun` mid-run → daemon shows
  `interrupted` (query via port-forwarded CLI); no operator errors.

## 3.2 Admission validation — `@kopf.on.validate`

- On create/update: extract blueprint YAML (inline or ConfigMap-resolved), call the
  daemon's `POST /v1/blueprints/validate`, and `raise kopf.AdmissionError(...)` with the
  formatted findings when error-severity findings exist — the same output `goblin
  validate` prints [L1].
- **Webhook plumbing decision (record the choice):** managed
  `ValidatingWebhookConfiguration` via kopf requires a reachable HTTPS endpoint — in
  kind, use kopf's dev tunnel/server options [S1]; in real clusters, a small Service +
  cert-manager annotation or a manually provisioned cert. If plumbing proves brittle for
  V1, ship admission as **reconcile-time validation** instead (create handler validates
  before submit and fails the CR with `status.phase: Failed` + Event + findings), and
  defer the true webhook to a follow-up. Both paths use the identical validation call;
  the e2e must cover whichever ships.
- `failurePolicy: Ignore` if the webhook ships (README §7: don't wedge GitOps on
  webhook outages — reconcile-time validation is the backstop either way).
- e2e: `kubectl apply` an invalid blueprint → rejected at admission (or failed at
  reconcile, per the shipped path) with findings visible to the user.

## 3.3 Hardening

- **RBAC audit:** trim the step-2 rules to the observed minimum; add a default-deny
  NetworkPolicy example for the namespace (operator → API server + daemon only; daemon →
  LLM egress + operator/CLI inbound only) as an optional manifest with docs.
- **HA note (docs, not code):** single replica by default; for standby HA, deploy two
  replicas with kopf peering (`KopfPeering`, active/passive — highest priority works,
  other freezes) [S1]. No code change: remove `--standalone`, apply the peering CR.
- **Operator Deployment polish:** resource requests/limits from measured usage (record);
  structured logs with dot-notation events (`operator.submitted`,
  `operator.status_patched`, `operator.admission_rejected`, `operator.cancelled`) [L4].
- **Secret hygiene e2e assertion:** grep operator + daemon logs during the e2e for token
  / provider-key values; fail on any hit.

## 3.4 Docs + cross-PRD bookkeeping

- `docs/deployment.md`: full k8s section — architecture diagram, install order
  (CRD → daemon → operator), `kubectl get blueprintRuns` workflow, admission behavior,
  troubleshooting (operator logs, daemon logs, port-forward), upgrade + token-rotation
  procedure.
- `ROADMAP.md`: tick the delivered k8s line items; leave `AgentSandbox` CRDs and
  cattle-vs-pet explicitly unchecked (their own future PRD) [L2].
- `prds/agent_server_plan`: note on its step 6 that it is superseded by this PRD
  (daemon-shaped manifests), completing the rebase the split PRD started [L3].
- Root `AGENTS.md`/`CLAUDE.md`: new workspace member + `goblin-operator:*` and
  `operator:e2e`/`operator:crd` tasks.

## 3.5 V2 design note — scheduled and triggered runs (appendix to this file; **not
implemented**)

One page recasting the roadmap's eventbus questions [L2] in CRD terms:

- **`CronBlueprintRun`:** spec `{schedule: "*/15 * * * *", suspend: false, runTemplate:
  <BlueprintRun.spec>}`; a `@kopf.timer` handler computes due windows (persist
  `lastScheduleTime` in status) and stamps out child `BlueprintRun`s (ownerReferences
  for cascade). ConcurrencyPolicy (`Forbid` default) maps to "skip if previous child
  non-terminal". No daemon or operator-core changes — pure composition.
- **Webhook triggers:** a tiny ingress-facing adapter Deployment (FastAPI, repo
  conventions) that verifies a provider signature and creates `BlueprintRun`s — the
  "eventbus" as a 100-line service, not infrastructure.
- **Issue trackers:** a poller adapter per tracker (same pattern: external state → CR).
- Design rule to state explicitly: **all triggers converge on creating `BlueprintRun`
  CRs.** Nothing new ever talks to the daemon directly; the resource is the queue.

## 3.6 Tests

- Unit: delete handler best-effort semantics (daemon down, already-terminal);
  admission/reconcile validation mapping (findings → error message); NetworkPolicy and
  RBAC manifests parse.
- e2e (gated): §3.1 cancel flow; §3.2 invalid-blueprint rejection; secret-grep; invalid
  blueprint in a ConfigMap variant.

## Acceptance criteria

- [ ] `task operator:e2e` covers: CR→Completed, ConfigMap blueprint, idempotent
      re-apply, delete→`interrupted`, invalid blueprint rejected (admission or
      reconcile per shipped path), zero secret leakage in logs.
- [ ] Docs + ROADMAP + agent_server_plan bookkeeping landed; `AGENTS.md` updated.
- [ ] V2 scheduling design note (§3.5) written; zero scheduling code.
- [ ] `task lint typecheck test` green across all four workspace members; e2e remains
      gated behind `operator:e2e`.
