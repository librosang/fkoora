"""Central configuration for the football scraper.

goal.com serves the SAME data in ~40 locales. Entity IDs (matches, teams,
competitions, players) are identical across languages, so we scrape:

  * goal.com /en/...  -> English names + rich match details
  * goal.com /ar/...  -> Arabic names for competitions, teams, venues

Merging both languages by ID guarantees EN/AR consistency, because both
come from the same provider (sportfeeds.io) via the same site.

Page types per date:
  * past dates   -> /results/{date}        (finished matches)
  * today        -> /live-scores           (no date parameter)
  * future dates -> /fixtures/{date}       (upcoming)

kooora.com is kept as an OPTIONAL Arabic fallback (--kooora flag).
"""

from pathlib import Path
from urllib.parse import quote

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_URL = "postgresql://localhost:5432/football"
LOG_PATH = PROJECT_ROOT / "scraper.log"

# ---------------------------------------------------------------------------
# Source URLs - goal.com (primary, both languages)
# ---------------------------------------------------------------------------
GOAL_BASE = "https://www.goal.com"

# English
GOAL_RESULTS_URL = GOAL_BASE + "/en/results/{date}"
GOAL_FIXTURES_URL = GOAL_BASE + "/en/fixtures/{date}"
GOAL_LIVE_URL = GOAL_BASE + "/en/live-scores"

# Arabic (slugs: results / fixtures / live-scores)
GOAL_AR_RESULTS_URL = GOAL_BASE + "/ar/" + quote("النتائج") + "/{date}"
GOAL_AR_FIXTURES_URL = GOAL_BASE + "/ar/" + quote("مواعيد-المباريات") + "/{date}"
GOAL_AR_LIVE_URL = GOAL_BASE + "/ar/" + quote("مباريات-جارية-حاليًا")

# Match detail pages. The slug part of the URL is ignored by goal.com: only
# the match ID matters, so any placeholder works when we don't have the slug.
GOAL_MATCH_URL = GOAL_BASE + "/en/match/{slug}/{match_id}"
GOAL_AR_MATCH_URL = GOAL_BASE + "/ar/" + quote("المباراة") + "/{slug}/{match_id}"

# Player profile pages. Same convention as matches: slug is ignored, only the
# player ID matters. The page contains the bio (full name, Arabic name, photo,
# position, shirt number, height, age, country of birth, current club) plus
# the player's full club career history (every club joined + appearances +
# goals + season + competition).
GOAL_PLAYER_URL = GOAL_BASE + "/en/player/{slug}/{player_id}"
GOAL_AR_PLAYER_URL = GOAL_BASE + "/ar/" + quote("اللاعب") + "/{slug}/{player_id}"

# ---------------------------------------------------------------------------
# Source URLs - kooora.com (optional Arabic fallback only)
# ---------------------------------------------------------------------------
KOOORA_BASE = "https://www.kooora.com"
KOOORA_FIXTURES_URL = (
    KOOORA_BASE + "/%D9%83%D8%B1%D8%A9-%D8%A7%D9%84%D9%82%D8%AF%D9%85"
    "/%D9%85%D9%88%D8%A7%D8%B9%D9%8A%D8%AF-%D8%A7%D9%84%D9%85%D8%A8%D8%A7%D8%B1%D9%8A%D8%A7%D8%AA/{date}"
)

# ---------------------------------------------------------------------------
# HTTP behaviour
# ---------------------------------------------------------------------------
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
REQUEST_TIMEOUT = 45          # seconds per request
RATE_LIMIT_DELAY = 1.0        # base delay between requests (seconds)
RATE_LIMIT_JITTER = 0.5       # random jitter added to the delay
MAX_RETRIES = 3               # retries per request on transient errors
RETRY_BACKOFF = 2.0           # exponential backoff factor

# ---------------------------------------------------------------------------
# Slow / "bootstrap" mode
#
# The historical one-time walk (`python -m scraper.cli bootstrap --slow`)
# swaps the live RATE_LIMIT_DELAY / RATE_LIMIT_JITTER for the slower profile
# below while it is running, then restores the originals on exit. The
# inter-day pause is applied once per scraped day (after the listings +
# enrichment for that day finish), so over the full ~3652-day historical
# window the run is gentle enough to leave running unattended for hours.
#
# Tuning rationale:
#   * delay 2.5s + up to 1.5s jitter  -> effective ~3-4s between requests,
#     well within goal.com's tolerance for a long unattended walk.
#   * inter-day pause 3s             -> adds a breather between the small
#     burst of a single day's listings+AR listing (2 requests) plus any
#     enrichment hits, so consecutive days never look like a burst.
#   * past/future defaults           -> 10 years back + 1 year forward
#     covers the full historical record the project cares about plus a
#     forward fixture window so the daily updater has something to refresh.
# ---------------------------------------------------------------------------
SLOW_RATE_LIMIT_DELAY = 2.5
SLOW_RATE_LIMIT_JITTER = 1.5
BOOTSTRAP_DAY_PAUSE_SEC = 3.0
BOOTSTRAP_DEFAULT_YEARS_BACK = 10
BOOTSTRAP_DEFAULT_DAYS_AHEAD = 365
BOOTSTRAP_PROGRESS_LOG = "bootstrap.progress.log"

# ---------------------------------------------------------------------------
# Match-detail enrichment
#
# Detail pages (lineups, events, stats) cost one request per match, so by
# default we only fetch details for the big competitions. Pass --all on the
# CLI to enrich every match.
#
# Rules are (name_fragment, area_fragment_or_None).
#   * Prefix the name fragment with "=" to require an EXACT name match
#     (needed because many local leagues share names: 20+ "Premier League"s,
#     several "Bundesliga"s, "Northern Premier League", ...).
#   * Without "=", substring matching is used (cups, qualifiers, ...).
#   * area_fragment disambiguates by country/region; None = name is enough.
# ---------------------------------------------------------------------------
DEFAULT_COMPETITION_RULES = [
    ("=premier league", "england"),   # the English PL only (exactly)
    ("=laliga", None),                 # Spain (goal.com writes it as one word)
    ("=la liga", None),
    ("=serie a", "italy"),             # Italy (not Brazil/Ecuador Serie A)
    ("=bundesliga", "germany"),        # Germany (not Austria)
    ("=ligue 1", "france"),
    ("champions league", None),        # UEFA (all stages incl. qualification)
    ("europa league", None),
    ("conference league", None),
    ("europa conference", None),
    ("world cup", None),
    ("afcon", None),
    ("africa cup", None),
    ("nations league", None),
    ("saudi pro league", None),
    ("egyptian premier league", None),
    ("afc champions league", None),
    ("copa libertadores", None),
]

# Competitions whose NAME matches a rule above but are youth / reserve /
# women's / lower-division editions. Name fragments, case-insensitive.
DEFAULT_COMPETITION_EXCLUDE = [
    "u18", "u19", "u20", "u21", "u23",
    "youth", "reserve", "academy",
    "women", "feminine", "femenino", "femenil", "(w)",
    "division 2", "2. division", "2nd division",
]

# Statuses worth re-scraping / fetching details for
DETAIL_WORTHY_STATUSES = {"RESULT", "LIVE", "AET", "PEN"}

# Statuses meaning a match ENDED with a result (standings-affecting).
# A transition into one of these is the event that changes a league table,
# so the API uses it to refresh that competition's standings immediately
# instead of waiting for the next periodic warm cycle.
MATCH_ENDED_STATUSES = {"RESULT", "AET", "PEN"}

# How many days ahead the `upcoming` command looks by default
DEFAULT_UPCOMING_DAYS = 14
