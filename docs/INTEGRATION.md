# Integrating shortener with AutoFixOps

This is the guide for wiring this test target into a running AutoFixOps
install so you can watch the full loop end-to-end:
**alert → diagnose → policy → PR on this repo → merge → verify**.

## Prerequisites

- AutoFixOps is running somewhere reachable. Locally that's
  `http://localhost:8000`. Inside Kubernetes that's
  `http://autofixops-api.autofixops.svc.cluster.local:8000`.
- This repo (`shortener`) is deployed somewhere — Kubernetes via
  `make k8s-up monitoring-up`, or docker-compose via `make up`.
- A GitHub Personal Access Token with `repo` scope on **this repo**
  (so AutoFixOps can open PRs against it).

## 1. Configure AutoFixOps to point at this repo

In the AutoFixOps dashboard at `http://localhost:3000/onboard`:

| Field | Value |
|---|---|
| Project name | `shortener` |
| GitHub Repository | `vanshitahujaa/test-app` |
| GitHub Token | your PAT |
| Target manifest path | `k8s/base/api.yaml` |
| Prometheus URL | `http://localhost:9090` (port-forwarded) or the in-cluster URL |
| Allowed chaos namespaces | `shortener` |
| Max resource scale factor | `2.0` |

Equivalent CLI:

```bash
curl -X POST http://localhost:8000/api/v1/config -H 'Content-Type: application/json' -d '{
  "name": "shortener",
  "github_repo": "vanshitahujaa/test-app",
  "github_token": "ghp_your_token_here",
  "target_manifest_path": "k8s/base/api.yaml",
  "prometheus_url": "http://localhost:9090",
  "allowed_chaos_namespaces": ["shortener"],
  "max_resource_scale_factor": 2.0
}'
```

## 2. Wire Alertmanager to AutoFixOps

If you ran `monitoring-up`, the file
`k8s/monitoring/alertmanager-config.yaml` already declares an
`AlertmanagerConfig` that routes `namespace=shortener` alerts to
the AutoFixOps webhook. Verify:

```bash
kubectl -n monitoring get secret alertmanager-kube-prometheus-stack-alertmanager-generated -o jsonpath='{.data.alertmanager\.yaml}' | base64 -d | grep -A2 autofixops
```

If you're running AutoFixOps **outside** the cluster, you'll need to
either expose a NodePort/Ingress for it, or use `host.docker.internal`
for kind:

```yaml
# in k8s/monitoring/alertmanager-config.yaml
url: http://host.docker.internal:8000/api/v1/alerts
```

For docker-compose-only setups, just point Alertmanager at
`http://host.docker.internal:8000/api/v1/alerts` directly.

## 3. Trigger the loop

### From the AutoFixOps dashboard

1. Open the Chaos page (red zone).
2. Set target to your shortener API URL, e.g. `http://localhost:8000`.
3. Pick "Memory leak", type `CONFIRM`, click Inject.

### Or from this repo's tooling

```bash
make inject-leak
```

Within ~1 minute Prometheus fires `TargetAppMemoryLeak`, Alertmanager
forwards it to AutoFixOps, and:

1. The incident appears on the AutoFixOps `/incidents` page.
2. Status moves through `INGESTED → CONTEXT_BUILT → DIAGNOSED → POLICY_APPROVED → PENDING_PR_MERGE`.
3. **A real PR appears on `vanshitahujaa/test-app`** modifying
   `k8s/base/api.yaml` to bump `memory: 192Mi → 384Mi` (capped at 2×).
4. The PR body contains the full incident evidence chain.

## 4. Merge and watch verification

Open the PR, review it, merge. AutoFixOps polls the PR every 60s,
notices the merge, waits 5 minutes for your deployment system (or a
`kubectl rollout restart`) to apply the new manifest, then queries
Prometheus to confirm the metric has stabilized.

Manual apply for testing:

```bash
git pull   # to pick up the merged change locally
make k8s-up
```

After the stability window, the incident transitions to `RESOLVED`
and the resolution is added to AutoFixOps' RAG memory.

## 5. Try the AI fallback path

The rule engine knows about `TargetAppMemoryLeak`, `HighCPUUsage`,
and `PodCrashLooping`. To force the AI path, fire an unrecognized alert:

```bash
# Force a 5xx storm by killing Postgres
kubectl -n shortener delete pod -l app=postgres
# wait ~2 min — HighErrorRate fires, rule engine returns UNKNOWN,
# AI is invoked, incident is escalated (read-only AI)
kubectl -n shortener get pods    # postgres restarts on its own
```

## Troubleshooting

**No incident appears in AutoFixOps after `make inject-leak`.**
Check Alertmanager has the route:
```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-alertmanager 9093
open http://localhost:9093
```
Look at *Status → Config* for the autofixops receiver and *Alerts*
for `TargetAppMemoryLeak`.

**The PR is created on AutoFixOps' own repo, not this one.**
Your `ProjectConfig.github_repo` is wrong. Reset via the dashboard
Settings page or the `POST /api/v1/config` call above.

**The PR modifies a file that doesn't exist.**
Set `target_manifest_path` to a path that actually exists in this
repo. Default for shortener is `k8s/base/api.yaml`; for the worker,
`k8s/base/worker.yaml`.

**Verification times out.**
The default is 30 polls × 60s = 30 min waiting for the merge. If you
forget to merge, the incident transitions to `FAILED`. Re-trigger
the leak with `make inject-leak` — dedup is per-pod-and-time-bucket so
a second leak gets a fresh incident.
