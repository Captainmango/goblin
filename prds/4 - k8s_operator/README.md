# PRD: Goblin Kubernetes Operator — `BlueprintRun` CRDs over the daemon's wire protocol

**Status:** Proposed
**Author:** Investigation + planning (AI-assisted)
**Date:** 2026-07-27
**Depends on:** `prds/3 - cli_daemon_split` — **through step 7** (containerized daemon +
remote TCP/Bearer mode + wire protocol models). Do not start this PRD before that one
ships; it is written to be independently reviewable and independently schedulable [L1].
**Roadmap ref:** `ROADMAP.md` — "Set up remote box on K8s", "Do we need an eventbus?" [L2]
**Related PRDs:** `prds/agent_server_plan` (its step 6 superseded here in daemon-shaped
form — see §3.5) [L3]

---

## 1. Summary

Take the already-working `goblin-daemon` from the CLI/daemon split PRD and give it a
Kubernetes-native face:

1. **Deploy the daemon in-cluster** as a plain Deployment + Service + PVC — the step-7
   image unchanged, remote mode (TCP + Bearer) as designed, zero Kubernetes awareness in
   the daemon itself.
2. **Add a thin `goblin-operator`** (kopf) that watches `BlueprintRun` custom resources,
   translates them into wire-protocol calls (`POST /v1/runs`) against the daemon Service,
   and writes run status back to each resource's `status` subresource.

The key decision of this PRD: **the CRD is the interface, the wire protocol is the
mechanism, and the daemon stays cluster-agnostic.** `BlueprintRun.spec` is *generated
from* the split PRD's pydantic `SubmitRunRequest` model, so the resource schema and the
client/server contract can never drift apart. This is exactly the model the split PRD was
shaped for (its §3.7: "the protocol is disposable; the models are not") [L1].

This is deliberately a **separate PRD** from the CLI/daemon split: the split is already a
large, risky re-architecture that delivers standalone value (startup latency, run
registry, notifications, remote mode). Bolting cluster work onto it would double its
review surface and couple their schedules. Everything here is additive to a running
system — no changes to `goblin/` or `goblin-daemon/` beyond packaging.

Deliverables: k8s manifests for the daemon, a new `goblin-operator/` workspace member,
the `BlueprintRun` CRD (generated, CI-verified), create/status/delete/admission
handlers, a kind-based e2e suite, and a V2 design note for scheduled runs
(`CronBlueprintRun`).

---

## 2. Problem statement

After the split PRD's step 7, a containerized daemon accepts blueprint runs over
HTTP+Bearer. That is necessary but not sufficient in a cluster:

- **Nothing declarative can request a run.** k8s-native consumers — GitOps pipelines,
  other controllers, `kubectl`-wielding humans — work by applying resources, not by
  holding RPC sessions. "Spin up a workflow" must be expressible as
  `kubectl apply -f blueprintRun.yaml`.
- **No status surface.** A run submitted via the split PRD's CLI is observable through
  the CLI, but cluster tooling (Argo, `kubectl get`, k9s, alert managers) sees nothing.
  Run state must live on a resource's `status` subresource and as k8s Events.
- **No validation at admission.** An invalid blueprint submitted as a CR would only fail
  inside the daemon, after the resource exists. The split PRD's wire protocol already
  exposes `/v1/blueprints/validate` — admission webhooks can reject bad blueprints at
  `kubectl apply` time.
- **The daemon's durability story is untested in-cluster.** Pod restarts must not lose
  run history (registry sqlite + checkpoints), and runs in flight during a restart must
  reconcile to `interrupted` and stay resumable (mechanisms already built in split-PRD
  step 3 — unverified under real pod churn) [L1].

Requirements, restated:

1. A cluster resource (`BlueprintRun`) whose spec mirrors the wire protocol's submit
   request; applying one starts a run on the in-cluster daemon.
2. Run status, phase summary, spend, and errors flow back onto the resource.
3. Deleting the resource cancels the run; invalid blueprints are rejected at admission.
4. The daemon remains a boring, portable service: no k8s client imports, no leader
   election, no CRD code — replaceable and testable outside the cluster.
5. Everything verifiable in `kind` in CI.

---

## 3. Investigation

### 3.1 The operator model (verified)

Operators are not RPC servers. A Python operator opens long-lived HTTPS **watch streams**
against the API server, reconciles CRUD events on the resources it owns, and reports
back through the CR's `status` subresource plus k8s Events [S1]. Consequences:

- The split PRD's wire protocol is **not** what cluster consumers speak — they speak
  resources. The operator is the bridge: resource ⇄ wire-protocol client.
- There is no server to build in the operator beyond (optionally) admission webhooks;
  the control loop is outbound-only otherwise, which keeps RBAC and networking simple.
- Status writeback must be idempotent and conflict-tolerant (reconcile loops re-fire;
  `observedGeneration` discipline applies).

### 3.2 kopf assessment (verified, with honest caveats)

- **Capabilities** [S1, S2]: decorator handlers (`@kopf.on.create/.on.delete/.timer/
  .on.validate`), aiohttp watch streams with resume/backoff handled, `KopfPeering` /
  `ClusterKopfPeering` active/passive HA with 60 s keepalives, managed
  `ValidatingWebhookConfiguration` support, k8s Events via `kopf.event`.
- **Freshness:** `requires_python >=3.10`, classifiers through Python 3.13/3.14; latest
  release 1.44.6 (2026-06-03) with steady cadence [S2].
- **Maintenance honesty:** the upstream status page declares the project
  production-ready but in *maintenance mode* — "This piece of art is finished" — and it
  is effectively a single-maintainer project [S1]. Safe to adopt; nothing to expect.
- **Blast-radius assessment:** the operator this PRD needs is ~200–400 lines of
  translation logic (resource → one HTTP call → status patch). If kopf ever becomes a
  liability, a raw watch loop on `kubernetes-asyncio` replaces it without touching the
  daemon, the CRD, or the wire protocol. Kopf is therefore an acceptable convenience
  dependency, not a foundation.

### 3.3 The wire models are already CRD-shaped

The split PRD's `goblin/goblin/protocol.py` defines `SubmitRunRequest`
(`blueprint_yaml` / `blueprint_path`, `context`, `run_dir`, plus step-6's additive
`notify`) and `RunRecord`/`PhaseRecord` as pydantic v2 models [L1]. That buys:

- **Schema generation:** `SubmitRunRequest.model_json_schema()` is draft-2020-12 JSON
  Schema; a small generator emits the CRD's `spec` OpenAPI v3 schema from it (with the
  CRD-required structural-schema pruning), checked in and drift-tested in CI. Spec and
  protocol can never diverge silently.
- **Status mapping:** `RunRecord` fields map directly onto `status`:
  `phase` (from `status`), `phases` (per-phase summary), `spend`, `error`,
  `startedAt`/`completedAt`, plus operator-owned `runId` and `observedGeneration`.
- **Admission for free:** the daemon's `POST /v1/blueprints/validate` returns the same
  findings the CLI prints [L1] — the validating webhook is a thin proxy of it.
- Two spec deviations from the wire model, by design: `blueprintRef` (a
  ConfigMap key reference — blueprints as cluster resources, resolved by the operator
  before submission) instead of `blueprint_path` (meaningless in-cluster; inline YAML
  only — the daemon already rejects `imports:` for inline submissions [L1]).

### 3.4 What the daemon needs from k8s (no code changes)

Everything required already exists post-split-step-7 [L1]:

- **Remote mode:** `GOBLIN_DAEMON_HOST/PORT` + required `GOBLIN_DAEMON_TOKEN` — provided
  via a k8s Secret; TLS terminated at ingress/service mesh if ever exposed beyond the
  cluster network (in-cluster: the Service network is the boundary).
- **Persistence:** registry sqlite + checkpoints + run dirs under one workspace root →
  one PVC. Pod restart semantics are already correct: startup reconciliation marks
  in-flight runs `interrupted`; checkpoints stay resumable.
- **Secrets:** model/ACP provider keys are daemon-side env per the whole design — one
  Secret, mounted as env.
- **Runtime prerequisites in the image:** git, Node/`npx` (ACP harnesses, MCP stdio
  servers) — already in the step-7 image.
- **Single replica** to start: concurrent-run control is the daemon's semaphore;
  multi-pod run distribution is explicitly V2 (§6.4).

### 3.5 Prior art in-repo

`prds/agent_server_plan` step 6 sketched k8s manifests for its (never-landed)
`goblin-server` [L3]. Its deployment concerns — PVC for the shared workspace, Secret for
keys, Ingress guidance — transfer directly to the daemon and are incorporated here; its
server-centric manifest shape is superseded. Its step-8 scheduling ideas (cron/webhook
trigger tables) are recast in CRD terms in this PRD's V2 design note (step 3, §3.6).

---

## 4. Options analysis

### Option A — kopf operator, daemon untouched ✅

- Thin `goblin-operator` member; kopf handlers translate `BlueprintRun` ⇄ wire protocol;
  daemon deployed as-is.
- Pros: smallest possible new code surface; watch-resume/backoff/peering/admission
  handled by the framework [S1]; zero coupling into the daemon; operator replaceable
  (§3.2).
- Cons: kopf's maintenance-mode status (accepted, §3.2); a second Python service to
  operate.
- **Verdict: selected.**

### Option B — hand-rolled watch loop (`kubernetes-asyncio`)

- Pros: no framework dependency; full control.
- Cons: re-implements watch resume, backoff, leader election, and finalizer semantics —
  precisely the code operators get wrong; more lines to own for identical behavior.
- **Verdict: rejected** as the starting point; documented as the escape hatch if kopf
  becomes a problem.

### Option C — No operator: drive the in-cluster daemon with the CLI

`kubectl port-forward svc/goblin-daemon 8100` + `goblin --server` (split-PRD step 7
already delivers this).

- Pros: zero new components.
- Cons: fails the core requirement — cluster resources can't request runs; no status
  surface, no admission, no GitOps story.
- **Verdict: rejected** as the end state. It remains the correct **interim** (and is
  step 1's verification path).

### Option D — The daemon embeds the operator

One pod, one process: daemon loads kopf handlers alongside the API.

- Pros: one deployment.
- Cons: couples cluster lifecycle to the runtime (a CRD watch wedge kills runs and vice
  versa); forces k8s client imports into the daemon (violates the split PRD's
  k8s-agnostic design and its import guards); complicates local/compose deployment with
  cluster-only code paths.
- **Verdict: rejected.** Separation is the point of the architecture.

---

## 5. Target architecture

```
 kubectl apply -f blueprintRun.yaml
        │
        ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ k8s API server                                               │
 │   BlueprintRun CR (spec ≡ SubmitRunRequest + blueprintRef)   │
 └──────────┬───────────────────────────────▲───────────────────┘
            │ watch (kopf)                  │ status subresource + k8s Events
            ▼                               │
 ┌──────────────────────────────┐           │
 │ goblin-operator Deployment   │───────────┘
 │  on.create  → submit run     │
 │  timer      → poll status    │──── wire protocol (HTTP+Bearer, split-PRD models) ──┐
 │  on.delete  → cancel run     │                                                    │
 │  on.validate → admission     │                                                    │
 └──────────────────────────────┘                                                    ▼
                                                        ┌─────────────────────────────────┐
                                                        │ goblin-daemon Deployment + Svc  │
                                                        │  (split-PRD step-7 image,       │
                                                        │   remote mode, unchanged)       │
                                                        │  PVC: registry + checkpoints +  │
                                                        │       run dirs                  │
                                                        │  Secret: token + provider keys  │
                                                        └─────────────────────────────────┘
```

**Resource shape** (abbreviated; generated schema is authoritative):

```yaml
apiVersion: goblin.dev/v1
kind: BlueprintRun
metadata: { name: fix-flaky-tests }
spec:
  blueprintYaml: |            # or blueprintRef: {configMap: ..., key: ...}
    blueprint: {name: fix, phases: [...]}
  context: {pr: 1234}
  notify: true
status:
  observedGeneration: 1
  runId: 9f2c…
  phase: InProgress            # Pending|InProgress|Completed|Failed|Interrupted
  phases: {clone: {status: completed}, fix: {status: running}}
  spend: {iterations: 3, tokens: 81230, cost: 0.42}
  error: null
  startedAt: …  completedAt: …
```

**Flows:** create → submit → `status.runId`; 15 s timer → `get_run` → status patch +
Events on transitions; delete → `cancel_run` (best-effort); validate (admission) →
daemon `/v1/blueprints/validate` → reject with findings.

---

## 6. Requirements

### 6.1 Functional

| ID | Requirement |
|---|---|
| F1 | Manifests deploy the daemon (Deployment/Service/PVC/Secret) into a namespace; health via `/healthz` probes; CLI reachable via port-forward (Option C interim works). |
| F2 | `BlueprintRun` CRD whose spec schema is **generated from** the split PRD's pydantic wire models (+ `blueprintRef`); checked-in manifest; CI drift check. |
| F3 | Creating a CR submits a run to the daemon (inline YAML or ConfigMap-resolved); `status.runId` recorded. Duplicate reconcile of the same generation never double-submits (idempotency key = CR uid+generation, supported by the daemon since split-PRD step 3). |
| F4 | Status writeback: phase, per-phase summary, spend, error, timestamps; k8s Events on phase transitions. |
| F5 | Deleting a CR cancels the daemon run (`interrupted`), best-effort. |
| F6 | Admission validation rejects invalid blueprints at apply time with the daemon's own findings. |
| F7 | Daemon pod restart: run history survives (PVC); in-flight runs reconcile to `interrupted` and remain resumable via a new CR. |
| F8 | Operator deploys with least-privilege RBAC (watch/patch its CRDs, read ConfigMaps, create Events); single replica; peering documented for HA. |

### 6.2 Non-functional

- **Zero k8s coupling in existing packages:** no `kopf`/`kubernetes` imports anywhere in
  `goblin/` or `goblin-daemon/` (import-guard test); all cluster code in
  `goblin-operator/`.
- Repo conventions per `AGENTS.md`: `from __future__ import annotations`, pydantic where
  parsing, env-driven frozen config singleton (`GOBLIN_OPERATOR_*`), structured logging
  dot-notation events (`operator.submitted`, `operator.status_patched`,
  `operator.admission_rejected`), thin logic delegating to testable functions [L4].
- Secrets: daemon token + provider keys only via k8s Secret → env; never logged, never
  in CR specs/status.
- Status patches are idempotent, conflict-retried, and `observedGeneration`-disciplined.
- Tests: unit with a mocked `DaemonClient` boundary; e2e in `kind` (CI-gated); VCR for
  any recorded HTTP per repo rules; no MCP servers in integration tests [L4].

### 6.3 Changes by file/package

| Where | Change |
|---|---|
| `goblin-operator/` (new member) | `pyproject.toml` (deps: `goblin` for protocol models, `goblin-common`, `kopf`, `httpx`), `goblin_operator/{operator,daemon_client,config,crd}.py`, `manifests/`, `tests/`. |
| `goblin/` | **None.** (Wire models consumed read-only; any additive model change follows the split PRD's evolution rules and re-generates the CRD.) |
| `goblin-daemon/` | **None.** (Idempotency keys and remote mode already exist from the split PRD.) |
| Root | Workspace member + `Taskfile.yml` `goblin-operator:*` tasks + `operator:crd` (generate/drift-check) + `operator:e2e` (kind, CI-gated). |
| `manifests/` (in member) | `namespace.yaml`, `daemon.yaml` (Deployment/Service/PVC/Secret template), `crd-blueprintruns.yaml` (generated), `operator.yaml` (Deployment/RBAC). |
| Docs | `docs/deployment.md` k8s section; `ROADMAP.md` ticks; `prds/agent_server_plan` step-6 superseded note. |

### 6.4 Out of scope (V2+)

- **Scheduled/triggered runs** — `CronBlueprintRun` and webhook/issue-tracker adapters:
  design note in step 3, nothing built.
- **`AgentSandbox` CRDs / cattle-vs-pet runtimes** — separate roadmap investigation,
  untouched here [L2].
- **Multi-pod run distribution / autoscaling runners** — single daemon replica;
  distribution requires the split PRD's global-daemon/config-per-run work first.
- **Canvas composition** (embedded OpenHands agent server in the pod) — remains the
  rebased `agent_server_plan` follow-up, independent of this PRD.
- **TLS inside the cluster** — Service network is the boundary; mesh/ingress TLS is a
  deployment concern documented, not built.

---

## 7. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| kopf maintenance-mode / single maintainer | Framework stalls on a future k8s/Python version | Operator is ~200–400 lines with zero daemon coupling; swap to a raw `kubernetes-asyncio` watch loop (Option B) without touching CRD, daemon, or protocol [S1, S2] |
| CRD schema drifts from the wire models as the protocol evolves | Resources the daemon rejects (or vice versa) | Schema is generated from the models, checked in, and drift-tested in CI; protocol evolution rules (additive-only or version bump) inherited from the split PRD [L1] |
| Daemon pod restart mid-run | Run lost / stuck | PVC preserves registry + checkpoints; startup reconciliation marks `interrupted`; e2e kills the pod mid-run and resumes via a new CR (F7) |
| Duplicate reconcile double-submits a run | Cost/scope leak | Idempotency key = `uid:generation` (daemon-supported since split-PRD step 3); unit + e2e coverage |
| Secrets leak into CRs/logs | Credential exposure | Secrets only via Secret→env on the daemon; operator reads none of them; log-grep test; CR status carries ids/statuses only |
| ACP/MCP phases in-cluster (`npx` downloads, egress) | Slow/failing runs, surprise cost | Image pre-installs provider CLIs where possible; document egress + API-key requirements; sqlite (not Turso-cloud) as the default swarm backend in-cluster |
| Admission webhook unavailability blocks CR creation | GitOps pipelines wedge | `failurePolicy: Ignore` with a warning Event (validation then happens at reconcile time via the daemon submit path); document the tradeoff; managed webhook config kept optional |
| kind e2e flakiness in CI | Noise, lost trust | Gate e2e behind a dedicated task/CI job (not default `task test`); record runs; keep unit suite hermetic |

---

## 8. Step plan

| Step | File | Goal |
|---|---|---|
| 1 | `step_01_k8s_daemon_deployment.md` | Daemon running in kind: manifests, secrets/PVC, probes, port-forward CLI verification, pod-restart reconciliation e2e (Option C made real) |
| 2 | `step_02_crd_and_operator.md` | `goblin-operator` member: generated `BlueprintRun` CRD, create/timer handlers with status writeback, unit tests + kind e2e CR→Completed |
| 3 | `step_03_admission_cancel_and_hardening.md` | Delete→cancel, admission validation, RBAC/peering hardening, docs + ROADMAP, V2 scheduling design note (`CronBlueprintRun`) |

Step 1 delivers immediate standalone value (a supported in-cluster daemon). Steps 2–3
deliver the resource interface. Each step keeps `task lint typecheck test` green across
all workspace members.

---

## 9. Sources

### External

- **[S1]** kopf docs — https://kopf.readthedocs.io/en/stable/architecture/ (aiohttp watch
  streams, reconcile model), /peering/ (`KopfPeering` active/passive HA), /admission/
  (`@kopf.on.validate`, managed webhook configurations), /status/ (production-ready,
  maintenance mode: "This piece of art is finished").
- **[S2]** kopf PyPI — https://pypi.org/pypi/kopf/json (1.44.6, 2026-06-03;
  `requires_python >=3.10`; classifiers through Python 3.13/3.14).
- **[S3]** Kubernetes docs — Custom Resources + status subresource:
  https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/ ;
  validating admission webhooks:
  https://kubernetes.io/docs/reference/access-authn-authz/extensible-admission-controllers/ ;
  kind: https://kind.sigs.k8s.io/
- **[S4]** Operator pattern — https://kubernetes.io/docs/concepts/extend-kubernetes/operator/

### In-repo

- **[L1]** `prds/3 - cli_daemon_split/README.md` + steps — the foundation: wire protocol
  + pydantic models (step 2), daemon + registry + idempotency + reconciliation (step 3),
  remote TCP/Bearer mode + container image (step 7), "models survive as CRD schemas"
  (§3.7), validate endpoint (step 3 §3.4).
- **[L2]** `ROADMAP.md` — k8s line items ("Set up remote box on K8s", `AgentSandbox`
  CRDs, cattle-vs-pet) and the eventbus/scheduling questions.
- **[L3]** `prds/agent_server_plan/README.md` + `step_06_kubernetes.md` — prior k8s
  manifest thinking (superseded here in daemon form, §3.5) and scheduling ideas (recast
  as `CronBlueprintRun` in step 3).
- **[L4]** `AGENTS.md` — repo conventions (config singletons, structured logging,
  testing rules, VCR integration tests, no MCP in integration tests).
- **[L5]** `examples/` blueprints + `goblin/tests/integration/` — e2e material (the
  `load_project`-only pattern for LLM-free cluster tests).
