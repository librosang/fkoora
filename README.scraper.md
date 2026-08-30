# Bilingual Football Scraper — goal.com → PostgreSQL

A polite, dependency-light scraper that builds a **bilingual (English / Arabic)
football database** in **PostgreSQL**, entirely from **goal.com**, which serves
the same data in ~40 locales:

| goal.com page | Language | What it provides |
|---|---|---|
| `/en/results/{date}` · `/en/live-scores` · `/en/fixtures/{date}` | English | listings: competitions, teams, scores, kickoff times, venues |
| `/ar/النتائج/{date}` · `/ar/مباريات-جارية-حاليًا` · `/ar/مواعيد-المباريات/{date}` | Arabic | same listings with Arabic names (same entity IDs) |
| `/en/match/{slug}/{id}` | English | **match details**: lineups, formations, ratings, scorers, assists, cards, substitutions, managers, stats (possession, xG, shots…), venue coordinates, referee, season |
| `/ar/المباراة/{slug}/{id}` | Arabic | same details with **Arabic player / manager / venue names** |

Because both languages come from the same site and share entity IDs
(sportfeeds.io under the hood), the English and Arabic columns are **always
consistent** — no cross-site fuzzy matching.

Historical data (any past date) and future fixtures both work — tested back to
at least May 2025.

> **Note**: goal.com's English listings sometimes use generic names ("Cup",
> "Premier League") while the Arabic versions are specific ("كأس النرويج",
> "الدوري الإنجليزي الممتاز"). That's exactly what each language version of
> the site displays — the `area_name_en` column (e.g. "Norway", "England")
> disambiguates.

---

## Quick start

```bash
pip install -r requirements.txt

# create the (empty) database once - the schema is applied automatically
createdb football
export FOOTBALL_DB_URL=postgresql://postgres:postgres@localhost:5432/football

# scrape one day (listings in both languages; ~15 seconds)
python -m scraper.cli date 2026-08-26

# one day + bilingual lineups/events/stats for the big competitions
python -m scraper.cli date 2026-08-25 --details

# see what's in the database
python -m scraper.cli show 2026-08-25 --arabic
python -m scraper.cli stats
```

The database is whatever `FOOTBALL_DB_URL` points at (override per command
with `--db postgresql://user:pass@host:5432/dbname`).

### Page-type auto-selection

The scraper picks the right page per date automatically:

| Date | English page | Arabic page |
|---|---|---|
| past | `/en/results/{date}` | `/ar/النتائج/{date}` |
| today | `/en/live-scores` | `/ar/مباريات-جارية-حاليًا` |
| future | `/en/fixtures/{date}` | `/ar/مواعيد-المباريات/{date}` |

## Commands

| Command | What it does |
|---|---|
| `date YYYY-MM-DD [--details]` | scrape one day; `--details` also fetches lineups/events/stats |
| `backfill --from YYYY-MM-DD --to YYYY-MM-DD [--details]` | historical range; idempotent — safe to re-run after interruptions |
| `bootstrap [--years-back N] [--days-ahead M] [--no-slow] [--no-details]` | **ONE-TIME slow historical walk** of the last N years + next M days; resumable — see below |
| `upcoming [--days N]` | future fixtures for the next N days (default 14) |
| `enrich [--date D \| --from D --to D]` | fetch details for already-stored matches that don't have them yet |
| `standings <id>… [--major]` | scrape a competition's **league table + every round's matches** (EN+AR); `--major` does every major competition in the DB |
| `show YYYY-MM-DD [--arabic]` | pretty-print stored matches for a date |
| `stats` | row counts per table |
| `serve [--port N] [--host H]` | launch the **local web view** (kooora-style, mobile-first) |
| `cache-crests` | pre-download every team crest & competition logo into `crest_cache/` |

### `bootstrap` — one-time historical baseline

The `bootstrap` command is the recommended way to seed a fresh database
with a complete historical baseline that **never needs to be re-scraped**,
plus a forward fixture window that becomes the starting point for the
daily updater.

```bash
# One-time, slow, polite walk of the last 10 years + next 1 year.
# Listings (EN+AR) for every day, plus details for major-competition
# matches. Re-run any time to resume from where it left off.
python -m scraper.cli bootstrap

# Same thing, detached so it survives the shell (logs to bootstrap.log):
./scripts/bootstrap_historical.sh
```

| Flag | Default | Purpose |
|---|---|---|
| `--years-back N` | `10` | how many years into the past to walk, day-by-day starting from today |
| `--days-ahead M` | `365` | how many days into the future to walk (fixtures become the seed for the daily updater) |
| `--today YYYY-MM-DD` | today | anchor date for the walk (useful for reproducible runs) |
| `--no-details` | off | listings only — skip the bilingual lineups/events/stats enrichment pass |
| `--no-slow` | off | use the normal 1 s rate-limit profile instead of the slow 2.5 s + 1.5 s jitter one (NOT recommended for the first run) |
| `--day-pause S` | `3.0` (slow) / `0` (fast) | seconds to sleep between consecutive days |
| `--max-details N` | `200` | cap detail pages per day (prevents a single dense match day from dominating the walk) |
| `--all` / `--leagues …` | major only | standard competition filter (see below) — applies to the enrichment pass |

The walk is **fully resumable**: every date that already has a successful
`scrape_runs` row of the matching mode (`run_mode='date'` for listings,
`run_mode='details'` for enrichment) is skipped, so an interrupted run
simply picks up where it left off when re-launched. Historical listings
are absolute (scores never change), so a successful listing run for a past
date is never re-scraped automatically.

Walking order: the past window is traversed **newest-first** (today-1,
today-2, …, today-10y) so the most recent data lands in the database
first; the future window is traversed **oldest-first** (today, today+1,
…, today+1y).

Tuning the slow profile: edit `SLOW_RATE_LIMIT_DELAY`,
`SLOW_RATE_LIMIT_JITTER`, `BOOTSTRAP_DAY_PAUSE_SEC` in
`scraper/config.py`. The defaults (~3–4 s between requests, 3 s between
days) keep a ~7300-request historical walk gentle enough to leave
running unattended (~6 h for listings only, longer with `--details`).

### Competition filters (for `--details` / `enrich`)

Detail pages cost two requests per match (EN + AR), so by default details are
only fetched for **top-5 leagues + major cups** (Premier League, LaLiga, Serie
A, Bundesliga, Ligue 1, UEFA CL/EL/Conference, World Cup, AFCON, Nations
League, Saudi Pro League, Copa Libertadores…), excluding youth/reserve/women's
editions.

```bash
--all                                   # every competition, no filter
--leagues 'premier league@england'      # custom filter; @area disambiguates
--leagues 'premier league@england' 'saudi' 'ligue 1@france'
--max-details 20                        # cap detail pages per day (testing)
--refresh-details                       # re-fetch even if already fetched
```

> Note: many local leagues share names (20+ "Premier League"s, several
> "Bundesliga"s). Use `@area` to pin the one you mean. `--all` ignores filters.

### Language & source options

```bash
--no-arabic     # English only (1 request per listing day, 1 per match detail)
--kooora        # additionally merge kooora.com as an Arabic fallback
                # (off by default - goal.com covers both languages)
```

### Typical workflows

```bash
# ONE-TIME bootstrap on a fresh database: last 10 years back + next 1 year
# forward, slow + resumable. Detached so it survives the shell.
./scripts/bootstrap_historical.sh

# Same thing in the foreground (if you want to watch progress):
python -m scraper.cli bootstrap

# full season backfill, listings only first (fast: ~2 requests per day)
python -m scraper.cli backfill --from 2025-08-01 --to 2026-05-31

# then enrich with bilingual details in the background over time
python -m scraper.cli enrich --from 2025-08-01 --to 2026-05-31

# keep the DB current: cron this daily (e.g. 09:00)
python -m scraper.cli date $(date -I) --details
python -m scraper.cli upcoming --days 7
```

Everything is **idempotent** — rows are upserted, re-scraping a date refreshes
scores and fills missing columns without creating duplicates. Interrupted
backfills (and the `bootstrap` walk) resume where they left off because the
`scrape_runs` table already records each successful date.

### Running the bootstrap on Docker container start

The Docker image (`Dockerfile.api`) ships with an entrypoint
(`docker-entrypoint.sh`) that can launch the historical bootstrap
automatically when the container starts, **in parallel with the API** so the
API can serve requests immediately.

```bash
# Build the image
docker build -t fkoora-api -f Dockerfile.api .

# Run with bootstrap on start (one-time walk of the last 10 years + next 1 year)
docker run -d \
  --name fkoora-api \
  -p 9000:9000 \
  -e FOOTBALL_DB_URL=postgresql://user:pass@host:5432/football \
  -e BOOTSTRAP_ON_START=1 \
  fkoora-api

# Tail the bootstrap log:
docker exec -it fkoora-api tail -f /app/bootstrap.log

# Stop the bootstrap walk only (API keeps running):
docker exec fkoora-api pkill -f 'scraper.cli bootstrap'
```

| Env var | Default | Purpose |
|---|---|---|
| `BOOTSTRAP_ON_START` | `0` | `1` to run the bootstrap walk on container start; `0` to skip |
| `BOOTSTRAP_YEARS_BACK` | `10` | how many years into the past to walk (day-by-day from today) |
| `BOOTSTRAP_DAYS_AHEAD` | `365` | how many days into the future to walk |
| `BOOTSTRAP_NO_DETAILS` | `0` | `1` to skip the bilingual detail enrichment pass (listings only) |
| `BOOTSTRAP_NO_SLOW` | `0` | `1` to use the normal 1 s rate-limit instead of the slow 2.5 s + 1.5 s jitter profile (NOT recommended for the first walk) |
| `BOOTSTRAP_ALL` | `0` | `1` to enrich ALL competitions instead of just the majors (top-5 leagues + UEFA/AFCON/etc.) |
| `BOOTSTRAP_FORCE` | `0` | `1` to re-run the walk even if the marker file exists (e.g. after expanding `BOOTSTRAP_YEARS_BACK`) |
| `BOOTSTRAP_LOG` | `/app/bootstrap.log` | where the bootstrap writes its log |
| `BOOTSTRAP_MARKER_PATH` | `/app/.bootstrap_complete` | marker file written on full completion; persist this on a volume if you want the "skip" behaviour to survive image rebuilds |
| `GUNICORN_BIND` | `0.0.0.0:9000` | gunicorn bind address |
| `GUNICORN_WORKERS` | `1` | gunicorn workers (keep at 1 — exactly one scrape scheduler is enforced via a Postgres advisory lock) |
| `GUNICORN_THREADS` | `8` | gunicorn threads |
| `GUNICORN_TIMEOUT` | `180` | gunicorn worker timeout (seconds) |

**How the one-time walk works on Docker:**

1. **First start** (`BOOTSTRAP_ON_START=1`, marker missing): the entrypoint
   launches `python -m scraper.cli bootstrap` in the background, then execs
   gunicorn as PID 1. The API starts serving immediately while the slow walk
   runs in the background.
2. **Container restart mid-walk**: the entrypoint launches the bootstrap
   again. Because every successful date is recorded in `scrape_runs`, the
   walk simply resumes where it left off (already-done dates are skipped).
3. **Container restart after full completion**: the marker file exists, so
   the entrypoint skips the bootstrap entirely — startup is instant.
4. **Re-run after changing `BOOTSTRAP_YEARS_BACK`**: delete the marker file
   inside the container (`docker exec fkoora-api rm /app/.bootstrap_complete`)
   or set `BOOTSTRAP_FORCE=1` and restart the container. The walk will run
   again, but already-done dates still get skipped (only the new range is
   scraped).

**Persisting the marker across rebuilds** (optional): mount a small volume
on `/app` (or specifically `BOOTSTRAP_MARKER_PATH`) so the marker survives
image rebuilds. Without this, a fresh container will re-scan the
`scrape_runs` table on first start (cheap — ~4000 DB queries, no network),
find every date already done, and exit in a few seconds.

**Resource notes:** the bootstrap runs in its own Python process and shares
the configured DB connection pool with the API. The slow profile (2.5 s +
1.5 s jitter between requests, 3 s between days) keeps goal.com load
modest. The API's own scheduler runs in a separate thread inside gunicorn
and focuses on today's live scores; the bootstrap walk focuses on past
dates plus the future window, so the two rarely collide. If they do,
upserts are idempotent, so worst case is some duplicate work — never
duplicate rows.

---

## JSON API backend (serves the Next.js frontend)

`scraper/api.py` exposes the database as the REST API consumed by the
Next.js frontend (`../src/`). It also contains the built-in scheduler
(crons) and the server-side image proxy.

```bash
python -m scraper.cli api --port 8000        # API + scheduler (default)
python -m scraper.cli api --no-schedule     # external crontab mode
python -m scraper.cli refresh               # one-shot run (crontab body)
python -m scraper.cli cache-images --days 12  # pre-warm the image disk cache
```

| Endpoint | Purpose |
|---|---|
| `GET /api/matches?date&today&major&tz` | grouped bilingual day listing; `tz` (minutes east of UTC) selects matches inside the **user's local-day window**, so day views are calendar-correct for every timezone |
| `GET /api/match/<id>` | full detail: events (incl. VAR `outcome`/`decision`), lineups, managers, stats, venue, referee |
| `GET /api/competition/<id>` | competition dialog data: **standings** (position/played/W/D/L/GF/GA/GD/points, last-5 form, zone markers with Arabic names), the season, and the full **round list** with match counts. Cups simply return `standings: null`. |
| `GET /api/competition/<id>/matches?gameset=<id>` | one round's matches (results + fixtures); omit `gameset` for the **active round**. Matches carry the same shape as `/api/matches` rows, so the frontend's match dialog opens from here too. |
| `GET /api/img?t=<token>` | image proxy — every crest/logo URL is replaced by an opaque HMAC token (`image_tokens` table) before leaving the server; images are disk-cached in `img_cache/` |
| `GET /api/cron/refresh?secret=…` | trigger a listing refresh + enrichment run (guard with `API_CRON_SECRET`) |
| `GET /api/health` | row counts, last scrape runs, scheduler intervals |

Competition data is fetched **on demand** the first time a competition is
opened (table page + every round, EN+AR — about three requests) and then
re-scraped when older than `COMPETITION_TTL_SEC` (default 1800 s).

The scheduler keeps the database fresh on its own (today every 60 s,
neighbouring days every 5 min while a match is live or a kickoff is due
within 12 h — every 30 min otherwise, live match details every 2 min, detail
backfill every 30 min — all env-configurable). Unknown dates and un-enriched
matches are fetched **on demand** on first request.

### goal.com load discipline

Scores and statuses are identical in every language — only names differ, and
names change a few times per season. The hot scrape paths therefore fetch the
**EN page every cycle** (fast data) and the **AR page only on slow AR cycles**:

| Path | EN cadence | AR cadence |
|---|---|---|
| today's listing (live-scores) | 60 s (`REFRESH_TODAY_SEC`) | 10 min (`AR_LISTING_SEC`) |
| neighbouring-day listings | 5 min active / 30 min idle (`REFRESH_AROUND_SEC` / `REFRESH_AROUND_IDLE_SEC`) | 10 min |
| live match detail pages | 2 min (`ENRICH_LIVE_SEC`) | 10 min per match (`AR_DETAIL_SEC`) |

When an EN-only live cycle observes the final whistle, one extra bilingual
fetch completes the record so the closing events keep their Arabic names.
The database layer preserves the language a refresh does not carry, so
alternating EN/AR fetches never loses names.

On top of that, the built-in rate limiter is **thread-safe**: no matter how
many scrape threads run concurrently (scheduler jobs, on-demand fetches,
standings refreshes), requests keep the configured global spacing
(`RATE_LIMIT_DELAY` + jitter), so accidental bursts that trigger 429s are
impossible.

### Heavy usage / scaling

* **Several API processes are safe now**: the scheduler elects exactly one
  **leader** through a PostgreSQL advisory lock (`SCHEDULER_ROLE=auto`, the
  default). Standbys re-check every 15 s and take over if the leader dies —
  run N gunicorn workers / replicas without multiplying goal.com traffic.
  `SCHEDULER_ROLE=force` for a dedicated scraper box, `off` to disable.
* **Conditional responses**: every JSON endpoint emits a strong `ETag` and
  answers `If-None-Match` with `304`. The Next.js proxies forward the
  browser's validator, so while data is unchanged a poll costs a few hundred
  bytes on both hops instead of re-downloading the payload.
* **Image serving**: disk cache + in-memory LRU (`IMG_MEM_CACHE_MB`, 0 to
  disable) + `ETag`/`304` revalidation, so hot crests are served from RAM
  with zero disk reads and cheap revalidations.
* **DB pool** sizing via `DB_POOL_MIN` / `DB_POOL_MAX` (default 1/8 per
  process; keep the total across processes within PostgreSQL's
  `max_connections`).

Key env vars (all optional): `REFRESH_TODAY_SEC`, `REFRESH_AROUND_SEC`,
`REFRESH_AROUND_IDLE_SEC`, `AROUND_ACTIVE_LOOKAHEAD_SEC`, `ENRICH_LIVE_SEC`,
`ENRICH_BACKFILL_SEC`, `LIVE_ENRICH_MAX`, `AR_LISTING_SEC`, `AR_DETAIL_SEC`,
`ON_DEMAND_RETRY_SEC`, `SCHEDULER_ROLE`, `SCHED_LOCK_KEY`,
`IMG_MEM_CACHE_MB`, `DB_POOL_MIN`, `DB_POOL_MAX`, plus the competition
knobs (`COMPETITION_TTL_SEC`, `COMP_REFRESH_SEC`, `COMP_REFRESH_MAX`,
`COMP_VIEW_TRACK_SEC`, `COMP_EVENT_REFRESH`, `COMP_EVENT_DEBOUNCE_SEC`).
`GET /api/health` reports the live configuration and which process is the
scheduler leader.

See `../README.md` for the full architecture and the frontend wiring.

---

## Web view (kooora-style, mobile-first)

The project ships with a lightweight **Flask web UI** that reads the
PostgreSQL database directly — no scraping involved, so it works fully
offline once data is stored.

```bash
python -m scraper.cli serve              # http://127.0.0.1:8765
python -m scraper.cli serve --port 9000
python -m scraper.cli serve --db postgresql://localhost/football
```

| Route | What it shows |
|---|---|
| `/` | **today** — live scores (auto-refresh every 2 min while matches are live) |
| `/day/{date}` | any date: past → *Results*, today → *Live*, future → *Fixtures* |
| `/match/{id}` | full detail: scoreboard with big crests + **tabbed sections** (Events / Lineups / Stats), lineups (starters + bench, captain, ratings), managers, team stats with comparison bars, venue & referee |
| `?lang=ar` | instant **Arabic / RTL** switch on every page |

Design (inspired by the old kooora.com):

* **Mobile-first**: match rows stack the two teams (crest + name + score per
  line) with a slim status column — big touch targets, no horizontal scroll.
  On screens ≥ 680px rows switch to the classic horizontal layout
  `time | home | score | away`.
* **Team emblems on every match row** (and competition logos in the navy
  header bars). Crests are downloaded **once** into a local `crest_cache/`
  folder and served by the app itself (`/crest/<team_id>`,
  `/compimg/<comp_id>`) — so logos render in **every browser**, including
  Safari (whose tracking prevention / Private Relay can block the source
  CDN), and keep working offline. A generic badge shows if a crest can't be
  fetched. Pre-cache everything with:

  ```bash
  python -m scraper.cli cache-crests   # one-time, ~6 min for all teams
  ```

* **Tabs (Events / Lineups / Stats)** on the match page — like old kooora's
  match tabs, with the active tab in navy; sections that have no data are
  omitted automatically.
* Navy competition header bars, white/light-blue alternating rows, bold blue
  winner names, red pulsing LIVE indicators — the classic look, fully
  **RTL-aware** for Arabic.
* Kick-off times are stored in UTC and converted to **your local timezone**
  in the browser.
* Collapsible competition sections (tap a header to fold a league), popular
  competitions sorted first, live matches pinned to the top.
* Collapsible sections and tabs are plain **button-based toggles** (not
  `<details>`/`<summary>`) — flex-styled summaries render unreliably in
  Safari, so the UI avoids them entirely.

---

## Database schema

```
competitions ─┬─< seasons
              ├─< standings >──────── teams        (league tables + zone markers)
              ├─< standings_markers                (legend: CL / Europa / relegation…)
              ├─< gamesets                         (rounds: "Game Week 3" / "الجولة 3")
              ├─< competition_scrapes              (TTL bookkeeping for on-demand scrapes)
              ├─────< matches >─────── teams
              │        │
venues ────────┘        ├─< match_events   >── players
                       ├─< lineups        >── players
                       ├─< match_managers >── teams
                       └─< team_match_stats >── teams
image_tokens (opaque image-proxy tokens -> upstream URLs)
scrape_runs  (observability: every run, per source, per day)
```

Notable columns: `matches.slug_ar` / `lineups_confirmed` /
`home_formation` / `away_formation`; `match_events.outcome` + `.decision`
(VAR review results — disallowed goals, overturned penalties).

| Table | Grain | Highlights |
|---|---|---|
| `competitions` | one per competition ID | `name_en`, `name_ar`, area in both languages, logo URL |
| `seasons` | one per competition season | e.g. "2026/2027" |
| `teams` | one per team ID | EN long/short names, **AR name**, 3-letter code, crest URL |
| `players` | one per player ID | EN name, **AR name** (from AR match pages), image, verified flag |
| `venues` | one per stadium | EN/AR name, latitude/longitude |
| `matches` | one per match ID | kickoff UTC, `match_date`, status, score breakdown (HT/FT/ET/aggregate/penalties), red cards, venue, referee, season, round/stage (EN + AR) |
| `match_events` | one per match event | goals (with assists), cards, substitutions — minute + stoppage minute, running score, **player names in EN and AR** |
| `lineups` | one per player per match per team | starter/bench, shirt number, pitch coordinates, captain flag, 0–10 rating |
| `match_managers` | one per team per match | manager names in EN and AR |
| `team_match_stats` | one per stat per team per match | POSSESSION, EXPECTED_GOAL, SHOT_TOTAL, SHOT_ON_TARGET, CORNER_TOTAL, FOUL_COMMITED, OFFSIDE_TOTAL |
| `standings` | one row per table position per stage | played/W/D/L/GF/GA/GD/points, **last-5 form** (JSON), zone **markers** (JSON), group name; refreshed wholesale per scrape so position shifts never leave stale rows |
| `standings_markers` | one per zone marker | legend id → bilingual name + type (PROMOTION / RELEGATION) — drives the coloured position badges |
| `gamesets` | one per round per competition | provider `game_set_type_id` + EN/AR names ("Game Week 4" / "الجولة 4") + active flag; `matches.gameset_id` links every match to its round |
| `competition_scrapes` | one per competition | `standings_at` / `matches_at` timestamps + `has_standings` (0 for cups) — the on-demand TTL source |

Design notes:

* **IDs are the provider's IDs** — identical across languages and stable across
  re-scrapes, which is what makes the bilingual merge exact.
* `matches.match_date` = UTC date derived from kickoff (use this for
  "matches on day X" queries). `matches.listed_date` = the listing-page date
  the match was grouped under. Both are indexed.
* `match_events` / `lineups` / `team_match_stats` only exist when details were
  fetched — check `matches.detail_fetched_at IS NOT NULL`.
* Not every player has an Arabic name — goal.com's provider hasn't translated
  some squad players; those rows keep `name_ar` NULL.

---

## How the scraping works (no browser needed)

goal.com is a Next.js app that embeds the complete page payload as JSON in
`<script id="__NEXT_DATA__">`. The scraper:

1. `GET /en/{results|live-scores|fixtures}/...` → parse `liveScores` from
   `__NEXT_DATA__` (competitions, teams, scores, kickoff times, venues).
2. `GET /ar/...` (same page type) → Arabic names, same IDs → merge.
3. Optionally `GET /en/match/x/{id}` + `GET /ar/المباراة/x/{id}` per match →
   lineups, events, stats merged by player ID (the URL slug is ignored by the
   server — only the match ID matters).
4. Competition feature: `GET /en/{slug}/table/{compId}` → the full standings
   (the Arabic `/table` URL only renders a top-5 summary, so Arabic team names
   come from the AR edition of the round API below instead), plus
   `GET /api/competition-matches?id={compId}&edition={en|ar}` → goal.com's
   internal round API: **every gameset of the season with its matches in one
   request per language** (EN structure + AR names/slugs). Cups simply have no
   table page — that's recorded and skipped afterwards.

Politeness: ~1 request/second with jitter, exponential-backoff retries,
429-aware, shared session. One day of listings = **2 requests**; details add
2 requests per match.

## Project layout

```
.
├── README.md
├── requirements.txt          # requests + beautifulsoup4 + flask + psycopg
├── scraper/
│   ├── config.py             # URLs, rate limits, competition filter rules, DB URL
│   ├── http_client.py        # rate-limited fetcher + __NEXT_DATA__ extraction
│   ├── parsers/
│   │   ├── goal.py           # bilingual listing + match detail parsers
│   │   └── kooora.py         # optional Arabic fallback (--kooora)
│   ├── db/
│   │   ├── schema.sql        # full DDL (PostgreSQL, applied on start)
│   │   ├── backend.py        # connection/pool management
│   │   └── database.py       # idempotent upserts, cross-language merging
│   ├── pipeline.py           # orchestration (EN listing → AR merge → details)
│   ├── webapp.py             # Flask web view over the database
│   ├── templates/            # Jinja2: day list + match detail (+ macros)
│   ├── static/               # kooora-style CSS, tiny JS, crest fallback SVG
│   └── cli.py                # argparse CLI (incl. `serve`)
└── examples/queries.sql      # ready-to-run analytical queries (psql)
```

## Legal & fair use

Scrape at a modest rate, keep the delay ≥ 1s, and respect the site's terms of
service. The data is for personal/analytical use. Rate limits are configurable
in `scraper/config.py`.
