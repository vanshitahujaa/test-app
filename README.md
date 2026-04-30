# shortener — the realistic test target for AutoFixOps

A small but real URL shortener. Two services (API + worker), real Postgres,
real Redis, real Prometheus + Grafana monitoring, real failure modes.
The whole thing exists so [AutoFixOps](https://github.com/vanshitahujaa/Auto_fix_Ops)
has something interesting to diagnose, patch, and verify.

```
   client ──► api (FastAPI) ──► postgres (links table)
                  │
                  ├── redis (cache + click stream)
                  │
                  └── /metrics  ◄── Prometheus  ──►  Alertmanager  ──► AutoFixOps webhook
                                       ▲
                                       │
                                   worker (aggregator)
                                       │
                                       └─► postgres (click counts)
```

## What it is

**The business:** users `POST /shorten` to get a 7-character code; `GET /:code`
redirects them; `GET /stats/:code` shows how many times the link was clicked.
The API caches lookups in Redis and pushes click events onto a Redis stream.
The worker drains that stream and increments per-link counters in Postgres.

**Why two services:** so AutoFixOps' target resolver has more than one
pod to choose between. The api and worker have different memory profiles
and different chaos endpoints; you can demo memory-leak fixes on either.

**Why real DB and cache:** so the failure surface includes connection-pool
exhaustion, slow Postgres, Redis evictions — the real problems that bite
production services. A toy memory-leak harness can't reproduce those.

## Quick start (Docker, 1 minute)

```bash
make up                          # api on :8000, prom :9090, grafana :3001
make smoke                       # create a link, follow it, get its stats
make load                        # 200 reqs over ~20s
make inject-leak                 # leaks 50MB in the api process
make recover                     # frees it
make down
```

## Quick start (Kubernetes, 5 minutes)

Requires `kind`, `kubectl`, `helm`.

```bash
./scripts/bootstrap.sh           # creates a kind cluster and installs everything
kubectl -n shortener get pods
make k8s-status
```

Port-forward Grafana (admin / shortener-admin) and Prometheus per the
output of `bootstrap.sh`.

## Connecting AutoFixOps

See **[docs/INTEGRATION.md](docs/INTEGRATION.md)** — short version: configure
your AutoFixOps install with this repo's URL, set `TARGET_MANIFEST_PATH`
to `k8s/base/api.yaml`, point Alertmanager at the AutoFixOps webhook,
inject a fault, watch a real PR appear here.

## Structure

```
services/
  api/        FastAPI service — real business + chaos endpoints
  worker/     Background aggregator — different resource profile
k8s/
  base/                 Kustomize base (namespace, postgres, redis, api, worker)
  overlays/dev/         Single-replica dev overlay
  overlays/staging/     Pinned-tag staging overlay (the target for AutoFixOps PRs)
  monitoring/           ServiceMonitors, alert rules, alertmanager route, Grafana dashboard
scripts/
  bootstrap.sh          One-shot Kubernetes bring-up
  prometheus.local.yml  Standalone Prometheus config for docker-compose
docs/
  ARCHITECTURE.md       What's in here and why
  INTEGRATION.md        How to point AutoFixOps at this
docker-compose.yml      Fast local loop without K8s
Makefile                All operations
```

## Endpoints

### Real business
| Method | Path | Purpose |
|---|---|---|
| `POST` | `/shorten` | Create a short link. Body: `{"url": "https://..."}` |
| `GET`  | `/:code`  | 307 redirect to the target URL |
| `GET`  | `/stats/:code` | Click count + creation time |

### Operational
| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/healthz` | Liveness — always 200 if the process is alive |
| `GET`  | `/readyz`  | Readiness — Postgres + Redis must answer |
| `GET`  | `/metrics` | Prometheus exposition |

### Chaos (only when `CHAOS_ENABLED=true`)
| Method | Path | Purpose |
|---|---|---|
| `POST` | `/_chaos/leak?mb=N` | Allocate N MB and never free it |
| `POST` | `/_chaos/cpu?threads=N&seconds=S` | Pin N cores for S seconds |
| `POST` | `/_chaos/crash` | `os._exit(1)` — repeat 3× to crash-loop |
| `POST` | `/_chaos/recover` | Free leaks, stop CPU burners |

## License

MIT.
