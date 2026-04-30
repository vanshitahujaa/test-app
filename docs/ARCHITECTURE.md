# Architecture

The shortener exists for one reason: to give AutoFixOps a realistic
target. Every design decision in this repo is in service of that goal.

## Components

### `api` — FastAPI HTTP service

A normal-looking web service. Three real routes (`/shorten`, `/:code`,
`/stats/:code`), three operational routes (`/healthz`, `/readyz`,
`/metrics`), and four chaos levers (`/_chaos/leak`, `/_chaos/cpu`,
`/_chaos/crash`, `/_chaos/recover`).

Important details:
- **Tight DB pool:** `DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=5`. Under load
  this can exhaust, which is intentional — it produces the kind of
  symptom AutoFixOps' rule engine *doesn't* recognize, so the AI fallback
  has something to chew on.
- **Tight resource limits:** 192 MiB / 500m CPU. A single 50 MB leak
  call puts you over 80% and trips `TargetAppMemoryLeak`.
- **Liveness probe** points at `/healthz` (cheap), **readiness probe**
  points at `/readyz` (DB + Redis dependent). When Postgres goes down,
  pods become NotReady but don't restart — that's the realistic UX.

### `worker` — click aggregator

Reads from the Redis stream `clicks` (consumer group `click-agg`),
batches into in-memory counters, flushes batched updates to Postgres.

- Has its own `/metrics` server on port 9100 (prometheus_client default).
- `LEAK_RATE_KB` env: when >0, every batch appends garbage to a list.
  This is how you demo a *non-api* leak.
- Pool size 2 / overflow 2 — the worker should never need many connections.

### `postgres` — single-node StatefulSet

One replica, 256 MiB / 500m CPU, 1 Gi PVC. Holds the `links` table.
Credentials are in the `postgres-creds` Secret.

### `redis` — single-node StatefulSet

One replica, 96 MiB hard cap with `allkeys-lru` eviction. Used as both
the cache and the click event stream. The eviction policy means a
sustained burst of links can evict cache entries, increasing
`shortener_clicks_total{hit="cache_miss"}`.

## Failure surface (what can go wrong)

| Symptom | Trigger | Alert that fires | AutoFixOps action |
|---|---|---|---|
| Memory > 85% of limit | `POST /_chaos/leak?mb=80` | `TargetAppMemoryLeak` | `INCREASE_MEMORY_LIMIT` (rule engine) |
| CPU > 80% of limit, sustained | `POST /_chaos/cpu` | `HighCPUUsage` | `RESTART_POD` (rule engine) |
| ≥3 restarts in 5 min | `POST /_chaos/crash` x 3 | `PodCrashLooping` | `ROLLBACK_DEPLOYMENT` (rule engine) |
| 5xx rate > 5% | Hit `/shorten` while Postgres is down | `HighErrorRate` | escalate (AI fallback) |
| p95 latency > 1s | DB pool exhaustion under load | `SlowResponseTime` | escalate (AI fallback) |
| Pod NotReady > 5m | Redis or Postgres down | `PodNotReady` | escalate (AI fallback) |

## Why these specific alert names

AutoFixOps' deterministic rule engine (`engine/baseline.py` in that repo)
has hard-coded handlers for **`HighCPUUsage`**, **`TargetAppMemoryLeak`**,
and **`PodCrashLooping`**. Anything else falls through to the LLM.
Using these names on the deterministic alerts means demos run with
**zero LLM cost** until you exercise an unrecognized symptom. That's
also how a real production install should be configured.

## Resource limits — why so tight?

Realistic test demands realistic pressure. With 192 MiB on the api,
a single `make inject-leak` (50 MB) is enough to push working-set memory
above the 85% alert threshold within a few seconds. Loosening the limits
would make demos take minutes instead of seconds and would make the
`MEMORY_LEAK_OOM_RISK → INCREASE_MEMORY_LIMIT → 200 MiB` patch arithmetic
look arbitrary. As-is, the patch arithmetic is exactly what an SRE
would do by hand.

## What we deliberately did *not* build

- **Authentication.** This is a test target, not a production service.
- **Multi-region / HA.** One replica of each stateful component on purpose.
- **TLS.** Adds friction to local demos. Production would terminate at the ingress.
- **Migrations framework.** Schema is one table; created on startup.
- **Distributed tracing.** Useful but orthogonal to AutoFixOps' job.
- **Sophisticated chaos engineering.** Litmus / Chaos Mesh would be
  overkill here; four HTTP knobs cover every failure AutoFixOps reasons about.

## What you can extend

- Swap Postgres for CockroachDB if you want a network-partition demo.
- Add a third service (e.g. a `notifier` that sends webhooks on click)
  to make the target resolver pick between three deployments instead of two.
- Replace the leak with a slow goroutine-style leak (Python doesn't
  do goroutines, but a never-cleared `asyncio.Queue` works).
