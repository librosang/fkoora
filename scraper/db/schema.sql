-- ============================================================================
-- Football database schema (PostgreSQL)
--
-- Design notes
--   * All entity IDs (competitions, teams, players, matches, seasons) are the
--     sportfeeds.io IDs shared by goal.com and kooora.com, so English and
--     Arabic names live in the same row (name_en / name_ar columns).
--   * matches.match_date is the UTC date derived from kickoff - use it for
--     "matches on day X" queries. matches.listed_date is the date of the
--     fixtures page the match was scraped from (sites group some late/early
--     kickoffs under a neighbouring day).
--   * match_events / lineups are only populated when match details were
--     fetched (see matches.detail_fetched_at).
--   * Date/timestamp columns use the native PostgreSQL types (TIMESTAMPTZ /
--     DATE) - fresh databases get them directly from this file; EXISTING
--     databases are converted in place, with value validation, by
--     scraper/db/migrate.py (run automatically on first start, or manually
--     via `python -m scraper.cli migrate-types`). `kickoff_utc` remains the
--     UTC instant; `match_date` remains the UTC calendar date of the kickoff
--     (NOT the user's local date) - exactly the semantics the TEXT version
--     had. Application code still WRITES ISO strings (PostgreSQL casts
--     them) and READS through scraper/timeutil.py, which normalizes both
--     representations to the API's wire format.
--   * matches.data_version is a monotonic change counter: every meaningful
--     client-visible change (score, status, period, red cards, events,
--     lineups, ...) bumps it by one. The live layer (scraper/live.py)
--     publishes it with every SSE update so clients can drop out-of-order
--     events.
--   * The schema is applied idempotently on every start (CREATE TABLE IF NOT
--     EXISTS), so a fresh database needs no manual setup step.
--   * UPGRADES: the file is organised in three passes so that an existing
--     database created by an older version is healed automatically:
--       pass 1 - CREATE TABLE IF NOT EXISTS (creates only missing tables)
--       pass 2 - ALTER TABLE ADD COLUMN IF NOT EXISTS (adds columns that
--                appeared in newer versions to pre-existing tables)
--       pass 3 - CREATE INDEX IF NOT EXISTS (after the columns exist)
--     This ordering matters: indexes on new columns would fail if they ran
--     before pass 2 on an old database. (TYPE conversions are NOT in here on
--     purpose - ALTER TYPE rewrites the table on every run - they live in
--     scraper/db/migrate.py which checks information_schema first.)
-- ============================================================================

-- ===========================================================================
-- PASS 1 - CREATE TABLE IF NOT EXISTS
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- Competitions (leagues & cups)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS competitions (
    id            TEXT PRIMARY KEY,     -- sportfeeds ID (same on both sites)
    name_en       TEXT,
    name_ar       TEXT,
    area_name_en  TEXT,                 -- "England", "International", ...
    area_name_ar  TEXT,
    area_code     TEXT,                 -- "ENG", "WORLD", ...
    image_url     TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at  TIMESTAMPTZ NOT NULL
);
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS seasons (
    id             TEXT PRIMARY KEY,    -- sportfeeds season ID
    competition_id TEXT NOT NULL REFERENCES competitions(id),
    name           TEXT,                -- "2026/2027"
    is_active      INTEGER DEFAULT 0,
    first_seen_at  TIMESTAMPTZ NOT NULL,
    last_seen_at   TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------------------
-- Teams
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS teams (
    id            TEXT PRIMARY KEY,     -- sportfeeds team ID
    name_en       TEXT,                 -- long/full English name
    short_name_en TEXT,
    name_ar       TEXT,
    code          TEXT,                 -- 3-letter code, e.g. "SAB"
    crest_url     TEXT,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at  TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------------------
-- Players  (populated from lineups, events and scorer lists)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS players (
    id            TEXT PRIMARY KEY,     -- sportfeeds person ID
    name_en       TEXT,
    name_ar       TEXT,                 -- filled from /ar/اللاعب/{slug}/{id}
    image_url     TEXT,
    is_verified   INTEGER DEFAULT 0,
    -- bio fields (filled by the player profile parser; nullable when unknown)
    full_name_en  TEXT,                 -- "Lionel Andrés Messi Cuccittini"
    full_name_ar  TEXT,
    slug_en       TEXT,                 -- URL slug, for deep-linking back to goal.com
    slug_ar       TEXT,
    position      TEXT,                 -- GOALKEEPER / DEFENDER / MIDFIELDER / FORWARD
    shirt_number  INTEGER,
    height_cm     INTEGER,
    weight_kg     INTEGER,
    birth_date    TEXT,                  -- ISO YYYY-MM-DD
    age           INTEGER,
    nationality_en TEXT,
    nationality_ar TEXT,
    country_of_birth_en TEXT,
    country_of_birth_ar TEXT,
    place_of_birth_en TEXT,
    place_of_birth_ar TEXT,
    current_club_id TEXT,                -- sportfeeds team ID
    current_club_name_en TEXT,
    current_club_name_ar TEXT,
    profile_fetched_at TIMESTAMPTZ,       -- when the /player page was last pulled
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at  TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------------------
-- Player career entries
--
-- One row per "club stint per season" in a player's career history. The
-- /en/player/{slug}/{id} page lists every club the player has been at, with
-- the season, the competition, appearances, goals, and (sometimes) loan
-- flags. We store them flat here so the API can build a full career timeline
-- and so analysis queries like "Messi's goal tally per season" are trivial.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS player_career_entries (
    id              BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    player_id       TEXT NOT NULL REFERENCES players(id),
    team_id         TEXT,                 -- sportfeeds team ID
    team_name_en    TEXT,
    team_name_ar    TEXT,
    season_name     TEXT,                 -- "2024/2025" or "2024" (one per row)
    competition_id  TEXT,                 -- sportfeeds competition ID
    competition_name_en TEXT,
    competition_name_ar TEXT,
    appearances     INTEGER,
    goals           INTEGER,
    assists         INTEGER,
    yellow_cards    INTEGER,
    red_cards       INTEGER,
    minutes_played  INTEGER,
    is_loan         INTEGER DEFAULT 0,
    sort_order      INTEGER,              -- provider-ordered (most recent first)
    UNIQUE(player_id, team_id, season_name, competition_id)
);

-- ---------------------------------------------------------------------------
-- Venues (stadiums) - no provider ID, deduplicated by English name
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS venues (
    id         BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    name_en    TEXT UNIQUE,
    name_ar    TEXT,
    latitude   DOUBLE PRECISION,
    longitude  DOUBLE PRECISION
);

-- ---------------------------------------------------------------------------
-- Matches
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matches (
    id               TEXT PRIMARY KEY,  -- sportfeeds match ID
    competition_id   TEXT NOT NULL REFERENCES competitions(id),
    season_id        TEXT REFERENCES seasons(id),
    kickoff_utc      TIMESTAMPTZ NOT NULL, -- kickoff instant (UTC)
    match_date       DATE NOT NULL,     -- UTC date of kickoff (YYYY-MM-DD)
    listed_date      DATE,              -- fixtures-page date it was found on
    status           TEXT NOT NULL,     -- FIXTURE / RESULT / CANCELLED / LIVE ...
    period           TEXT,              -- live period info
    round_name       TEXT,              -- round / stage name (EN)
    gameset_name     TEXT,              -- "Final", "Round 3", ...
    gameset_name_ar  TEXT,
    gameset_id       TEXT,              -- provider gameSetTypeId (round key)
    gameset_is_knockout INTEGER DEFAULT 0,
    home_team_id     TEXT NOT NULL REFERENCES teams(id),
    away_team_id     TEXT NOT NULL REFERENCES teams(id),
    venue_id         BIGINT REFERENCES venues(id),
    referee          TEXT,
    lineups_confirmed INTEGER DEFAULT 0,   -- provider "lineups.confirmed" flag
    home_formation   TEXT,                -- e.g. "4-3-3"
    away_formation   TEXT,
    -- scores
    home_score       INTEGER,
    away_score       INTEGER,
    home_score_ht    INTEGER,           -- half-time
    away_score_ht    INTEGER,
    home_score_ft    INTEGER,           -- full-time (end of 90')
    away_score_ft    INTEGER,
    home_score_et    INTEGER,           -- after extra time
    away_score_et    INTEGER,
    home_agg_score   INTEGER,           -- two-legged aggregate
    away_agg_score   INTEGER,
    home_pen_score   INTEGER,           -- penalty shootout
    away_pen_score   INTEGER,
    home_red_cards   INTEGER DEFAULT 0,
    away_red_cards   INTEGER DEFAULT 0,
    -- bookkeeping
    slug_en          TEXT,
    slug_ar          TEXT,                -- Arabic link slug (from AR listing)
    detail_fetched_at TIMESTAMPTZ,       -- when lineups/events/stats were pulled
    last_updated_at  TIMESTAMPTZ,        -- source lastUpdatedAt
    data_version     BIGINT NOT NULL DEFAULT 1, -- bumps on every meaningful change
    first_seen_at    TIMESTAMPTZ NOT NULL,
    last_seen_at2    TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------------------
-- Match events (goals, cards, substitutions, period markers)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS match_events (
    id                 BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    match_id           TEXT NOT NULL REFERENCES matches(id),
    team_side          TEXT,            -- home / away / NULL (neutral marker)
    event_type         TEXT NOT NULL,   -- GOAL, CARD_YELLOW, SUBSTITUTION, ...
    minute             INTEGER,
    extra_minute       INTEGER,         -- stoppage/extra time minute
    player_id          TEXT REFERENCES players(id),
    player_name_en     TEXT,            -- denormalized snapshot (survives deletes)
    player_name_ar     TEXT,
    related_player_id  TEXT REFERENCES players(id),  -- assist / sub partner
    related_player_name_en TEXT,
    related_player_name_ar TEXT,
    home_score_after   INTEGER,
    away_score_after   INTEGER,
    outcome           TEXT,              -- VAR events: e.g. NO_GOAL / NO_PENALTY
    decision          TEXT,              -- VAR events: e.g. CANCELLED / CONFIRMED
    sort_order         INTEGER
);

-- ---------------------------------------------------------------------------
-- Lineups (starting XI + bench, per match & team)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS lineups (
    match_id     TEXT NOT NULL REFERENCES matches(id),
    team_id      TEXT NOT NULL REFERENCES teams(id),
    player_id    TEXT NOT NULL REFERENCES players(id),
    is_starter   INTEGER NOT NULL DEFAULT 0,
    shirt_number INTEGER,
    position_x   DOUBLE PRECISION,      -- pitch coordinates from provider
    position_y   DOUBLE PRECISION,
    is_captain   INTEGER DEFAULT 0,
    rating       DOUBLE PRECISION,      -- player match rating (0-10)
    PRIMARY KEY (match_id, team_id, player_id)
);

-- ---------------------------------------------------------------------------
-- Managers per match
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS match_managers (
    match_id         TEXT NOT NULL REFERENCES matches(id),
    team_id          TEXT NOT NULL REFERENCES teams(id),
    manager_id       TEXT,
    manager_name_en  TEXT,
    manager_name_ar  TEXT,
    PRIMARY KEY (match_id, team_id)
);

-- ---------------------------------------------------------------------------
-- Team match statistics (summary set: possession, xG, shots, ...)
-- id keeps provider insertion order (the API orders stats by first insert).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS team_match_stats (
    id        BIGINT GENERATED BY DEFAULT AS IDENTITY,
    match_id  TEXT NOT NULL REFERENCES matches(id),
    team_id   TEXT NOT NULL REFERENCES teams(id),
    stat_type TEXT NOT NULL,            -- POSSESSION, EXPECTED_GOAL, SHOT_TOTAL
    value     DOUBLE PRECISION,
    PRIMARY KEY (match_id, team_id, stat_type)
);

-- ---------------------------------------------------------------------------
-- Image tokens: opaque proxy tokens -> upstream image URLs.
-- The API hands out /api/img?t=<token> paths ONLY; the original CDN link
-- never reaches the browser (server-side link hiding).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS image_tokens (
    token      TEXT PRIMARY KEY,     -- HMAC-SHA256(secret, url) prefix
    url        TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------------------
-- Standings (league tables; scraped from goal.com table pages)
-- stage: total (overall) - home/away tables can be added later
-- One row per table position; refreshed wholesale per scrape (see
-- Database.replace_standings) so position shifts never leave stale rows.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS standings (
    id              BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    competition_id  TEXT NOT NULL REFERENCES competitions(id),
    season_id       TEXT REFERENCES seasons(id),
    stage           TEXT NOT NULL DEFAULT 'total',
    table_name      TEXT,                  -- group name ("Group A") or NULL
    position        INTEGER NOT NULL,
    team_id         TEXT NOT NULL REFERENCES teams(id),
    played          INTEGER,
    win             INTEGER,
    draw            INTEGER,
    lose            INTEGER,
    goals_for       INTEGER,
    goals_against   INTEGER,
    goal_diff       INTEGER,
    points          INTEGER,
    form_json       TEXT,                  -- [{"wdl":"WIN","matchId":"..."}]
    markers_json    TEXT,                  -- ["CHAMPIONS_LEAGUE", ...]
    updated_at      TIMESTAMPTZ,
    UNIQUE(competition_id, season_id, stage, table_name, position)
);

-- ---------------------------------------------------------------------------
-- Gamesets (rounds / matchdays of a competition, e.g. "Game Week 3")
-- game_set_type_id is the provider's stable round key used to link matches.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gamesets (
    id                BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    competition_id    TEXT NOT NULL REFERENCES competitions(id),
    season_id         TEXT REFERENCES seasons(id),
    game_set_type_id  TEXT NOT NULL,
    name_en           TEXT,
    name_ar           TEXT,
    is_active         INTEGER DEFAULT 0,
    sort_order        INTEGER,
    UNIQUE(competition_id, game_set_type_id)
);

-- ---------------------------------------------------------------------------
-- Standings markers legend (zone colors: Champions League, Relegation, ...)
-- id -> display name mapping per competition/season
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS standings_markers (
    competition_id  TEXT NOT NULL,
    season_id       TEXT,
    marker_id       TEXT NOT NULL,
    name            TEXT,                  -- English legend name from provider
    type            TEXT,                  -- PROMOTION / RELEGATION / ...
    UNIQUE(competition_id, season_id, marker_id)
);

-- ---------------------------------------------------------------------------
-- Competition scrape bookkeeping (TTL checks for the on-demand API)
-- has_standings = 0 for cups (no table page) so we skip re-hitting the 404.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS competition_scrapes (
    competition_id  TEXT PRIMARY KEY REFERENCES competitions(id),
    season_id       TEXT,
    has_standings   INTEGER DEFAULT 1,
    standings_at    TIMESTAMPTZ,
    matches_at      TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- API <-> worker handoff: refresh job queue (the ONLY way data gaps travel
-- between the read-only API server and the scraper worker).
--
-- The API upserts a row whenever it serves missing/empty/stale data (empty
-- day listing, match detail never fetched, player profile never fetched,
-- unknown or stale competition, external /api/cron/refresh poke). The
-- worker polls pending rows (done_at IS NULL), scrapes the data and marks
-- them done. Upserts reset a finished row back to pending, guarded by the
-- on-demand retry window so a hammering client cannot re-trigger endless
-- goal.com fetches for data that simply does not exist.
--
-- kinds:
--   day_listing     ref = YYYY-MM-DD, payload = {"tz": minutes}  local-day pages
--   match_detail    ref = match id
--   player_profile  ref = player id
--   comp_refresh    ref = competition id   (TTL refresh, scrape_competition_if_stale)
--   comp_discovery  ref = competition id   (unknown comp, forced scrape_competition)
--   comp_pending    ref = competition id   (marker: match ended while nobody
--                                          had the league open; the API serves
--          refreshing=true until the worker refreshes or the marker is consumed)
--   cron_refresh    ref = UTC date         (external cron poke)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS refresh_jobs (
    id            BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    kind          TEXT NOT NULL,
    ref           TEXT NOT NULL,
    payload       TEXT,                          -- optional JSON args ({"tz": 60})
    requested_at  TIMESTAMPTZ NOT NULL,
    attempts      INTEGER NOT NULL DEFAULT 0,
    done_at       TIMESTAMPTZ,                   -- NULL = pending
    error         TEXT,
    UNIQUE (kind, ref)
);
CREATE INDEX IF NOT EXISTS refresh_jobs_pending_idx
    ON refresh_jobs (requested_at) WHERE done_at IS NULL;

-- ---------------------------------------------------------------------------
-- API <-> worker handoff: which competitions users actually open. The API
-- upserts one row per open; the worker keeps those leagues warm and uses
-- the timestamps to decide which standings to refresh the moment a match
-- ends (event-driven refresh) - leagues nobody looks at are only refreshed
-- on the slow fallback cycle, exactly like the old in-process tracking.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS competition_views (
    competition_id  TEXT PRIMARY KEY,
    last_viewed_at  TIMESTAMPTZ NOT NULL,
    view_count      BIGINT NOT NULL DEFAULT 1
);

-- ---------------------------------------------------------------------------
-- Scrape run log (observability)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scrape_runs (
    id                  BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    run_mode            TEXT,           -- date / backfill / upcoming / details
    target              TEXT,           -- date or match id
    source              TEXT,           -- goal / kooora / both
    status              TEXT NOT NULL,  -- running / ok / error
    competitions_found  INTEGER DEFAULT 0,
    matches_found       INTEGER DEFAULT 0,
    matches_stored      INTEGER DEFAULT 0,
    details_fetched     INTEGER DEFAULT 0,
    error               TEXT,
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ
);

-- ===========================================================================
-- PASS 2 - UPGRADE SAFETY NET
--
-- Adds every nullable (or defaulted) column introduced after the first
-- release to any table that predates it. Core NOT NULL columns are absent
-- on purpose: they have existed since the first schema and adding a NOT NULL
-- column without a default to a populated table is illegal anyway.
-- Every statement is IF NOT EXISTS, so running on a fresh database is a no-op.
-- ===========================================================================

ALTER TABLE competitions ADD COLUMN IF NOT EXISTS name_en       TEXT;
ALTER TABLE competitions ADD COLUMN IF NOT EXISTS name_ar       TEXT;
ALTER TABLE competitions ADD COLUMN IF NOT EXISTS area_name_en  TEXT;
ALTER TABLE competitions ADD COLUMN IF NOT EXISTS area_name_ar  TEXT;
ALTER TABLE competitions ADD COLUMN IF NOT EXISTS area_code     TEXT;
ALTER TABLE competitions ADD COLUMN IF NOT EXISTS image_url     TEXT;

ALTER TABLE seasons ADD COLUMN IF NOT EXISTS name      TEXT;
ALTER TABLE seasons ADD COLUMN IF NOT EXISTS is_active INTEGER DEFAULT 0;

ALTER TABLE teams ADD COLUMN IF NOT EXISTS name_en       TEXT;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS short_name_en TEXT;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS name_ar       TEXT;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS code          TEXT;
ALTER TABLE teams ADD COLUMN IF NOT EXISTS crest_url     TEXT;

ALTER TABLE players ADD COLUMN IF NOT EXISTS name_en       TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS name_ar       TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS image_url     TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS is_verified   INTEGER DEFAULT 0;
ALTER TABLE players ADD COLUMN IF NOT EXISTS full_name_en  TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS full_name_ar  TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS slug_en       TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS slug_ar       TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS position      TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS shirt_number  INTEGER;
ALTER TABLE players ADD COLUMN IF NOT EXISTS height_cm     INTEGER;
ALTER TABLE players ADD COLUMN IF NOT EXISTS weight_kg     INTEGER;
ALTER TABLE players ADD COLUMN IF NOT EXISTS birth_date    TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS age           INTEGER;
ALTER TABLE players ADD COLUMN IF NOT EXISTS nationality_en TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS nationality_ar TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS country_of_birth_en TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS country_of_birth_ar TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS place_of_birth_en  TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS place_of_birth_ar  TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS current_club_id      TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS current_club_name_en TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS current_club_name_ar TEXT;
ALTER TABLE players ADD COLUMN IF NOT EXISTS profile_fetched_at   TEXT;

ALTER TABLE player_career_entries ADD COLUMN IF NOT EXISTS team_id            TEXT;
ALTER TABLE player_career_entries ADD COLUMN IF NOT EXISTS team_name_en       TEXT;
ALTER TABLE player_career_entries ADD COLUMN IF NOT EXISTS team_name_ar       TEXT;
ALTER TABLE player_career_entries ADD COLUMN IF NOT EXISTS season_name        TEXT;
ALTER TABLE player_career_entries ADD COLUMN IF NOT EXISTS competition_id     TEXT;
ALTER TABLE player_career_entries ADD COLUMN IF NOT EXISTS competition_name_en TEXT;
ALTER TABLE player_career_entries ADD COLUMN IF NOT EXISTS competition_name_ar TEXT;
ALTER TABLE player_career_entries ADD COLUMN IF NOT EXISTS appearances        INTEGER;
ALTER TABLE player_career_entries ADD COLUMN IF NOT EXISTS goals              INTEGER;
ALTER TABLE player_career_entries ADD COLUMN IF NOT EXISTS assists            INTEGER;
ALTER TABLE player_career_entries ADD COLUMN IF NOT EXISTS yellow_cards       INTEGER;
ALTER TABLE player_career_entries ADD COLUMN IF NOT EXISTS red_cards          INTEGER;
ALTER TABLE player_career_entries ADD COLUMN IF NOT EXISTS minutes_played     INTEGER;
ALTER TABLE player_career_entries ADD COLUMN IF NOT EXISTS is_loan            INTEGER DEFAULT 0;
ALTER TABLE player_career_entries ADD COLUMN IF NOT EXISTS sort_order         INTEGER;

ALTER TABLE venues ADD COLUMN IF NOT EXISTS name_ar   TEXT;
ALTER TABLE venues ADD COLUMN IF NOT EXISTS latitude  DOUBLE PRECISION;
ALTER TABLE venues ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION;

ALTER TABLE matches ADD COLUMN IF NOT EXISTS season_id          TEXT REFERENCES seasons(id);
ALTER TABLE matches ADD COLUMN IF NOT EXISTS listed_date        TEXT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS period             TEXT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS round_name         TEXT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS gameset_name       TEXT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS gameset_name_ar    TEXT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS gameset_id         TEXT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS gameset_is_knockout INTEGER DEFAULT 0;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS venue_id           BIGINT REFERENCES venues(id);
ALTER TABLE matches ADD COLUMN IF NOT EXISTS referee            TEXT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS lineups_confirmed  INTEGER DEFAULT 0;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_formation     TEXT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_formation     TEXT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_score       INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_score       INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_score_ht    INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_score_ht    INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_score_ft    INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_score_ft    INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_score_et    INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_score_et    INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_agg_score   INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_agg_score   INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_pen_score   INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_pen_score   INTEGER;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS home_red_cards   INTEGER DEFAULT 0;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS away_red_cards   INTEGER DEFAULT 0;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS slug_en            TEXT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS slug_ar            TEXT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS detail_fetched_at  TEXT;
ALTER TABLE matches ADD COLUMN IF NOT EXISTS last_updated_at    TEXT;

ALTER TABLE match_events ADD COLUMN IF NOT EXISTS team_side             TEXT;
ALTER TABLE match_events ADD COLUMN IF NOT EXISTS minute                INTEGER;
ALTER TABLE match_events ADD COLUMN IF NOT EXISTS extra_minute          INTEGER;
ALTER TABLE match_events ADD COLUMN IF NOT EXISTS player_id             TEXT REFERENCES players(id);
ALTER TABLE match_events ADD COLUMN IF NOT EXISTS player_name_en        TEXT;
ALTER TABLE match_events ADD COLUMN IF NOT EXISTS player_name_ar        TEXT;
ALTER TABLE match_events ADD COLUMN IF NOT EXISTS related_player_id     TEXT REFERENCES players(id);
ALTER TABLE match_events ADD COLUMN IF NOT EXISTS related_player_name_en TEXT;
ALTER TABLE match_events ADD COLUMN IF NOT EXISTS related_player_name_ar TEXT;
ALTER TABLE match_events ADD COLUMN IF NOT EXISTS home_score_after      INTEGER;
ALTER TABLE match_events ADD COLUMN IF NOT EXISTS away_score_after      INTEGER;
ALTER TABLE match_events ADD COLUMN IF NOT EXISTS outcome               TEXT;
ALTER TABLE match_events ADD COLUMN IF NOT EXISTS decision              TEXT;
ALTER TABLE match_events ADD COLUMN IF NOT EXISTS sort_order            INTEGER;

ALTER TABLE lineups ADD COLUMN IF NOT EXISTS is_starter   INTEGER NOT NULL DEFAULT 0;
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS shirt_number INTEGER;
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS position_x   DOUBLE PRECISION;
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS position_y   DOUBLE PRECISION;
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS is_captain   INTEGER DEFAULT 0;
ALTER TABLE lineups ADD COLUMN IF NOT EXISTS rating       DOUBLE PRECISION;

ALTER TABLE match_managers ADD COLUMN IF NOT EXISTS manager_id      TEXT;
ALTER TABLE match_managers ADD COLUMN IF NOT EXISTS manager_name_en TEXT;
ALTER TABLE match_managers ADD COLUMN IF NOT EXISTS manager_name_ar TEXT;

ALTER TABLE team_match_stats ADD COLUMN IF NOT EXISTS value DOUBLE PRECISION;

ALTER TABLE standings ADD COLUMN IF NOT EXISTS season_id     TEXT REFERENCES seasons(id);
ALTER TABLE standings ADD COLUMN IF NOT EXISTS stage         TEXT DEFAULT 'total';
ALTER TABLE standings ADD COLUMN IF NOT EXISTS table_name    TEXT;
ALTER TABLE standings ADD COLUMN IF NOT EXISTS played        INTEGER;
ALTER TABLE standings ADD COLUMN IF NOT EXISTS win           INTEGER;
ALTER TABLE standings ADD COLUMN IF NOT EXISTS draw          INTEGER;
ALTER TABLE standings ADD COLUMN IF NOT EXISTS lose          INTEGER;
ALTER TABLE standings ADD COLUMN IF NOT EXISTS goals_for     INTEGER;
ALTER TABLE standings ADD COLUMN IF NOT EXISTS goals_against INTEGER;
ALTER TABLE standings ADD COLUMN IF NOT EXISTS goal_diff     INTEGER;
ALTER TABLE standings ADD COLUMN IF NOT EXISTS points        INTEGER;
ALTER TABLE standings ADD COLUMN IF NOT EXISTS form_json     TEXT;
ALTER TABLE standings ADD COLUMN IF NOT EXISTS markers_json  TEXT;
ALTER TABLE standings ADD COLUMN IF NOT EXISTS updated_at    TEXT;

ALTER TABLE gamesets ADD COLUMN IF NOT EXISTS season_id       TEXT REFERENCES seasons(id);
ALTER TABLE gamesets ADD COLUMN IF NOT EXISTS name_en         TEXT;
ALTER TABLE gamesets ADD COLUMN IF NOT EXISTS name_ar         TEXT;
ALTER TABLE gamesets ADD COLUMN IF NOT EXISTS is_active       INTEGER DEFAULT 0;
ALTER TABLE gamesets ADD COLUMN IF NOT EXISTS sort_order      INTEGER;

ALTER TABLE standings_markers ADD COLUMN IF NOT EXISTS season_id TEXT;
ALTER TABLE standings_markers ADD COLUMN IF NOT EXISTS name      TEXT;
ALTER TABLE standings_markers ADD COLUMN IF NOT EXISTS type      TEXT;

ALTER TABLE competition_scrapes ADD COLUMN IF NOT EXISTS season_id     TEXT;
ALTER TABLE competition_scrapes ADD COLUMN IF NOT EXISTS has_standings INTEGER DEFAULT 1;
ALTER TABLE competition_scrapes ADD COLUMN IF NOT EXISTS standings_at  TEXT;
ALTER TABLE competition_scrapes ADD COLUMN IF NOT EXISTS matches_at    TEXT;

ALTER TABLE scrape_runs ADD COLUMN IF NOT EXISTS run_mode           TEXT;
ALTER TABLE scrape_runs ADD COLUMN IF NOT EXISTS target             TEXT;
ALTER TABLE scrape_runs ADD COLUMN IF NOT EXISTS source             TEXT;
ALTER TABLE scrape_runs ADD COLUMN IF NOT EXISTS competitions_found INTEGER DEFAULT 0;
ALTER TABLE scrape_runs ADD COLUMN IF NOT EXISTS matches_found      INTEGER DEFAULT 0;
ALTER TABLE scrape_runs ADD COLUMN IF NOT EXISTS matches_stored     INTEGER DEFAULT 0;
ALTER TABLE scrape_runs ADD COLUMN IF NOT EXISTS details_fetched    INTEGER DEFAULT 0;
ALTER TABLE scrape_runs ADD COLUMN IF NOT EXISTS error              TEXT;
ALTER TABLE scrape_runs ADD COLUMN IF NOT EXISTS started_at         TEXT;
-- match change counter (bumped by the upsert layer on meaningful changes;
-- see scraper/db/database.py - drives SSE event versioning)
ALTER TABLE matches ADD COLUMN IF NOT EXISTS data_version BIGINT NOT NULL DEFAULT 1;
ALTER TABLE scrape_runs ADD COLUMN IF NOT EXISTS finished_at        TEXT;

-- ===========================================================================
-- PASS 3 - INDEXES
-- Must run AFTER pass 2: indexes referencing columns that only exist in the
-- new schema (e.g. idx_players_current_club on players.current_club_id)
-- would fail on an old database otherwise.
-- ===========================================================================
CREATE INDEX IF NOT EXISTS idx_seasons_competition    ON seasons(competition_id);
CREATE INDEX IF NOT EXISTS idx_teams_name_en          ON teams(name_en);
CREATE INDEX IF NOT EXISTS idx_teams_name_ar          ON teams(name_ar);
CREATE INDEX IF NOT EXISTS idx_players_name_en        ON players(name_en);
CREATE INDEX IF NOT EXISTS idx_players_name_ar        ON players(name_ar);
CREATE INDEX IF NOT EXISTS idx_players_current_club   ON players(current_club_id);
CREATE INDEX IF NOT EXISTS idx_player_career_player   ON player_career_entries(player_id);
CREATE INDEX IF NOT EXISTS idx_player_career_team     ON player_career_entries(team_id);
CREATE INDEX IF NOT EXISTS idx_matches_date           ON matches(match_date);
CREATE INDEX IF NOT EXISTS idx_matches_listed_date    ON matches(listed_date);
CREATE INDEX IF NOT EXISTS idx_matches_kickoff        ON matches(kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_matches_competition    ON matches(competition_id);
CREATE INDEX IF NOT EXISTS idx_matches_gameset        ON matches(competition_id, gameset_id);
CREATE INDEX IF NOT EXISTS idx_matches_status         ON matches(status);
CREATE INDEX IF NOT EXISTS idx_matches_home_team      ON matches(home_team_id);
CREATE INDEX IF NOT EXISTS idx_matches_away_team      ON matches(away_team_id);
CREATE INDEX IF NOT EXISTS idx_events_match           ON match_events(match_id);
CREATE INDEX IF NOT EXISTS idx_events_player          ON match_events(player_id);
CREATE INDEX IF NOT EXISTS idx_events_type            ON match_events(event_type);
CREATE INDEX IF NOT EXISTS idx_lineups_player         ON lineups(player_id);
CREATE INDEX IF NOT EXISTS idx_lineups_team           ON lineups(team_id);
CREATE INDEX IF NOT EXISTS idx_standings_comp         ON standings(competition_id, season_id, stage);

-- ---------------------------------------------------------------------------
-- Query-path indexes (Fkoora live-data architecture). Every index below
-- corresponds to an actual API query pattern - nothing speculative:
--   * idx_matches_date_kickoff            - daily fixtures page:
--       WHERE match_date = $1 ORDER BY kickoff_utc
--   * idx_matches_competition_date_kickoff - competition/day listing:
--       WHERE competition_id = $1 AND match_date = $2 ORDER BY kickoff_utc
--   * idx_matches_live (PARTIAL)          - the live query:
--       WHERE status = 'LIVE' ORDER BY kickoff_utc  (status vocabulary is
--       FIXTURE / LIVE / RESULT / AET / PEN / CANCELLED - 'LIVE' is the
--       exact live marker the provider uses)
--   * idx_matches_upcoming (PARTIAL)      - fixtures about to kick off:
--       WHERE status = 'FIXTURE' (the provider's only upcoming status)
--   * idx_matches_home/away_team_kickoff  - team pages: recent results and
--       upcoming fixtures per team
--   * idx_match_events_match_sort         - match detail: events already
--       ordered for the details dialog
-- lineups (match_id, team_id) is already covered by the lineups PRIMARY KEY
-- (match_id, team_id, player_id) prefix; standings reads are covered by the
-- UNIQUE(competition_id, season_id, stage, table_name, position) constraint
-- index - deliberately NOT duplicated here.
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_matches_date_kickoff
    ON matches (match_date, kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_matches_competition_date_kickoff
    ON matches (competition_id, match_date, kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_matches_live
    ON matches (kickoff_utc, competition_id)
    WHERE status = 'LIVE';
CREATE INDEX IF NOT EXISTS idx_matches_upcoming
    ON matches (match_date, kickoff_utc, competition_id)
    WHERE status = 'FIXTURE';
CREATE INDEX IF NOT EXISTS idx_matches_home_team_kickoff
    ON matches (home_team_id, kickoff_utc DESC);
CREATE INDEX IF NOT EXISTS idx_matches_away_team_kickoff
    ON matches (away_team_id, kickoff_utc DESC);
CREATE INDEX IF NOT EXISTS idx_match_events_match_sort
    ON match_events (match_id, sort_order);
