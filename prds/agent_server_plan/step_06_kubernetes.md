# Step 6 — Kubernetes deployment (remote hosting)

**Goal:** Run the same image remotely in a k8s cluster so Canvas and CLI clients can reach a
shared, always-on Goblin Agent Server.

**Depends on:** step 5. **Blocks:** nothing (V1 completes here).

---

## 6.1 Manifests — `infra/k8s/agent-server/`

Plain manifests first (a Helm chart is a later nicety):

- **`namespace.yaml`** — `goblin`.
- **`secret.yaml`** — `GOBLIN_SERVER_API_KEY`, `OH_SESSION_API_KEYS_0`, `OH_SECRET_KEY`,
  `GOBLIN_MODEL_API_KEY` (via `stringData`; document sealing/external-secrets as the
  production path).
- **`pvc.yaml`** — `workspace` PVC (RWO is fine: single replica; see §6.3).
- **`deployment.yaml`** — single replica (stateful, volume-backed; scale-out is V2+):
  - **Pod = one box, two containers** (mirrors the compose box but k8s-native; preferred over
    a supervisor in one container because k8s gives per-container health/lifecycle):
    - `goblin-server` container: the step-5 image, command overridden to
      `goblin-server serve`; `readinessProbe`/`livenessProbe` on `:8100/health`.
    - `runtime` container: same image, command
      `python -m openhands.agent_server --host 0.0.0.0 --port 8101`, workingDir `/workspace`;
      probes on `:8101/health` and `:8101/ready` [S2].
    - Both mount the `workspace` PVC at `/workspace` (shared filesystem requirement — see
      `README.md` §5 and the `load_project` note in step 4).
    - `GOBLIN_SERVER_RUNTIME_HOST: 127.0.0.1` (pod-local loopback).
    - Resources: start `requests: 500m/1Gi`, `limits: 2/4Gi` per container (LLM-bound, not
      CPU-bound); tune with usage.
    - `securityContext`: non-root, drop caps; note the runtime executes arbitrary agent
      commands in its container — treat the pod as a trust boundary.
- **`service.yaml`** — ClusterIP `goblin-agent-server` exposing `8100` (api) and `8101`
  (runtime) as named ports.
- **`ingress.yaml`** (optional) — host- or path-based TLS ingress to both ports (separate
  hostnames simplest, e.g. `goblin-api.example.com` → :8100,
  `goblin-runtime.example.com` → :8101); terminate TLS at the ingress (both APIs are
  key-authenticated but transport encryption is on you [S2]).
- Human-in-the-loop notifications are not part of V1; do not deploy a relay sidecar.

## 6.2 Operate

- Image publishing: tag `jp-goblin-agent-server:<version>` to your registry; document the
  build/push task in the root `Taskfile.yml` (`agent-server:image`).
- Canvas: Manage Backends → `https://goblin-runtime.example.com` + `OH_SESSION_API_KEYS_0`.
- CLI/API clients: `https://goblin-api.example.com` + Bearer key (step 7 wires the CLI).
- Upgrades: `Recreate` strategy (volume is RWO); in-flight runs die on rollout — document
  drain-before-upgrade (cancel or let runs finish) since resume-from-checkpoint exists.

## 6.3 Deferred (explicitly not this step)

Per ROADMAP [L7]: `AgentSandbox` CRDs, "cattle vs pet" box semantics, per-run pods/sandboxes,
horizontal scaling with a shared run queue, eventbus/webhook coordination. The single-pod
design here is the "pet" that V2 can evolve from.

## Acceptance criteria

- [ ] `kubectl apply -f infra/k8s/agent-server/` brings the pod to Ready on a test cluster
      (kind/k3s acceptable for the gate).
- [ ] Port-forward smoke test: submit run → completes; checkpoint on the PVC; Canvas backend
      reachable via ingress or port-forward.
- [ ] Restart pod → registry/checkpoints/conversations survive; interrupted run resumable.
- [ ] Secrets never in plain manifests destined for git (only `secret.yaml` template +
      `.gitignore`d real file or external-secrets annotation).
