# Match Center — bilingual football app (scraper + database + API + frontend)

A complete pipeline that **scrapes goal.com (English + Arabic) into our own
PostgreSQL database** and serves it to a bilingual (EN/AR, RTL-aware) web
frontend. The frontend never scrapes anything — every row it renders comes out
of PostgreSQL through our Python API.

```
goal.com EN + AR ──scrape──▶  PostgreSQL (FOOTBALL_DB_URL) ◀── SQL reads
        (rate-limited,            idempotent                      │
         retries)                 upserts                        ▼
                                             Flask API  :8000   (scraper/api.py)
                                             /api/matches      day listings (local-tz correct)
                                             /api/match/<id>   events·lineups·stats (VAR included)
                                             /api/competition/<id>          standings + round list
                                             /api/competition/<id>/matches one round's results/fixtures
                                             /api/img?t=…      image proxy (URLs stay hidden)
                                             /api/cron/refresh scraping trigger
                                             + built-in scheduler (crons)
                                                      │
                                                      ▼  HTTP (server-side)
                                      Next.js frontend :3000  (src/)
                                      pure consumer — /api/* are thin proxies
                                      bilingual UI, RTL/LTR, live scores,
                                      VAR-annotated events, standings tables,
                                      round navigator, kooora-classic theme
```

## Repository layout

| Path | What it is |
|---|---|
| `scraper/` | Python package: `cli.py`, `config.py`, `http_client.py`, `parsers/goal.py`, `major.py` (major-leagues rules), `db/` (schema + upserts), `pipeline.py`, `api.py` (**JSON API + scheduler**), `wsgi.py` (gunicorn entry), `webapp.py` (standalone Flask web view) |
| `src/` | Next.js frontend: `app/` (pages + `/api` proxies), `components/mc/` (UI), `lib/goal/service.ts` (API client), `lib/i18n.ts` (EN/AR strings) |
| `examples/queries.sql` | ready-to-run analytical queries (psql) |
| `scripts/daemon.py` | double-fork daemon launcher (keeps background processes alive) |
| `Dockerfile` / `Dockerfile.frontend` | production images: gunicorn API (python:3.12-slim) and standalone Next.js (bun build → node run) |
| `docker-compose.yml` | the three-service stack (PostgreSQL + API + frontend) for isolated testing |
| `DEPLOY.md` | production deployment behind Nginx Proxy Manager / Cloudflare Tunnel |

## Run it (local development)

You need a PostgreSQL server (any 13+ works). Create an empty database once:

```bash
createdb football          # or: psql -c "CREATE DATABASE football"
```

Two processes:

```bash
# 1) backend: scraper + database + API + scheduler   (http://127.0.0.1:8000)
cd scraper/..                    # repository root (the scraper/ package lives here)
pip install -r requirements.txt
export FOOTBALL_DB_URL=postgresql://postgres:postgres@localhost:5432/football
python -m scraper.cli api --port 8000          # add --no-schedule for cron mode

# 2) frontend: Next.js dev server                    (http://localhost:3000)
cd ..
bun install                                    # or npm install
cp .env.example .env.local                     # FOOTBALL_API_BASE=http://127.0.0.1:8000
bun run dev
```

The schema is created automatically on first start — no migration step to run.
Open http://localhost:3000 — you get today's matches, results, fixtures, and
match dialogs with events (incl. VAR decisions), lineups and stats. There is
no refresh button: the view refreshes itself — every minute while matches are
live (or right after the next kickoff), every 30 minutes otherwise, and it
retries automatically if a load fails. The scheduler keeps the database fresh
by itself; see "Caching / cron strategy" below.

### First database fill

`python -m scraper.cli api` starts with whatever the database holds. To fill
it upfront:

```bash
python -m scraper.cli date 2026-08-26 --details     # one day + details
python -m scraper.cli backfill --from 2026-08-20 --to 2026-08-27 --details
python -m scraper.cli upcoming --days 7             # future fixtures
python -m scraper.cli standings --major             # league tables + all rounds (major comps)
python -m scraper.cli cache-images --days 12        # pre-warm the image cache
```

The API also **scrapes on demand**: navigating to a date that was never
scraped triggers the listing scrape for that day automatically (once per
date), opening a match whose details were never fetched fetches them
synchronously, and opening a competition's page (standings/rounds) scrapes
it on first request — then again whenever older than `COMPETITION_TTL_SEC`
(default 30 min).

## Deploy with Docker

The whole stack runs as three containers — PostgreSQL, Flask API (gunicorn,
one worker so the built-in scheduler runs exactly once) and the Next.js
frontend (standalone build). Quick start in isolation:

```bash
docker compose up -d --build      # postgres :5432, api :8000, frontend :3000
open http://127.0.0.1:3000
```

The schema is created automatically on first start; within a minute the
scheduler fills today's matches. For production behind Nginx Proxy Manager
/ Cloudflare Tunnel (fkoora.site → frontend, API stays internal because the
frontend proxies every `/api/*` call server-side), see **`DEPLOY.md`** —
including the full merged compose file, backup/restore and update recipes.

## Caching / cron strategy

The API server (`python -m scraper.cli api`) runs a scheduler thread that
keeps the database fresh without any external tooling:

| Job | Default interval | What it does |
|---|---|---|
| today listing | 60 s | refresh goal.com live-scores EN+AR (live scores, statuses) |
| neighbouring days | 300 s | yesterday's results + tomorrow's fixtures |
| live details | 120 s | re-fetch detail pages of matches currently `LIVE` (events/lineups while playing) |
| backfill | 1800 s | fetch details for finished matches still missing them (capped per run) |

Intervals are configurable through env vars (`REFRESH_TODAY_SEC`,
`REFRESH_AROUND_SEC`, `ENRICH_LIVE_SEC`, `ENRICH_BACKFILL_SEC`, `0` disables).
For platforms where a long-running scheduler is not possible, run
`python -m scraper.cli api --no-schedule` and drive refreshing from outside:

```cron
# system crontab example
* * * * *  cd /srv/match-center && FOOTBALL_DB_URL=postgresql://... python3 -m scraper.cli refresh >/dev/null 2>&1
```

or hit `GET /api/cron/refresh?secret=…` from any cron service (the frontend
proxies it too — `vercel.json` already registers it every 5 minutes; set
`API_CRON_SECRET` on the backend and `CRON_SECRET` on the frontend).

## Image proxying (original links stay server-side)

Every provider image URL (team crest, competition logo) is replaced, **before
any JSON leaves the server**, with an opaque local path `/api/img?t=<token>`.
The token is a deterministic HMAC-SHA256 prefix; the token→URL mapping lives
in the `image_tokens` table and is never sent to the browser. The backend
downloads images once into `img_cache/` (bounded parallelism) and serves them
with long cache headers; the frontend's `/api/img` is a pass-through proxy.
Result: the browser only ever talks to our own origin and the upstream CDN
link is invisible in HTML, JSON and network traces.

## Frontend notes

- Bilingual EN/AR with full RTL mirroring (dialog headers, tabs, tables
  included), Arabic uses Latin digits (`ar-MA-u-nu-latn`).
- Day listings are **local-calendar correct for any timezone** — the API
  selects matches whose kickoff falls inside the requester's local-day UTC
  window (the frontend sends `tz`).
- **Competition dialogs**: every competition bar in the match list carries a
  table icon — click it for the **standings** (position badges coloured by
  Champions-League / Europa / relegation zones, W/D/L/GF/GA/GD/points,
  last-5 form chips, bilingual zone legend) and the **round navigator**
  (كل الجولات — الجولة 1…38 / Game Week 1…38) listing that round's results
  and fixtures grouped by date; every match opens the regular match dialog.
  Cups that have no table simply show the rounds tab only.
- Disallowed goals, missed penalties and own goals are annotated
  (Goal cancelled (VAR) / هدف ملغي (الفار) …) thanks to the VAR
  `outcome`/`decision` columns in `match_events`.
- `FOOTBALL_API_BASE` tells the frontend where the backend lives
  (default `http://127.0.0.1:8000`).

More details: `README.scraper.md` (scraper/DB/CLI reference,
including the legacy standalone web view `python -m scraper.cli serve`).
