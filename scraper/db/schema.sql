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
--   * The schema is applied idempotently on every start (CREATE TABLE IF NOT
--     EXISTS), so a fresh database needs no manual setup step.
-- ============================================================================

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
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Seasons
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS seasons (
    id             TEXT PRIMARY KEY,    -- sportfeeds season ID
    competition_id TEXT NOT NULL REFERENCES competitions(id),
    name           TEXT,                -- "2026/2027"
    is_active      INTEGER DEFAULT 0,
    first_seen_at  TEXT NOT NULL,
    last_seen_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_seasons_competition ON seasons(competition_id);

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
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_teams_name_en ON teams(name_en);
CREATE INDEX IF NOT EXISTS idx_teams_name_ar ON teams(name_ar);

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
    profile_fetched_at TEXT,             -- when the /player page was last pulled
    first_seen_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_players_name_en ON players(name_en);
CREATE INDEX IF NOT EXISTS idx_players_name_ar ON players(name_ar);
CREATE INDEX IF NOT EXISTS idx_players_current_club ON players(current_club_id);

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
CREATE INDEX IF NOT EXISTS idx_player_career_player ON player_career_entries(player_id);
CREATE INDEX IF NOT EXISTS idx_player_career_team ON player_career_entries(team_id);

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
    kickoff_utc      TEXT NOT NULL,     -- ISO-8601 kickoff time (UTC)
    match_date       TEXT NOT NULL,     -- UTC date of kickoff (YYYY-MM-DD)
    listed_date      TEXT,              -- fixtures-page date it was found on
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
    detail_fetched_at TEXT,             -- when lineups/events/stats were pulled
    last_updated_at  TEXT,              -- source lastUpdatedAt
    first_seen_at    TEXT NOT NULL,
    last_seen_at2    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_matches_date        ON matches(match_date);
CREATE INDEX IF NOT EXISTS idx_matches_listed_date ON matches(listed_date);
CREATE INDEX IF NOT EXISTS idx_matches_kickoff     ON matches(kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_matches_competition ON matches(competition_id);
CREATE INDEX IF NOT EXISTS idx_matches_gameset     ON matches(competition_id, gameset_id);
CREATE INDEX IF NOT EXISTS idx_matches_status      ON matches(status);
CREATE INDEX IF NOT EXISTS idx_matches_home_team   ON matches(home_team_id);
CREATE INDEX IF NOT EXISTS idx_matches_away_team   ON matches(away_team_id);

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
CREATE INDEX IF NOT EXISTS idx_events_match  ON match_events(match_id);
CREATE INDEX IF NOT EXISTS idx_events_player ON match_events(player_id);
CREATE INDEX IF NOT EXISTS idx_events_type   ON match_events(event_type);

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
CREATE INDEX IF NOT EXISTS idx_lineups_player ON lineups(player_id);
CREATE INDEX IF NOT EXISTS idx_lineups_team   ON lineups(team_id);

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
    created_at TEXT NOT NULL
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
    updated_at      TEXT,
    UNIQUE(competition_id, season_id, stage, table_name, position)
);
CREATE INDEX IF NOT EXISTS idx_standings_comp ON standings(competition_id, season_id, stage);

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
    standings_at    TEXT,
    matches_at      TEXT
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
    started_at          TEXT,
    finished_at         TEXT
);
