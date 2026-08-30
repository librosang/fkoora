# Deploying Fkoora (match-center) with Docker

Production topology for this project is five containers:

```
Internet
   │
Cloudflare (DNS + tunnel)
   │
cloudflared (host) ──▶ localhost:80
   │
Nginx Proxy Manager (:80/:443, container `nginxproxymanager`)
   │
   ├── fkoora.site / www.fkoora.site ──▶ fkoora-frontend :3000   (Next.js)
   │                                         │  server-side proxy
   │                                         ▼
   └── (optional) api.fkoora.site ──────▶ fkoora-api :9000        (Flask, read-only)
                                              │    │                │ refresh_jobs /
                                              │    │ Redis hits     │ competition_views
                                              │    ▼                ▼
                                              │ fkoora-redis   fkoora-postgres :5432
                                              │ (response        ▲         ▲
                                              │  cache)          │ SQL     │ invalidations
                                              └──────────▶ fkoora-worker (scheduler + job
                                                            queue, talks to goal.com,
                                                            DELs cache keys on write)
```

**Why api.fkoora.site is optional:** the Next.js server proxies every
`/api/*` request (data *and* images) to the Flask API over the Docker
network (`FOOTBALL_API_BASE=http://fkoora-api:9000`). The browser only ever
talks to the Next.js server, so the API never has to be reachable from the
Internet. Expose `api.fkoora.site` through NPM only if you want it for
debugging/monitoring.

**API and scraper are SPLIT into two processes** (one image, two
containers): `fkoora-api` is a pure database reader - it serves the
frontend and never talks to goal.com. When it notices missing/empty/stale
data it serves what it has, flags `refreshing: true` and writes a
`refresh_jobs` row. `fkoora-worker` (SERVICE_ROLE=worker) picks those rows
up within seconds, runs the freshness scheduler (today's listings every
60 s, neighbouring days adaptively every 5/30 min, live match details every
2 min, slow backfill, event-driven standings refresh ~1 min after the
final whistle) and is the ONLY process that talks to goal.com. The two
never share memory - they coordinate entirely through two tiny tables
(`refresh_jobs`, `competition_views`), which is what makes the split safe:
the API can restart, scale or crash without touching scraper state, and
vice versa. Run several worker containers if you like - they elect
exactly ONE leader via a PostgreSQL advisory lock (`SCHEDULER_ROLE=auto`,
the default); the others stand by and take over automatically.

**Legacy single container:** `SERVICE_ROLE=all` runs a background worker
next to gunicorn in ONE container (the pre-split behavior) if you do not
want separate containers. The default role is `api` (read-only).

**Traffic discipline:** JSON endpoints serve conditional responses (strong
ETag → `304` when unchanged, forwarded by the Next.js proxies) **and** a
shared Redis response cache sits in front of PostgreSQL (one Redis GET per
hit; the worker deletes the affected keys the moment fresh data lands, so
a hit is never a stale score). Images are disk-cached + memory-LRU'd and
revalidate with `304`s, and the worker fetches Arabic pages on slow
10-minute cycles (names change a few times per season; EN pages carry the
minute-by-minute scores). Together these keep both goal.com traffic,
database load and user-facing bandwidth flat as usage grows.

**No manual DB setup:** the schema is applied idempotently on every start
(`CREATE TABLE IF NOT EXISTS` in `scraper/db/schema.sql`). A fresh volume just
works; the first worker tick (within a minute) starts filling it.

---

## 1. Server layout

Put the repository next to your main `docker-compose.yml`:

```
/srv/compose/                 (wherever your docker-compose.yml lives)
├── docker-compose.yml        (your infra + the fkoora-* services)
│                              └─ adopt docker-compose.fkoora-full.yml:
│                                 YOUR stack verbatim, fkoora block migrated
├── nginx-proxy-manager/  mysql/  immich/  oscam/  tauri-portal/
├── postgres-data/            (fkoora PostgreSQL volume - keep as is!)
└── fkoora/                   <- this repo
    ├── Dockerfile.api          (Flask API + worker image - ONE image)
    ├── Dockerfile.frontend     (Next.js image)
    ├── docker-compose.yml           (standalone: whole stack on loopback,
    │                                 for isolated local testing)
    ├── docker-compose.fkoora-full.yml (your exact stack merged: + worker,
    │                                 + fkoora-redis, api made read-only)
    ├── scraper/                 (Python package: API + worker + schema)
    ├── src/                     (Next.js app)
    ├── package.json, bun.lock
    └── DEPLOY.md                (this file)
```

## 2. Add the services to your compose

The shipped `docker-compose.fkoora-full.yml` is **your actual stack**
(NPM, MariaDB, OpenLiteSpeed, tauri-portal, adminer, redis, Immich, oscam
- all verbatim) with ONLY the fkoora block migrated. Diff it against your
live `docker-compose.yml`, then either copy it over or copy just the
`fkoora-*` services into yours. The fkoora part of it, for reference:

```yaml
  fkoora-postgres:            # UNCHANGED - same volume, same loopback port
    image: postgres:16-alpine
    container_name: fkoora_postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: fkoora
      POSTGRES_USER: fkoora
      POSTGRES_PASSWORD: fuckkoora
    volumes:
      - ./postgres-data:/var/lib/postgresql/data
    ports:
      - "127.0.0.1:5433:5432"
    networks:
      - webnet
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U fkoora -d fkoora"]
      interval: 10s
      timeout: 5s
      retries: 5

  fkoora-redis:               # NEW - dedicated response cache (like immich-redis)
    image: redis:7-alpine
    container_name: fkoora_redis
    restart: unless-stopped
    command: ["redis-server", "--maxmemory", "256mb",
              "--maxmemory-policy", "allkeys-lru",
              "--save", "", "--appendonly", "no"]
    networks:
      - webnet
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

  fkoora-api:                 # CHANGED - read-only role + REDIS_URL
    build:
      context: ./fkoora
      dockerfile: Dockerfile.api
    container_name: fkoora_api
    restart: unless-stopped
    environment:
      SERVICE_ROLE: api                       # read-only API (the default)
      FOOTBALL_DB_URL: postgresql://fkoora:fuckkoora@fkoora-postgres:5432/fkoora
      REDIS_URL: redis://fkoora-redis:6379/0  # shared response cache
    volumes:
      - ./fkoora/img_cache:/app/img_cache     # crest/logo cache (shared w/ worker)
    networks:
      - webnet
    depends_on:
      fkoora-postgres:
        condition: service_healthy
      fkoora-redis:
        condition: service_healthy

  fkoora-worker:              # NEW - the ONLY process talking to goal.com
    build:
      context: ./fkoora
      dockerfile: Dockerfile.api              # same image as the API
    container_name: fkoora_worker
    restart: unless-stopped
    environment:
      SERVICE_ROLE: worker                    # scheduler + refresh_jobs consumer
      FOOTBALL_DB_URL: postgresql://fkoora:fuckkoora@fkoora-postgres:5432/fkoora
      REDIS_URL: redis://fkoora-redis:6379/0  # invalidates the cache on write
      # BOOTSTRAP_ON_START: "1"               # one-time historical walk
    volumes:
      - ./fkoora/img_cache:/app/img_cache     # shared with the API (pre-warm)
    networks:
      - webnet                                # no ports: never serves HTTP
    depends_on:
      fkoora-postgres:
        condition: service_healthy
      fkoora-redis:
        condition: service_healthy

  fkoora-frontend:            # UNCHANGED
    build:
      context: ./fkoora
      dockerfile: Dockerfile.frontend
    container_name: fkoora_frontend
    restart: unless-stopped
    environment:
      FOOTBALL_API_BASE: http://fkoora_api:9000
    ports:
      - "3000:3000"
    networks:
      - webnet
    depends_on:
      fkoora-api:
        condition: service_healthy
```

Notes:

* **Diff-first:** everything outside the `fkoora-*` services in the shipped
  file is byte-identical to your current compose. The only changes are the
  `fkoora-api` environment (read-only role + `REDIS_URL`) and the two new
  services (`fkoora-worker`, `fkoora-redis`).
* **No data migration:** `./postgres-data` and `./fkoora/img_cache` are
  reused verbatim; the two coordination tables (`refresh_jobs`,
  `competition_views`) create themselves on first start.
* **Your shared `redis` service:** the default here is a dedicated
  `fkoora-redis` (same pattern as your `immich-redis`) so its `allkeys-lru`
  eviction can never evict another app's keys. To reuse the shared instance
  instead, delete `fkoora-redis` and set
  `REDIS_URL: redis://redis:6379/0` on api + worker - fkoora keys are
  namespaced `fk:api:v1:*` and all carry TTLs, so they coexist safely; just
  do **not** add `allkeys-lru` to the shared instance itself.
* Keep `POSTGRES_PASSWORD` and both `FOOTBALL_DB_URL`s in sync (they
  already are in the shipped file).
* `fkoora-worker` publishes no ports at all - it never serves HTTP, it only
  scrapes. Prefer it over stacking `SERVICE_ROLE=all` on the api container:
  separate restarts, separate logs, scraper crashes never take the API down.
* `REDIS_URL` must be set on **both** `fkoora-api` and `fkoora-worker`:
  the API caches responses under the keys, the worker drops them when new
  data lands. Omit it on both to run cache-less (the API then reads
  PostgreSQL on every request - still correct, just more DB work).
* Host ports stay as they were: frontend `3000:3000`, postgres
  `127.0.0.1:5433:5432`; `fkoora-api` (internal :9000) and `fkoora-redis`
  publish nothing. NPM can also forward straight to `fkoora-frontend:3000`
  over webnet if you ever want the host port gone. For isolated local
  testing use the repo's own `docker-compose.yml` (loopback ports).
* This PostgreSQL is dedicated to Fkoora. Do **not** point it at MariaDB or
  at the Immich `immich-db` container - separate apps, separate databases.

## 3. First start

```bash
# from the directory holding your (merged) docker-compose.yml:
docker compose up -d --build fkoora-redis fkoora-api fkoora-worker
docker compose logs -f fkoora-worker     # schema applies, scheduler starts
docker compose logs -f fkoora-api        # read-only API serving
```

`fkoora-postgres` and `fkoora-frontend` keep their existing containers
(unchanged config); the one-time rebuild is needed because
`requirements.txt` gained the `redis` package. If you adopted the whole
merged file, a plain `docker compose up -d --build` works too - it leaves
every non-fkoora service untouched.

Within ~1 minute the worker scrapes today's listings; give it a few
minutes for details of big competitions. To force a full day immediately:

```bash
docker compose exec fkoora-worker python -m scraper.cli date 2026-08-29
# backfill a range / fetch details:
docker compose exec fkoora-worker python -m scraper.cli backfill --from 2026-08-01 --to 2026-08-28
docker compose exec fkoora-worker python -m scraper.cli enrich
```

Sanity checks:

```bash
docker compose exec fkoora-worker python -m scraper.cli stats
curl http://fkoora-api:9000/api/health    # from any container on webnet
                                          # ("cache" shows redis connectivity)
```

## 4. Nginx Proxy Manager

Create the proxy host(s):

| Field | Value |
|---|---|
| Domain | `fkoora.site`, `www.fkoora.site` |
| Scheme | `http` |
| Forward host | `fkoora-frontend` |
| Forward port | `3000` |
| Websockets | on (harmless) |
| Block common exploits | on |

Optional API host (debug only):

| Field | Value |
|---|---|
| Domain | `api.fkoora.site` |
| Forward host | `fkoora-api` |
| Forward port | `9000` |

Enable "Request a new SSL certificate" + Force SSL on each host.

## 5. Cloudflare Tunnel

Keep the tunnel pointing at NPM only (it already does). Add the hostnames:

```yaml
ingress:
  - hostname: fkoora.site
    service: http://localhost:80
  - hostname: www.fkoora.site
    service: http://localhost:80
  # optional, only if you exposed api.fkoora.site in NPM:
  - hostname: api.fkoora.site
    service: http://localhost:80
  # ... your other domains ...
  - service: http_status:404
```

(`localhost:80` because cloudflared runs on the host; Docker DNS names like
`fkoora-frontend` are not resolvable from the host network.)

DNS records for the three hostnames: CNAME to the tunnel (`<tunnel-id>.cfargotunnel.com`), proxied.

## 6. Environment variables reference

**fkoora-api** (read-only API)

| Variable | Default | Meaning |
|---|---|---|
| `SERVICE_ROLE` | `api` | `api` (read-only) \| `worker` \| `all` (legacy both-in-one) |
| `FOOTBALL_DB_URL` | `postgresql://localhost:5432/football` | PostgreSQL DSN |
| `REDIS_URL` | *(empty = cache off)* | shared response cache, e.g. `redis://fkoora-redis:6379/0` |
| `API_CRON_SECRET` | *(empty = open)* | protects `/api/cron/refresh` |
| `IMG_CACHE_DIR` | `/app/img_cache` | crest/logo disk cache (mount a volume) |
| `IMG_MEM_CACHE_MB` | `128` | in-memory hot image LRU |
| `DB_POOL_MIN` / `DB_POOL_MAX` | `1` / `8` | per-process PostgreSQL pool |
| `COMPETITION_TTL_SEC` | `1800` | staleness window for standings/rounds |
| `ON_DEMAND_RETRY_SEC` | `600` | re-request window for failed data-gap jobs |
| `API_CACHE_TTL_*` | see `scraper/apicache.py` | per-endpoint cache TTLs (`LISTING_TODAY/PAST/FUTURE`, `MATCH_LIVE/DONE/UPCOMING`, `COMPETITION`, `TEAM`, `PLAYER`, ...) |
| `IMAGE_PROXY_SECRET` | built-in default | HMAC key for image tokens |

**fkoora-worker** (scraper)

| Variable | Default | Meaning |
|---|---|---|
| `SERVICE_ROLE` | `worker` | must be `worker` here |
| `FOOTBALL_DB_URL` | same as API | PostgreSQL DSN |
| `REDIS_URL` | *(empty = no invalidation)* | same value as the API - enables cache invalidation after every scrape |
| `SCHEDULER_ROLE` | `auto` | `auto` \| `force` \| `off` (leader election; `off` = jobs only) |
| `WORKER_POLL_SEC` | `4` | refresh_jobs poll cadence |
| `REFRESH_TODAY_SEC` / `REFRESH_AROUND_SEC` / `ENRICH_LIVE_SEC` / `ENRICH_BACKFILL_SEC` | 60 / 300 / 120 / 1800 | scheduler intervals (0 = off) |
| `AR_LISTING_SEC` / `AR_DETAIL_SEC` | 600 / 600 | slow Arabic name cycles |
| `COMP_REFRESH_SEC` / `COMP_VIEW_TRACK_SEC` | 1800 / 21600 | viewed-leagues warm cycle |
| `MAX_JOB_ATTEMPTS` | `3` | retries before a failing job is retired |
| `BOOTSTRAP_ON_START` | `0` | one-time historical walk on container start |

**fkoora-frontend**

| Variable | Default | Meaning |
|---|---|---|
| `FOOTBALL_API_BASE` | `http://127.0.0.1:9000` | Flask API base URL (**server-side**, container-to-container) |
| `CRON_SECRET` | *(empty = open)* | must match `API_CRON_SECRET` |

If you set `API_CRON_SECRET`, an external warmer can still call
`https://fkoora.site/api/cron/refresh` with the secret - since the split it
just enqueues a refresh job for the worker (or skip it entirely: the
worker's scheduler is the default freshness source).

### 6.1 The shared Redis response cache

`REDIS_URL` turns on a cache-aside layer in front of PostgreSQL
(`scraper/apicache.py`). What it buys you:

* **Database off the hot path** - a cache hit costs ONE Redis GET instead
  of the endpoint's SQL query chain; a poll with a matching `If-None-Match`
  answers `304` without even parsing the payload. (ETags alone only saved
  bandwidth - the server still built every payload before comparing tags.)
* **Shared + restart-proof** - one cache for every gunicorn worker and
  every API replica; a deploy or crash does not lose it (unlike a
  per-process cache).
* **Never stale** - the worker deletes the affected keys the moment new
  data lands (listing scrape -> that day's listing keys, detail fetch ->
  the match key, standings refresh -> the competition keys, profile fetch
  -> the player key). TTLs are only the safety net for when the worker is
  down.
* **Degrade-safe** - Redis unavailable? Every request falls back to plain
  database reads (one rate-limited warning per 5 min in the log, retry
  every 60 s). `/api/health` reports
  `"cache": {"enabled": ..., "connected": ...}`.

Defaults per endpoint (override with `API_CACHE_TTL_*`): day listing today
15 s / past days 1800 s (finished scores are immutable) / future 300 s;
match detail live 15 s / finished-with-details 3600 s / upcoming 300 s;
standings + rounds 60 s; team 300 s; player profile 3600 s. A payload
carrying `refreshing: true` is always cached for only 15 s (the worker
also drops the key when the fill lands).

The shipped Redis is a pure cache: 256 MB, `allkeys-lru`, nothing written
to disk - losing it just means entries get rebuilt from PostgreSQL.

## 7. Day-2 operations

**Backup** (cron it):

```bash
docker compose exec -T fkoora-postgres \
  pg_dump -U fkoora -d fkoora --no-owner \
  | gzip > /backups/fkoora-$(date +%F).sql.gz
```

**Restore:**

```bash
gunzip -c /backups/fkoora-2026-08-29.sql.gz \
  | docker compose exec -T fkoora-postgres psql -U fkoora -d fkoora
```

**Update to a new version of the code:**

```bash
cd /srv/compose/fkoora && git pull          # or copy the new files
sh scripts/remove-legacy-seo-files.sh       # only needed when copying/unzipping over an older drop
docker compose build fkoora-api fkoora-worker fkoora-frontend
docker compose up -d fkoora-api fkoora-worker fkoora-frontend
```

> **⚠ Upgrading by unzipping over an older tree?** Zip extraction ADDS and
> OVERWRITES files but never DELETES files that the new version removed —
> and leftovers break the Next.js build with `Conflicting route and
> metadata/page` errors. The v4 SEO restructure removed these three files
> (replaced by runtime route handlers):
>
> | Removed in v4 | Replaced by |
> |---|---|
> | `src/app/robots.ts` | `src/app/robots.txt/route.ts` |
> | `src/app/sitemap.ts` | `src/app/sitemap.xml/route.ts` + `src/app/sitemaps/[name]/route.ts` |
> | `src/app/match/[id]/page.tsx` | `src/app/match/[id]/route.ts` + `src/app/match/[id]/[slug]/page.tsx` |
>
> `sh scripts/remove-legacy-seo-files.sh` deletes them if present (safe to
> run anytime); a plain `git pull` handles removals automatically.

### 7.1 Bilingual SEO URLs (v6) — how the language routing works

Every match AND competition now has TWO canonical URLs — one per language —
and the slug itself carries the language:

```
/match/<id>/chelsea-vs-brighton-hove-albion        English page
/match/<id>/تشيلسي-ضد-برايتون-اند-هوف-البيون        Arabic page (site default)

/competition/<id>/premier-league                   English standings page
/competition/<id>/الدوري-الانجليزي-الممتاز          Arabic standings page
```

Rules worth knowing when operating the site:

- **Legacy no-slug URLs still work**: `/match/<id>` and `/competition/<id>`
  answer with a real HTTP **308** to the slug URL of the requested language
  (`?lang=en` → the English slug, default → the Arabic slug). The `Location`
  header is relative, so it never leaks an internal hostname.
- **Both languages are indexed**: each variant is self-canonical, the two
  reference each other via `hreflang` (`ar`, `en`, `x-default`), and BOTH are
  listed in the sitemaps (`/sitemaps/matches-N.xml` two URLs per match,
  `/sitemaps/competitions-N.xml` two per competition).
- **When a team/competition has no Arabic name** the Arabic slug is built from
  the Latin name, both language slugs collide, and the English variant moves
  to the shared URL with `?lang=en` (canonical consolidates them cleanly).
- **Rich results work in both languages**: every page ships JSON-LD with
  `inLanguage` set to its own language — `SportsEvent` (name, startDate,
  location, eventStatus, image, url, description) on match pages and inside
  `ItemList`s on the home/competition pages, plus `BreadcrumbList`. Validate
  after deploy at <https://search.google.com/test/rich-results> with one URL
  per language.
- **`?lang=en` / `?lang=ar` still override** the content language on any URL
  (backwards compatibility); the canonical tag then consolidates the variant
  onto the right slug URL.
- Arabic slugs are normalized (hamza-alef → plain alef, no tashkeel), so
  `/تشيلسي-ضد-...` and a hand-typed `/تشیلی...` variant still land on the
  canonical page via redirect.

**Local SEO verification** (uses the mock backend, no Docker needed):

```bash
node scripts/mock_backend.js &                                     # :9000
bun run build
PORT=3100 FOOTBALL_API_BASE=http://127.0.0.1:9000 SITE_URL=https://fkoora.site \
  node .next/standalone/server.js &                                # :3100
bash scripts/smoke_test.sh     # 67 checks: bilingual slugs, 308s, sitemaps,
                               # JSON-LD rich results in BOTH languages
```

**Logs:**

```bash
docker compose logs -f fkoora-worker        # scraper scheduler + job queue
docker compose logs -f fkoora-api           # read-only API
docker compose logs -f fkoora-frontend      # Next.js server
```

**Pre-warm the crest cache after a restore/fresh start:**

```bash
docker compose exec fkoora-api python -m scraper.cli cache-images
```

## 8. Troubleshooting

* **`fkoora_frontend` restarts / 500s** - check `FOOTBALL_API_BASE` - it must
  point at the API container with **no trailing slash** (`http://fkoora_api:9000`
  or `http://fkoora-api:9000` both resolve on webnet; never `api.fkoora.site`).
* **Empty match days** - the worker needs a few minutes after the very
  first start; force it with `python -m scraper.cli date <today>` (see §3).
* **Data stopped updating after the migration** - the API no longer runs a
  scheduler. `docker compose ps fkoora-worker` must show it Up, and its log
  should tick every ~60 s (`docker compose logs -f fkoora-worker`). Running
  the api container alone makes the entrypoint print a loud warning at boot.
* **`fkoora-api` unhealthy at boot** - wrong `POSTGRES_PASSWORD` (must match
  in both places) or the postgres volume was created with a different
  password; check `docker compose logs fkoora-postgres`.
* **Redis down / cache degraded** - the API keeps working (plain database
  reads); `/api/health` shows `"cache": {"connected": false, ...}` and the
  log has a rate-limited warning. `docker compose logs fkoora-redis` /
  `docker compose restart fkoora-redis` - the API reconnects within 60 s.
* **Scores look delayed after adding Redis** - make sure `REDIS_URL` is set
  on the WORKER container too; without it nothing invalidates the API's
  cached entries and freshness falls back to the TTLs (15 s for live days,
  60 s for standings).
* **Port clash** - only `fkoora-frontend` (`3000:3000`) and
  `fkoora-postgres` (`127.0.0.1:5433:5432`) publish host ports; the API's
  internal :9000 and `fkoora-redis` conflict with nothing. If host 3000 is
  taken, drop the frontend `ports:` mapping and let NPM forward straight to
  `fkoora-frontend:3000` over webnet.
* **Standalone testing** of just this stack (own network, loopback ports):
  `cd fkoora && docker compose up -d --build`, then open `http://127.0.0.1:3000`.
