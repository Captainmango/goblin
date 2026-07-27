# Step 1 — Daemon in Kubernetes: manifests + kind verification

**Goal:** The split PRD's containerized daemon running as an in-cluster
Deployment+Service with durable state, verified end-to-end in `kind` by driving it with
the plain CLI over port-forward. This is Option C from the README made real — valuable on
its own, and the foundation the operator (step 2) talks to.

**Depends on:** `prds/3 - cli_daemon_split` step 7 (image, remote mode). **Blocks:**
steps 2–3.

---

## 1.1 Manifests — `goblin-operator/manifests/` (home chosen so all cluster artifacts
live in one place even though the operator itself lands in step 2)

```
manifests/
├── namespace.yaml        # goblin-system
├── daemon.yaml           # PVC + Secret template + Deployment + Service
└── kustomization.yaml    # optional convenience; plain kubectl apply -f must also work
```

**Daemon Deployment specifics:**

- Single replica (`strategy: Recreate` — RWO PVC; multi-replica is README §6.4).
- Image: the split-PRD step-7 daemon image; command
  `goblin-daemon serve --project-root /workspace --host 0.0.0.0 --port 8100`.
- Env from Secret `goblin-daemon-secrets`: `GOBLIN_DAEMON_TOKEN` (required, remote mode),
  `GOBLIN_MODEL_API_KEY`, `GOBLIN_DEFAULT_MODEL`, ACP provider keys as needed.
  **Template only** — real values via `kubectl create secret` or GitOps, never committed.
- PVC `goblin-workspace` on `/workspace` (registry sqlite, checkpoints, run dirs).
- Probes: liveness **and** readiness on `GET /healthz` (public route, no auth). Tune
  `initialDelaySeconds` for the measured cold start (SDK warm-up in lifespan — record
  the measured number from the split PRD's step 3 here).
- Resources: set requests/limits from the **measured warm RSS** recorded here during
  implementation (expect several hundred MB with the SDK warm; measure, don't guess).
- Service: ClusterIP `goblin-daemon:8100`. No Ingress in V1 (README §6.4 — the Service
  network is the boundary).

## 1.2 kind e2e — `goblin-operator/tests/e2e/` + root task `operator:e2e` (CI-gated,
**not** part of default `task test`)

Scripted flow (a `Taskfile` target wrapping kind + kubectl + pytest):

1. `kind create cluster`; load the daemon image (`kind load docker-image`).
2. Apply manifests; create the Secret with dummy-model values; wait for readiness.
3. `kubectl port-forward svc/goblin-daemon 18100:8100` &.
4. Drive the daemon with the real CLI in remote mode (`goblin --server
   http://localhost:18100` + token env [L1]):
   - `validate` an inline blueprint → wire findings.
   - Submit an **LLM-free blueprint** (a `load_project`-only or `condition`/`foreach`
     flow that never builds an agent [L5]) → poll `goblin runs <id>` to `completed`.
     (If an LLM phase is wanted, VCR at the daemon's provider boundary per repo rules
     [L4] — prefer LLM-free to keep the cluster test hermetic.)
5. **Pod-restart reconciliation:** submit a slow run (e.g. a phase with a long
   `sleep` via the Bash tool in a VCR-taped conversation, or a stub), `kubectl delete
   pod` mid-run, wait for the new pod, assert: registry shows the run `interrupted`,
   run history intact, daemon healthy — and that resuming via the CLI works.

## 1.3 Docs (this step's slice of `docs/deployment.md`)

- Cluster bring-up (kind + real clusters), Secret creation, port-forward usage, where
  state lives (PVC), upgrade flow (rolling = Recreate; brief unavailability; in-flight
  runs reconcile to `interrupted`).
- Security posture: token required; no Ingress by default; TLS only if ever exposed
  (then terminate at ingress/mesh).

## 1.4 Tests

- Manifest validation in default CI: `kubeval`/`kubectl apply --dry-run=client -f
  manifests/` (needs no cluster) — catches schema rot without kind flakiness.
- The §1.2 flow is the gated e2e; keep it scripted and recorded.

## Acceptance criteria

- [ ] `task operator:e2e` passes locally: daemon healthy in kind; CLI validate + run +
      runs over port-forward; pod-restart reconciliation verified (F7).
- [ ] Measured cold-start and warm RSS recorded in this file; probes/resources set from
      them.
- [ ] `docs/deployment.md` daemon-in-k8s section landed; no secrets in any committed
      manifest (CI grep).
- [ ] `task lint typecheck test` green; e2e gated behind `operator:e2e` only.
