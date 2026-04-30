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

## Deploy to a real server (Render, 3 minutes)

The fastest way to get a public HTTPS URL for AutoFixOps to point at.

1. Push this repo to your GitHub.
2. Go to [dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint**.
3. Connect the repo. Render reads [`render.yaml`](render.yaml) and provisions:
   - `shortener-api` — public web service (free tier)
   - `shortener-db` — managed Postgres (free 90 days)
   - `shortener-redis` — managed Key Value store (free 25 MB)
4. Click **Apply**. About 3 minutes later you'll have a URL like
   `https://shortener-api-xxxx.onrender.com`.

Smoke test it:
```bash
URL=https://shortener-api-xxxx.onrender.com
curl -X POST $URL/shorten -H 'Content-Type: application/json' -d '{"url":"https://example.com"}'
curl -X POST $URL/_chaos/leak?mb=80
```

**Free-tier caveats**
- The web service sleeps after 15 min idle (~30-60s cold start).
- The background worker is **not** included on the free tier — see the
  commented block in `render.yaml`. Without it, click counts in
  `/stats/{code}` won't increment, but every other route works.
- After 90 days the Postgres becomes paid; you can swap to Supabase or
  Neon at that point by changing `DATABASE_URL` in the Render dashboard.

**Security note** — this Render deployment exposes `/_chaos/*` on the
public internet. Anyone with the URL can crash your pod. That's the
point for testing AutoFixOps. Don't put real data on this instance.

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
