# Fkoora Live Architecture — Implementation Summary

This documents the implementation of *"Fkoora — Match Data, PostgreSQL,
Redis & Live Update Implementation"* on the existing match-center codebase,
following its prescribed phase order (audit → database → query layer →
Redis → worker → SSE → frontend). The architecture is the spec's target:

```
FOOTBALL PROVIDER
      │ adaptive polling (live/upcoming/idle)
      ▼
 Match Worker ── detect changes ──▶ PostgreSQL (source of truth) ── COMMIT
      │                                                          │
      │                    only after commit: scraper/live.py    │
      │                    ├─ Redis hot cache  fk:live:v1:*      │
      │                    ├─ Pub/Sub          fk:events:v1:matches
      │                    └─ bounded replay log (Last-Event-ID) │
      ▼                                                          ▼
 SSE service (ONE Redis subscription per process)          Redis response cache
      │ fan-out                                                  │ (apicache)
      ▼                                                          ▼
 Browsers (EventSource, delta events, per-match patching)     API JSON endpoints
```

## What changed, file by file

| File | Change |
|---|---|
| `scraper/db/schema.sql` | Native **TIMESTAMPTZ/DATE** column types for fresh databases; `matches.data_version BIGINT` counter; new indexes: `idx_matches_date_kickoff`, `idx_matches_competition_date_kickoff`, **partial** `idx_matches_live` (`status='LIVE'`), **partial** `idx_matches_upcoming` (`status='FIXTURE'`), `idx_matches_home/away_team_kickoff` (DESC), `idx_match_events_match_sort`. lineups/standings deliberately NOT re-indexed (PK/UNIQUE already cover them — no over-indexing). |
| `scraper/db/migrate.py` (NEW) | Safe, idempotent, **reversible** in-place TEXT→TIMESTAMPTZ/DATE migration: value validation against the ISO patterns the app writes, `kickoff_utc`↔`match_date` cross-repair, empty-string→NULL, missing-offset→UTC, per-column transactions with `lock_timeout`, `--force`/`--revert`. Hooked into every start; also a CLI command. |
| `scraper/timeutil.py` (NEW) | `iso_z`/`parse_ts`: both string and datetime normalize to the unchanged wire format `"YYYY-MM-DDTHH:MM:SSZ"` — the API contract is byte-identical before/after the migration. |
| `scraper/db/queries.py` (NEW) | The query layer: one source for listing/live/competition/team/state SQL — no duplicated SQL across routes. Every query maps to a specific index (verified with `EXPLAIN (ANALYZE, BUFFERS)`). |
| `scraper/db/database.py` | **Change detection**: `upsert_match_from_listing` and `apply_match_detail` compare the fields clients can see (COALESCE semantics) and bump `data_version` *only on real change*; unchanged provider responses never register. `db.changed_matches` / `db.new_match_events` are drained by the worker *after commit*. New events are diffed by identity (type/minute/player) for `match.event` SSE messages. |
| `scraper/live.py` (NEW) | Redis live layer: hot cache (`fk:live:v1:list`, per-match keys), monotonic event ids (`seq`), **bounded replay log** for SSE reconnection, `publish_changed()` (post-commit; re-reads committed state, **skips rolled-back writes** via a version guard), `get_live()` (Redis→PostgreSQL fallback), degrade-safe without `REDIS_URL`. Key naming follows the project's existing `fk:` convention. |
| `scraper/sse.py` (NEW) | `LiveEventBus`: **ONE Redis Pub/Sub subscription per process** fanning out to every connected browser (slow clients dropped, they re-sync via snapshot); heartbeat formatting, `Last-Event-ID` replay, on-connect `live.snapshot`; **DB-poll fallback mode** (one query per process per `SSE_POLL_SEC`) when Redis is absent — never per-browser work, never the provider. |
| `scraper/api.py` | New endpoints **`GET /api/matches/live`** (Redis hit = one GET, ETag/304, informational `stale` flag) and **`GET /api/events/live`** (SSE with `retry:`, snapshot, replay, heartbeats, `X-Accel-Buffering: no`). **Single-flight** cache-stampede locks on the listing/detail miss paths. Health gains cache/live/sse/build counters. Timestamps serialized via `iso_z`. |
| `scraper/worker.py` | **Adaptive provider polling**: `PROVIDER_POLL_LIVE/UPCOMING/IDLE_SECONDS` (+lookahead, `ADAPTIVE_POLL=0` → legacy fixed cadence), recomputed per tick from index-backed probes. **UTC midnight transition** handled in-process (immediate new-day listing + cadence reset). Every scrape path publishes live updates after `db.close()` (commit). |
| `scraper/db/backend.py` | `run_script` executes schema statements per-transaction with `SCHEMA_LOCK_TIMEOUT_SEC` — startup can no longer hang behind an idle/long transaction (a real lock-up found in testing). |
| `scraper/apicache.py` | Hit/miss/invalidation counters for observability. |
| `scraper/cli.py` | `python -m scraper.cli migrate-types [--force|--revert]`. |
| `src/app/api/events/live/route.ts` (NEW) | SSE **streaming pass-through** proxy: upstream body piped unbuffered, `Last-Event-ID` forwarded, `req.signal` aborts upstream on browser disconnect. |
| `src/app/api/matches/live/route.ts` (NEW) | Live-list proxy (ETag/304 forwarded). |
| `src/lib/goal/sse.ts` (NEW) | `LiveMatchStream` (EventSource wrapper with failure counting → HTTP fallback) + `applyMatchDelta` (patch only the affected row). |
| `src/components/mc/home-client.tsx` | Subscribes on the today view; applies `match.updated` deltas to **only the affected match** (version-guarded against out-of-order events); HTTP polling relaxes to a 5-minute safety net while SSE is open, returns to 60 s live polling on stream failure; live status indicator in the toolbar. |
| `.env.example`, `README.md`, `README.scraper.md`, `docker-compose.fkoora-full.yml`, `docker-compose.yml` (NEW), `docker-entrypoint.sh`, `Dockerfile.api`, `docker-healthcheck.py` (NEW) | All new knobs documented; gunicorn threads default 8→32 (one thread per SSE client; gevent note for thousands of clients). Docker compatibility hardening — see the Docker section below. |

## Deliberate design decisions

* **`match_date` remains the UTC date derived from kickoff** — the API keeps
  computing local-day windows in SQL (the spec explicitly allows this).
* **The day cache stays the existing `apicache` listing cache** (per-tz
  variants, worker-driven invalidation, TTL by day type) instead of a second
  `fkoora:matches:day:*` scheme — the spec says to keep the existing key
  convention and not duplicate mechanisms. The live hot cache (`fk:live:v1`)
  is the genuinely new layer.
* **No WebSocket** — SSE covers the server→browser requirement.
* **Statuses are the provider's real vocabulary** (`FIXTURE/LIVE/RESULT/
  AET/PEN/CANCELLED`); the partial indexes use exactly those.
* **Provider failures never look like "no matches"**: the live payload keeps
  serving last-known state with an internal `stale` flag; a failed scrape
  publishes nothing (version guard).

## Verification (all green, sandbox: real PostgreSQL 16 + real Redis 7.2)

| Suite | What it proves |
|---|---|
| `scripts/test_migration.py` | legacy TEXT database → 25 columns converted; edge values (no-offset, empty, invalid) repaired; idempotent; revert restores byte-identical wire format |
| `scripts/seed_test_data.py` + `scripts/verify_queries.py` | 10,764 matches / 100 live; **7/7 query paths use the intended indexes, zero seq scans on matches** (EXPLAIN ANALYZE, BUFFERS) |
| `scripts/test_live_flow.py` (30 checks) | unchanged re-scrape bumps nothing; changed score bumps once; post-commit publish (list + channel + log + monotonic ids); **rollback guard**; live endpoint ETag/304; SSE snapshot → live event → Last-Event-ID replay → heartbeats; poll fallback mode |
| `scripts/test_detail_changes.py` (20 checks) | detail-side change detection: identical re-fetch no-op, new goal → one `match.event`, stats/period diffs, language-preserving refresh, JSON-safe payloads on native types |
| `scripts/test_http_integration.py` (13 checks) | real `python -m scraper.cli api` server: SSE over HTTP, nginx headers, 304 revalidation, **40 concurrent cold requests → exactly ONE SQL rebuild** (single-flight) |
| `scripts/test_fullstack_sse.py` (10 checks) | real Flask API + real Next.js server: SSE streams through the Next proxy unbuffered, deltas arrive intact, SSR renders |
| `scripts/test_worker_logic.py` (11 checks) | adaptive interval selection (idle 300 / upcoming 120 / live 60 / fixed fallback), cheap probes, post-commit drain, midnight transition resets |
| `bunx tsc --noEmit` + `bun run build` | frontend typechecks clean; both new routes build |

## Operating notes

* **Upgrade path for an existing deployment**: deploy the new code and
  restart api+worker — the type migration applies itself (validated,
  reversible via `migrate-types --revert`). Or run
  `python -m scraper.cli migrate-types` first during a quiet window.
* Set `REDIS_URL` on BOTH api and worker to enable the full live path;
  without it everything still works (SSE uses the DB-poll fallback).
* Behind nginx/NPM, SSE needs no special config — the API already sends
  `X-Accel-Buffering: no`; keep proxy read timeouts above your
  heartbeat interval (default 15 s).
* Frontend fallback: if SSE fails 3× consecutively the page drops the
  stream and returns to 60 s HTTP polling automatically (nothing breaks).

## Docker compatibility hardening

Verified end-to-end in this sandbox by running the *actual* entrypoint
against real PostgreSQL + Redis (no docker daemon available here — the
entrypoint, roles, gunicorn, healthcheck script and both compose files were
exercised directly):

* **`docker-entrypoint.sh`**: the file previously ended after the
  "starting gunicorn" echo — **the `exec gunicorn` line was missing**, so
  `SERVICE_ROLE=api` containers exited instantly (crash-loop). Now execs
  gunicorn (`scraper.wsgi:app`, gthread, graceful-timeout for open SSE
  streams); `timestamp()` moved above its first use; new
  `SERVICE_ROLE=migrate` one-shot role.
* **`docker-healthcheck.py` (NEW)** + `Dockerfile.api`: the old
  HTTP-only healthcheck reported every **worker** container unhealthy
  forever (it never listens on :9000). The healthcheck now dispatches on
  `SERVICE_ROLE`: real `GET /api/health` for api/all, PID-1 process check
  for worker/migrate.
* **Redis eviction policy**: `docker-compose*.yml` fkoora-redis uses
  **`volatile-lru`** (was `allkeys-lru`). Only TTL-carrying keys
  (`fk:api:v1:*` cache, `fk:live:v1:match:*` hot keys) are ever evicted;
  the TTL-less `fk:live:v1:seq`/`:log` keys SSE Last-Event-ID replay
  depends on survive memory pressure untouched.
* **`fkoora-migrate` one-shot init service** in both compose files:
  idempotent schema + type migration, exits 0; api/worker gate on
  `service_completed_successfully` so they never race each other's ALTERs
  on an existing `./postgres-data` volume.
* **`docker-compose.yml` (NEW)** at the repo root — the standalone
  six-service stack (`docker compose up -d --build`, context `.`) that the
  README and `.env.example` always referenced.
* `stop_grace_period: 35s` on api/worker (gunicorn drains in-flight
  requests + SSE for 30 s); `.dockerignore`/`.gitignore` gain
  `postgres-data` (the bind volume the new compose creates must never
  enter a build context); SSE-through-NPM notes documented in both files.

Live validation (sandbox, real processes):

| Check | Result |
|---|---|
| `SERVICE_ROLE=migrate` entrypoint run | schema applied, report printed, exit 0 |
| `SERVICE_ROLE=api` entrypoint run | gunicorn up in 2 s, `/api/health` 200, app logs visible |
| SSE through gunicorn | 200, `text/event-stream`, `X-Accel-Buffering: no` |
| Full loop (worker + api + stream) | worker published 841 events → browser received 841 `match.updated` + 1 `live.snapshot` |
| SIGTERM to gunicorn (docker stop) | graceful worker exit, master shutdown, no leftovers |
| healthcheck script | api → exit 0; worker (wrong PID 1) → exit 1 |
| `scripts/validate_compose.py` | both compose files: no duplicate keys, depends graph valid, contexts/dockerfiles exist, ports/conditions well-formed |
