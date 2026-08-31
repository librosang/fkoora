# Deploying Fkoora (match-center) with Docker

Production topology for this project is three containers:

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
   └── (optional) api.fkoora.site ──────▶ fkoora-api :8000        (Flask)
                                              │
                                              ▼
                                     fkoora-postgres :5432       (PostgreSQL 16)
```

**Why api.fkoora.site is optional:** the Next.js server proxies every
`/api/*` request (data *and* images) to the Flask API over the Docker
network (`FOOTBALL_API_BASE=http://fkoora-api:8000`). The browser only ever
talks to the Next.js server, so the API never has to be reachable from the
Internet. Expose `api.fkoora.site` through NPM only if you want it for
debugging/monitoring.

**Scale-out is now safe (scheduler leadership):** the API keeps the database
fresh with a built-in scheduler thread (today's listings every 60 s,
neighbouring days adaptively every 5 min / 30 min, live match details every
2 min, slow backfill). The scheduler elects exactly ONE leader across all
API processes via a PostgreSQL advisory lock (`SCHEDULER_ROLE=auto`, the
default): run several gunicorn workers or replicas and only one of them
scrapes — the others stand by and take over automatically if it dies, so
goal.com traffic never multiplies with your worker count. Prefer one worker
+ threads for a personal site (the workload is I/O bound); when you do scale
out, mind the per-process DB pool (`DB_POOL_MIN`/`DB_POOL_MAX`, default 1/8)
against PostgreSQL's `max_connections`. `API_ENABLE_SCHEDULER=0` (or
`SCHEDULER_ROLE=off`) still disables the scheduler entirely for external
`/api/cron/refresh`-driven setups. `GET /api/health` shows which process is
the current leader.

**Traffic discipline:** JSON endpoints serve conditional responses (strong
ETag → `304` when unchanged, forwarded by the Next.js proxies), images are
disk-cached + memory-LRU'd and revalidate with `304`s, and the scraper
fetches Arabic pages on slow 10-minute cycles (names change a few times per
season; EN pages carry the minute-by-minute scores). Together these keep
both goal.com traffic and user-facing bandwidth flat as usage grows.

**No manual DB setup:** the schema is applied idempotently on first start
(`CREATE TABLE IF NOT EXISTS` in `scraper/db/schema.sql`). A fresh volume just
works; the first scheduler tick (within a minute) starts filling it.

---

## 1. Server layout

Put the repository next to your main `docker-compose.yml`:

```
/srv/compose/                 (wherever your docker-compose.yml lives)
├── docker-compose.yml        (your infra + the fkoora-* services)
├── nginx-proxy-manager/
├── mysql/
├── ...
└── fkoora/                   <- this repo
    ├── Dockerfile               (Flask API image)
    ├── Dockerfile.frontend      (Next.js image)
    ├── docker-compose.yml       (standalone variant, for isolated testing)
    ├── scraper/                 (Python package: API + scraper + schema)
    ├── src/                     (Next.js app)
    ├── package.json, bun.lock
    └── DEPLOY.md                (this file)
```

## 2. Add the services to your compose

The complete merged file is shipped alongside this repo
(`docker-compose.fkoora-full.yml`). The fkoora part of it, for reference:

```yaml
  fkoora-postgres:
    image: postgres:16-alpine
    container_name: fkoora_postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: fkoora
      POSTGRES_USER: fkoora
      POSTGRES_PASSWORD: CHANGE_THIS_PASSWORD     # <-- change!
    volumes:
      - ./fkoora/postgres:/var/lib/postgresql/data
    networks: [webnet]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U fkoora -d fkoora"]
      interval: 10s
      timeout: 5s
      retries: 5

  fkoora-api:
    build: { context: ./fkoora, dockerfile: Dockerfile }
    container_name: fkoora_api
    restart: unless-stopped
    environment:
      FOOTBALL_DB_URL: postgresql://fkoora:CHANGE_THIS_PASSWORD@fkoora-postgres:5432/fkoora
    volumes:
      - ./fkoora/img_cache:/app/img_cache
    expose: ["8000"]
    networks: [webnet]
    depends_on:
      fkoora-postgres: { condition: service_healthy }

  fkoora-frontend:
    build: { context: ./fkoora, dockerfile: Dockerfile.frontend }
    container_name: fkoora_frontend
    restart: unless-stopped
    environment:
      FOOTBALL_API_BASE: http://fkoora-api:8000
    expose: ["3000"]
    networks: [webnet]
    depends_on:
      fkoora-api: { condition: service_healthy }
```

Notes:

* `expose` (not `ports`) - NPM reaches the containers over `webnet`, nothing
  is published to the host/Internet. For isolated local testing use the
  repo's own `docker-compose.yml` instead (it maps `127.0.0.1:3000/8000`).
* Change `CHANGE_THIS_PASSWORD` in **both** places (postgres env +
  `FOOTBALL_DB_URL`) to the same long random string.
* This PostgreSQL is dedicated to Fkoora. Do **not** point it at MariaDB or
  at the Immich `immich-db` container - separate apps, separate databases.

## 3. First start

```bash
docker compose up -d --build fkoora-postgres fkoora-api fkoora-frontend
docker compose logs -f fkoora-api        # schema applies, scheduler starts
```

Within ~1 minute the scheduler scrapes today's listings; give it a few
minutes for details of big competitions. To force a full day immediately:

```bash
docker compose exec fkoora-api python -m scraper.cli date 2026-08-29
# backfill a range / fetch details:
docker compose exec fkoora-api python -m scraper.cli backfill --from 2026-08-01 --to 2026-08-28
docker compose exec fkoora-api python -m scraper.cli enrich
```

Sanity checks:

```bash
docker compose exec fkoora-api python -m scraper.cli stats
curl http://fkoora-api:8000/api/health    # from any container on webnet
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
| Forward port | `8000` |

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

**fkoora-api**

| Variable | Default | Meaning |
|---|---|---|
| `FOOTBALL_DB_URL` | `postgresql://localhost:5432/football` | PostgreSQL DSN |
| `API_ENABLE_SCHEDULER` | `1` | `0` disables the in-process scheduler |
| `API_CRON_SECRET` | *(empty = open)* | protects `/api/cron/refresh` |
| `IMG_CACHE_DIR` | `/app/img_cache` | crest/logo disk cache (mount a volume) |
| `IMAGE_PROXY_SECRET` | built-in default | HMAC key for image tokens |
| `REFRESH_TODAY_SEC` / `REFRESH_AROUND_SEC` / `ENRICH_LIVE_SEC` / `ENRICH_BACKFILL_SEC` | 60 / 300 / 120 / 1800 | scheduler intervals (0 = off) |

**fkoora-frontend**

| Variable | Default | Meaning |
|---|---|---|
| `FOOTBALL_API_BASE` | `http://127.0.0.1:8000` | Flask API base URL (**server-side**, container-to-container) |
| `CRON_SECRET` | *(empty = open)* | must match `API_CRON_SECRET` |

If you set `API_CRON_SECRET`, a good external warmer is a cron calling
`https://fkoora.site/api/cron/refresh` with the secret (expose
`api.fkoora.site` and use that URL, or run with the built-in scheduler like
above - the default - and skip this entirely).

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
docker compose build fkoora-api fkoora-frontend
docker compose up -d fkoora-api fkoora-frontend
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
docker compose logs -f fkoora-api           # API + scraper/scheduler
docker compose logs -f fkoora-frontend      # Next.js server
```

**Pre-warm the crest cache after a restore/fresh start:**

```bash
docker compose exec fkoora-api python -m scraper.cli cache-images
```

## 8. Troubleshooting

* **`fkoora_frontend` restarts / 500s** - check `FOOTBALL_API_BASE` is exactly
  `http://fkoora-api:8000` (no trailing slash, no `api.fkoora.site`).
* **Empty match days** - the scheduler needs a few minutes after the very
  first start; force it with `python -m scraper.cli date <today>` (see §3).
* **`fkoora-api` unhealthy at boot** - wrong `POSTGRES_PASSWORD` (must match
  in both places) or the postgres volume was created with a different
  password; check `docker compose logs fkoora-postgres`.
* **Port clash** - nothing in the fkoora stack publishes host ports, so the
  only possible clash is with your other `ports:` mappings (e.g. tauri-portal
  on 8000 is a *host* port - the API's internal 8000 does not conflict with
  it).
* **Standalone testing** of just this stack (own network, loopback ports):
  `cd fkoora && docker compose up -d --build`, then open `http://127.0.0.1:3000`.
