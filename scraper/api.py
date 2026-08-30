"""JSON API server over the football PostgreSQL database - the backend of
the web frontend.

Architecture
------------
    goal.com EN+AR  --scrape-->  PostgreSQL (FOOTBALL_DB_URL)
                                     |
                                     |  SQL reads
                                     v
                       this API  (Flask, default :8000)
                         /api/matches     day listing   (bilingual, grouped)
                         /api/match/<id>  full detail   (events/lineups/stats)
                         /api/team/<id>   team profile  (results/fixtures/squad)
                         /api/player/<id> player profile (bio + career history)
                         /api/img?t=...   image proxy   (URLs stay hidden)
                         /api/cron/refresh  trigger a scheduled scrape run
                         /api/health      db stats + last runs
                                     |
                                     v
                       Next.js frontend (:3000) - pure consumer, never scrapes

Day listings are LOCAL-calendar correct for every user timezone: matches are
selected by kickoff falling inside the requester's local-day UTC window
(the same fix the frontend had, now done once, in SQL, over the database).

Every provider image URL is replaced by an opaque local path /api/img?t=...
before any JSON leaves the server. The token is a deterministic HMAC prefix;
the URL mapping lives in the image_tokens table and never reaches the client.

A built-in scheduler thread (disable with --no-schedule) keeps the database
fresh: today's listings every minute, neighbouring days every 5 minutes
while anything is happening (live match / kickoff soon) and every 30 minutes
otherwise, live match details every 2 minutes, and a slow backfill of
anything that is still missing details. Standings are refreshed EVENT-DRIVEN:
the moment a scrape observes a match finishing (LIVE -> RESULT/AET/PEN), the
leagues users actually open are re-scraped in the background (~1 min after
the final whistle); a slow warm cycle is only the fallback for corrections,
postponements and missed events. External crons can call
/api/cron/refresh instead.

Load discipline (goal.com request volume):
  * EN pages are the fast lane (scores/status are language-independent);
    AR pages only carry names, which change a few times per season - so the
    Arabic listing/detail pages are fetched on slow AR cycles
    (AR_LISTING_SEC / AR_DETAIL_SEC, default 10 min) instead of every
    scrape. This roughly halves the hottest request paths.
  * When several API processes run against one database (scale-out), the
    scheduler elects a single LEADER via a PostgreSQL advisory lock - the
    others stand by. Otherwise every replica would multiply the goal.com
    traffic. (SCHEDULER_ROLE=auto|force|off, API_ENABLE_SCHEDULER=0.)

Conditional responses: the JSON endpoints emit strong ETags and answer
If-None-Match with 304, so browser polls collapse to a few hundred bytes
while the data is unchanged - and the Next.js proxies forward the header,
making the whole browser -> Next -> API chain revalidation-only.

Run:
    python -m scraper.cli api --port 8000
    python -m scraper.cli api --no-schedule      # external crontab mode
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import date as date_cls, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg
import requests as rq
from flask import Flask, Response, g, jsonify, request
from urllib.parse import urlsplit

from . import config
from .db import backend
from .db.database import Database, utcnow
from . import goal_order
from .major import is_major_competition

log = logging.getLogger("scraper.api")

# ---------------------------------------------------------------------------
# environment / configuration
# ---------------------------------------------------------------------------
API_DB_URL = os.environ.get("FOOTBALL_DB_URL", config.DEFAULT_DB_URL)

# shared secret for the image tokens + the cron endpoint
IMAGE_SECRET = os.environ.get("IMAGE_PROXY_SECRET", "match-center-image-proxy-secret")
CRON_SECRET = os.environ.get("API_CRON_SECRET", "")           # empty = open

IMG_ALLOWED_HOST_SUFFIXES = (".sportfeeds.io", ".goal.com")
IMG_CACHE_DIR = Path(os.environ.get("IMG_CACHE_DIR",
                                    str(config.PROJECT_ROOT / "img_cache")))
IMG_MAX_BYTES = 8 * 1024 * 1024

# scheduler intervals (seconds); 0 disables a job
REFRESH_TODAY_SEC = int(os.environ.get("REFRESH_TODAY_SEC", "60"))
REFRESH_AROUND_SEC = int(os.environ.get("REFRESH_AROUND_SEC", "300"))
ENRICH_LIVE_SEC = int(os.environ.get("ENRICH_LIVE_SEC", "120"))
ENRICH_BACKFILL_SEC = int(os.environ.get("ENRICH_BACKFILL_SEC", "1800"))
LIVE_ENRICH_MAX = int(os.environ.get("LIVE_ENRICH_MAX", "60"))

# --- goal.com load reduction ----------------------------------------------
# Scores and statuses are IDENTICAL in every language - only names differ,
# and names change a few times per season. The hot scrape paths therefore
# fetch the EN page every cycle (fast data) and the AR page only on slow AR
# cycles: listing pages every AR_LISTING_SEC, live match detail pages every
# AR_DETAIL_SEC. The EN cadence is untouched, so freshness is unaffected.
AR_LISTING_SEC = int(os.environ.get("AR_LISTING_SEC", "600"))
AR_DETAIL_SEC = int(os.environ.get("AR_DETAIL_SEC", "600"))
# Neighbouring days (yesterday results / tomorrow fixtures) only need the
# fast cadence while something is actually happening - a match live now or
# a kickoff within AROUND_ACTIVE_LOOKAHEAD_SEC. Otherwise they drop to the
# idle cadence (overnight, quiet weekdays).
AROUND_ACTIVE_LOOKAHEAD_SEC = int(os.environ.get("AROUND_ACTIVE_LOOKAHEAD_SEC",
                                                  "43200"))          # 12h
REFRESH_AROUND_IDLE_SEC = int(os.environ.get("REFRESH_AROUND_IDLE_SEC", "1800"))
# On-demand one-shot fetches (a date or match detail nobody asked for yet)
# are remembered for this long before they may be retried - a FAILED fetch
# no longer blocks that data until the next process restart.
ON_DEMAND_RETRY_SEC = int(os.environ.get("ON_DEMAND_RETRY_SEC", "600"))

# --- scheduler leadership ---------------------------------------------------
# With several API processes against one database, only ONE may run the
# scrape scheduler (the others would multiply goal.com requests). Leadership
# is elected with a PostgreSQL advisory lock: the winner scrapes, standbys
# re-check periodically and take over automatically if the leader dies.
#   auto  (default) - participate in the election
#   force           - always run the scheduler (dedicated scraper box)
#   off             - never run it (same as --no-schedule / API_ENABLE_SCHEDULER=0)
SCHEDULER_ROLE = os.environ.get("SCHEDULER_ROLE", "auto").strip().lower()
SCHED_LOCK_KEY = int(os.environ.get("SCHED_LOCK_KEY", "708112283"))

# In-memory LRU for served images (hot crests/logos - avoids a disk read
# per request under load). Size in MB; 0 disables the memory layer.
IMG_MEM_CACHE_BYTES = int(os.environ.get("IMG_MEM_CACHE_MB", "128")) * 1024 * 1024

# provider listing pages are grouped by the UTC+8 calendar day
PAGE_GROUP_TZ = timezone(timedelta(hours=8))

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# NOTE: day-listing competition ordering now lives in scraper/goal_order.py
# (goal.com's own featured order + alphabetical-by-area tail).

# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def utc_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def day_type_for(date: str, today: str) -> str:
    if date < today:
        return "past"
    if date > today:
        return "future"
    return "today"


def local_day_window(date: str, tz_min: int) -> Tuple[str, str]:
    """The user's local day `date` as a UTC ISO window [start, end)."""
    d = datetime.fromisoformat(date + "T00:00:00+00:00")
    start = d - timedelta(minutes=tz_min)
    end = start + timedelta(days=1)
    return start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")


def page_dates_for_local_day(date: str, tz_min: int) -> List[str]:
    """Which UTC+8 page dates can contain the user's local day."""
    start = datetime.fromisoformat(date + "T00:00:00+00:00") - timedelta(minutes=tz_min)
    end = start + timedelta(days=1)
    first = (start.astimezone(PAGE_GROUP_TZ)).date().isoformat()
    last = ((end - timedelta(seconds=1)).astimezone(PAGE_GROUP_TZ)).date().isoformat()
    return [first] if first == last else [first, last]


def _norm_num(v: Any) -> Any:
    """DOUBLE PRECISION -> int when integral (keeps JSON clean: 55 not 55.0)."""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


# ---------------------------------------------------------------------------
# conditional responses (ETag / If-None-Match)
# ---------------------------------------------------------------------------
# Timestamps that change on every rebuild even when the content is identical
# are excluded from the tag, so an unchanged payload keeps its ETag and
# browser polls collapse to 304s instead of re-downloading the body. NOTE:
# the `refreshing` flag is intentionally NOT excluded - it changes what the
# client must do (keep polling), so it must bust the tag.
_ETAG_VOLATILE_KEYS = ("generatedAt",)


def _content_etag(payload: Dict[str, Any]) -> str:
    """Strong, deterministic ETag for a JSON payload (volatile keys skipped)."""
    core = {k: v for k, v in payload.items() if k not in _ETAG_VOLATILE_KEYS}
    body = json.dumps(core, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return '"' + hashlib.sha256(body.encode("utf-8")).hexdigest()[:32] + '"'


def _etag_response(payload: Dict[str, Any], etag: Optional[str] = None) -> Response:
    """200 + ETag, or 304 when the client already holds this exact payload.

    `Cache-Control: no-cache` (NOT no-store) keeps the response storable so
    the browser revalidates every poll with If-None-Match - unchanged data
    costs a few hundred bytes instead of the full JSON body, and the Next.js
    proxy forwards the header, so the API<->Next hop collapses too.
    """
    tag = etag or _content_etag(payload)
    if tag in (request.headers.get("If-None-Match") or ""):
        resp = Response(status=304)
    else:
        resp = jsonify(payload)
    resp.headers["ETag"] = tag
    resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---------------------------------------------------------------------------
# image token layer (server-side URL hiding)
# ---------------------------------------------------------------------------
_known_tokens: Dict[str, str] = {}          # in-process memo: token -> url
_token_lock = threading.Lock()


def _make_token(url: str) -> str:
    return hmac.new(IMAGE_SECRET.encode(), url.encode(), hashlib.sha256).hexdigest()[:32]


def img_path(conn: psycopg.Connection, url: Optional[str]) -> Optional[str]:
    """Rewrite a provider image URL into the opaque local proxy path."""
    if not url or not url.startswith("https://"):
        return None
    token = _make_token(url)
    with _token_lock:
        if token not in _known_tokens:
            try:
                conn.execute(
                    """INSERT INTO image_tokens (token, url, created_at)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (token) DO NOTHING""",
                    (token, url, utcnow()),
                )
                conn.commit()
            except psycopg.Error:
                pass                              # read-only use still works
            _known_tokens[token] = url
    return f"/api/img?t={token}"


def _img_host_allowed(hostname: str) -> bool:
    h = hostname.lower()
    return any(h == s[1:] or h.endswith(s) for s in IMG_ALLOWED_HOST_SUFFIXES)


_EXT_BY_TYPE = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/jpg": ".jpg",
    "image/gif": ".gif", "image/webp": ".webp", "image/svg+xml": ".svg",
    "image/x-icon": ".ico", "image/vnd.microsoft.icon": ".ico",
}
_TYPE_BY_EXT = {ext: ctype for ctype, ext in _EXT_BY_TYPE.items()}

_download_sem = threading.Semaphore(8)      # bounded parallel CDN downloads
_token_locks: Dict[str, threading.Lock] = {}
_token_locks_guard = threading.Lock()


def _lock_for(token: str) -> threading.Lock:
    with _token_locks_guard:
        if token not in _token_locks:
            _token_locks[token] = threading.Lock()
        return _token_locks[token]


def _fetch_image(token: str, url: str) -> Optional[Path]:
    """Download (once) into the disk cache and return the file path."""
    IMG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = list(IMG_CACHE_DIR.glob(token + ".*"))
    if cached:
        return cached[0]

    with _lock_for(token):                     # one download per image
        cached = list(IMG_CACHE_DIR.glob(token + ".*"))
        if cached:                              # raced with another thread
            return cached[0]

        with _download_sem:                     # bounded CDN concurrency
            try:
                resp = rq.get(
                    url,
                    headers={"User-Agent": config.USER_AGENT, "Accept": "image/*"},
                    timeout=20,
                )
            except rq.RequestException:
                return None
            if resp.status_code != 200:
                return None
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype not in _EXT_BY_TYPE or len(resp.content) > IMG_MAX_BYTES:
                return None

            path = IMG_CACHE_DIR / (token + _EXT_BY_TYPE[ctype])
            tmp = path.with_suffix(path.suffix + ".part")
            tmp.write_bytes(resp.content)
            tmp.replace(path)
            return path


# ---------------------------------------------------------------------------
# JSON row builders (shape = the frontend's TypeScript types, camelCase)
# ---------------------------------------------------------------------------
def _team_ref(conn: psycopg.Connection, team_row) -> Optional[Dict[str, Any]]:
    if team_row is None:
        return None
    return {
        "id": team_row["id"],
        "nameEn": team_row["name_en"],
        "nameAr": team_row["name_ar"],
        "shortNameEn": team_row["short_name_en"],
        "code": team_row["code"],
        "crestUrl": img_path(conn, team_row["crest_url"]),
    }


def _competition_json(conn: psycopg.Connection, cid: str, name_en, name_ar,
                      area_en, area_ar, area_code, image_url) -> Dict[str, Any]:
    return {
        "id": cid,
        "nameEn": name_en,
        "nameAr": name_ar,
        "areaNameEn": area_en,
        "areaNameAr": area_ar,
        "areaCode": area_code,
        "imageUrl": img_path(conn, image_url),
    }


_LISTING_SQL = """
SELECT m.id, m.kickoff_utc, m.status, m.period,
       m.home_score, m.away_score, m.home_agg_score, m.away_agg_score,
       m.home_red_cards, m.away_red_cards,
       m.round_name, m.gameset_name, m.gameset_name_ar,
       m.slug_en, m.slug_ar,
       v.name_en AS venue_en, v.name_ar AS venue_ar,
       c.id AS c_id, c.name_en AS c_name_en, c.name_ar AS c_name_ar,
       c.area_name_en AS c_area_en, c.area_name_ar AS c_area_ar, c.area_code AS c_area_code,
       c.image_url AS c_image,
       th.id AS h_id, th.name_en AS h_name_en, th.name_ar AS h_name_ar,
       th.short_name_en AS h_short, th.code AS h_code, th.crest_url AS h_crest,
       ta.id AS a_id, ta.name_en AS a_name_en, ta.name_ar AS a_name_ar,
       ta.short_name_en AS a_short, ta.code AS a_code, ta.crest_url AS a_crest
FROM matches m
JOIN competitions c ON c.id = m.competition_id
JOIN teams th ON th.id = m.home_team_id
JOIN teams ta ON ta.id = m.away_team_id
LEFT JOIN venues v ON v.id = m.venue_id
WHERE m.kickoff_utc >= %(start)s AND m.kickoff_utc < %(end)s
ORDER BY m.kickoff_utc, m.id
"""


def _row_to_team(prefix: str, r) -> Dict[str, Any]:
    return {
        "id": r[f"{prefix}_id"],
        "nameEn": r[f"{prefix}_name_en"],
        "nameAr": r[f"{prefix}_name_ar"],
        "shortNameEn": r[f"{prefix}_short"],
        "code": r[f"{prefix}_code"],
    }


def _match_row(conn: psycopg.Connection, r, comp_json: Dict[str, Any]) -> Dict[str, Any]:
    row = _row_to_team("h", r)
    row["crestUrl"] = img_path(conn, r["h_crest"])
    home = row
    away = _row_to_team("a", r)
    away["crestUrl"] = img_path(conn, r["a_crest"])
    return {
        "matchId": r["id"],
        "kickoffUtc": r["kickoff_utc"],
        "status": r["status"],
        "period": r["period"],
        "homeTeam": home,
        "awayTeam": away,
        "competition": comp_json,
        "homeScore": r["home_score"],
        "awayScore": r["away_score"],
        "homeAggScore": r["home_agg_score"],
        "awayAggScore": r["away_agg_score"],
        "homeRedCards": r["home_red_cards"] or 0,
        "awayRedCards": r["away_red_cards"] or 0,
        "roundName": r["round_name"],
        "gamesetName": r["gameset_name"],
        "gamesetNameAr": r["gameset_name_ar"],
        "venueNameEn": r["venue_en"],
        "venueNameAr": r["venue_ar"],
        "slugEn": r["slug_en"],
        "slugAr": r["slug_ar"],
    }


def build_listing(conn: psycopg.Connection, date: str, today: str,
                  major_only: bool, tz_min: int) -> Dict[str, Any]:
    """Grouped day listing from the database (user's local calendar)."""
    start, end = local_day_window(date, tz_min)
    rows = conn.execute(_LISTING_SQL, {"start": start, "end": end}).fetchall()

    groups: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    comp_major: Dict[str, bool] = {}

    for r in rows:
        cid = r["c_id"]
        if cid not in groups:
            comp_json = _competition_json(
                conn, cid, r["c_name_en"], r["c_name_ar"],
                r["c_area_en"], r["c_area_ar"], r["c_area_code"], r["c_image"],
            )
            groups[cid] = {"competition": comp_json, "matches": []}
            order.append(cid)
            comp_major[cid] = is_major_competition({
                "name_en": r["c_name_en"], "name_ar": r["c_name_ar"],
                "area_name_en": r["c_area_en"],
            })
        groups[cid]["matches"].append(_match_row(conn, r, groups[cid]["competition"]))

    def _sort_key(cid: str):
        # goal.com ordering: their own featured-competition order first,
        # then alphabetical by area (country) - exactly how their scores
        # pages order the day's competitions.
        #
        # LIVE MATCHES NEVER INFLUENCE THIS ORDER. goal.com keeps the
        # league list identical whether or not a league has live matches
        # right now: a famous league with no games today still sits at its
        # usual place at the top. Live state is a per-match visual concern
        # (badge / minute / pulsing score), never a league sort key.
        comp = groups[cid]["competition"]
        return goal_order.goal_sort_key({
            "name_en": comp["nameEn"],
            "name_ar": comp["nameAr"],
            "area_name_en": comp["areaNameEn"],
        })

    ordered = sorted(order, key=_sort_key)
    out_groups = []
    for cid in ordered:
        if major_only and not comp_major[cid]:
            continue
        out_groups.append({**groups[cid], "isMajor": comp_major[cid]})

    return {
        "date": date,
        "dayType": day_type_for(date, today),
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "totalMatches": sum(len(g["matches"]) for g in out_groups),
        "groups": out_groups,
    }


# ---------------------------------------------------------------------------
# match detail
# ---------------------------------------------------------------------------
_DETAIL_MATCH_SQL = """
SELECT m.*, c.name_en AS c_name_en, c.name_ar AS c_name_ar,
       c.area_name_en AS c_area_en, c.area_name_ar AS c_area_ar,
       c.area_code AS c_area_code, c.image_url AS c_image,
       s.name AS season_name,
       v.name_en AS venue_en, v.name_ar AS venue_ar
FROM matches m
JOIN competitions c ON c.id = m.competition_id
LEFT JOIN seasons s ON s.id = m.season_id
LEFT JOIN venues v ON v.id = m.venue_id
WHERE m.id = %(mid)s
"""

_DETAIL_TEAM_SQL = "SELECT * FROM teams WHERE id = %s"


def build_detail(conn: psycopg.Connection, match_id: str) -> Optional[Dict[str, Any]]:
    m = conn.execute(_DETAIL_MATCH_SQL, {"mid": match_id}).fetchone()
    if m is None:
        return None

    comp_json = _competition_json(
        conn, m["competition_id"], m["c_name_en"], m["c_name_ar"],
        m["c_area_en"], m["c_area_ar"], m["c_area_code"], m["c_image"],
    )
    home = _team_ref(conn, conn.execute(_DETAIL_TEAM_SQL, (m["home_team_id"],)).fetchone())
    away = _team_ref(conn, conn.execute(_DETAIL_TEAM_SQL, (m["away_team_id"],)).fetchone())

    # ---- events --------------------------------------------------------
    events = []
    for ev in conn.execute(
        "SELECT * FROM match_events WHERE match_id = %s ORDER BY sort_order", (match_id,)
    ).fetchall():
        events.append({
            "teamSide": ev["team_side"],
            "eventType": ev["event_type"],
            "minute": ev["minute"],
            "extraMinute": ev["extra_minute"],
            "player": {"id": ev["player_id"], "nameEn": ev["player_name_en"],
                       "nameAr": ev["player_name_ar"]},
            "relatedPlayer": {"id": ev["related_player_id"],
                              "nameEn": ev["related_player_name_en"],
                              "nameAr": ev["related_player_name_ar"]},
            "homeScoreAfter": ev["home_score_after"],
            "awayScoreAfter": ev["away_score_after"],
            "outcome": ev["outcome"],
            "decision": ev["decision"],
        })

    # ---- lineups + managers --------------------------------------------
    def _side(team_id: str, formation: Optional[str]) -> Optional[Dict[str, Any]]:
        entries = []
        for e in conn.execute(
            """SELECT l.*, p.name_en AS p_en, p.name_ar AS p_ar
               FROM lineups l LEFT JOIN players p ON p.id = l.player_id
               WHERE l.match_id = %s AND l.team_id = %s
               ORDER BY l.is_starter DESC, l.shirt_number IS NULL, l.shirt_number""",
            (match_id, team_id),
        ).fetchall():
            entries.append({
                "person": {"id": e["player_id"], "nameEn": e["p_en"], "nameAr": e["p_ar"]},
                "isStarter": bool(e["is_starter"]),
                "shirtNumber": e["shirt_number"],
                "isCaptain": bool(e["is_captain"]),
                "positionX": e["position_x"],
                "positionY": e["position_y"],
                "rating": _norm_num(e["rating"]),
            })
        if not entries:
            return None
        mgr = conn.execute(
            "SELECT * FROM match_managers WHERE match_id = %s AND team_id = %s",
            (match_id, team_id),
        ).fetchone()
        return {
            "teamId": team_id,
            "formation": formation,
            "manager": ({"id": mgr["manager_id"], "nameEn": mgr["manager_name_en"],
                         "nameAr": mgr["manager_name_ar"]} if mgr else
                        {"id": None, "nameEn": None, "nameAr": None}),
            "entries": entries,
        }

    lineups_home = _side(m["home_team_id"], m["home_formation"])
    lineups_away = _side(m["away_team_id"], m["away_formation"])

    # ---- stats (re-assemble the per-team rows into home/away pairs) -----
    stats: List[Dict[str, Any]] = []
    stat_rows = conn.execute(
        """SELECT stat_type,
                  MAX(CASE WHEN team_id = %(home)s THEN value END) AS home_value,
                  MAX(CASE WHEN team_id = %(away)s THEN value END) AS away_value
           FROM team_match_stats WHERE match_id = %(mid)s
           GROUP BY stat_type
           ORDER BY MIN(id)""",
        {"mid": match_id, "home": m["home_team_id"], "away": m["away_team_id"]},
    ).fetchall()
    for s in stat_rows:
        if s["home_value"] is None and s["away_value"] is None:
            continue
        stats.append({
            "statType": s["stat_type"],
            "homeValue": _norm_num(s["home_value"]),
            "awayValue": _norm_num(s["away_value"]),
        })

    return {
        "matchId": m["id"],
        "kickoffUtc": m["kickoff_utc"],
        "status": m["status"],
        "period": m["period"],
        "homeTeam": home,
        "awayTeam": away,
        "homeScore": m["home_score"],
        "awayScore": m["away_score"],
        "homePenScore": m["home_pen_score"],
        "awayPenScore": m["away_pen_score"],
        "homeScoreHt": m["home_score_ht"],
        "awayScoreHt": m["away_score_ht"],
        "competition": comp_json,
        "roundName": m["round_name"],
        "seasonName": m["season_name"],
        "venueNameEn": m["venue_en"],
        "venueNameAr": m["venue_ar"],
        "referee": m["referee"],
        "events": events,
        "lineups": {
            "confirmed": bool(m["lineups_confirmed"]) and bool(
                (lineups_home and lineups_home["entries"]) or
                (lineups_away and lineups_away["entries"])
            ),
            "home": lineups_home,
            "away": lineups_away,
        },
        "stats": stats,
    }


# ---------------------------------------------------------------------------
# competition payload (standings + rounds) for the competition dialog
# ---------------------------------------------------------------------------

# Arabic display names for the common standing zone markers (fallbacks to
# the provider's English legend name when unknown).
MARKER_AR_NAMES = {
    "CHAMPIONS_LEAGUE": "دوري الأبطال",
    "UEFA_CUP": "الدوري الأوروبي",
    "EUROPA_LEAGUE": "الدوري الأوروبي",
    "EUROPA_CONF_LEAGUE": "مؤتمر أوروبا",
    "EUROPA_CONF_LEAGUE_QUAL": "تصفيات مؤتمر أوروبا",
    "RELEGATION": "الهبوط",
    "PROMOTION": "التأهل",
    "CHAMPIONS_LEAGUE_QUAL": "تصفيات دوري الأبطال",
    "CHAMPIONS_LEAGUE_Q": "تصفيات دوري الأبطال",
    "AFC_CHAMPIONS_LEAGUE": "دوري أبطال آسيا",
    "CAF_CHAMPIONS_LEAGUE": "دوري أبطال أفريقيا",
    "CAF_CONFEDERATION_CUP": "الكأس الكونفدرالية",
    "PLAYOFF": "ملحق",
    "CHAMPIONSHIP_PLAYOFF": "ملحق الصعود",
}

_COMP_MATCH_SQL = """
SELECT m.id, m.kickoff_utc, m.status, m.period,
       m.home_score, m.away_score, m.home_agg_score, m.away_agg_score,
       m.home_red_cards, m.away_red_cards,
       m.round_name, m.gameset_name, m.gameset_name_ar,
       m.slug_en, m.slug_ar,
       v.name_en AS venue_en, v.name_ar AS venue_ar,
       th.id AS h_id, th.name_en AS h_name_en, th.name_ar AS h_name_ar,
       th.short_name_en AS h_short, th.code AS h_code, th.crest_url AS h_crest,
       ta.id AS a_id, ta.name_en AS a_name_en, ta.name_ar AS a_name_ar,
       ta.short_name_en AS a_short, ta.code AS a_code, ta.crest_url AS a_crest
FROM matches m
JOIN teams th ON th.id = m.home_team_id
JOIN teams ta ON ta.id = m.away_team_id
LEFT JOIN venues v ON v.id = m.venue_id
WHERE m.competition_id = %(cid)s {extra}
ORDER BY m.kickoff_utc, m.id
"""


def _gameset_json(r) -> Dict[str, Any]:
    return {
        "gameSetTypeId": r["game_set_type_id"],
        "nameEn": r["name_en"],
        "nameAr": r["name_ar"],
        "isActive": bool(r["is_active"]),
        "sortOrder": r["sort_order"],
        "matchCount": r["match_count"],
    }


def build_competition(conn: psycopg.Connection, comp_id: str) -> Optional[Dict[str, Any]]:
    """Competition info + standings + gameset list for the dialog."""
    c = conn.execute(
        "SELECT * FROM competitions WHERE id = %s", (comp_id,)
    ).fetchone()
    if c is None:
        return None

    scrape = conn.execute(
        "SELECT * FROM competition_scrapes WHERE competition_id = %s", (comp_id,)
    ).fetchone()
    season_id = scrape["season_id"] if scrape else None

    season_json = None
    if season_id:
        srow = conn.execute(
            "SELECT id, name FROM seasons WHERE id = %s", (season_id,)
        ).fetchone()
        if srow:
            season_json = {"id": srow["id"], "name": srow["name"]}

    comp_json = _competition_json(
        conn, c["id"], c["name_en"], c["name_ar"],
        c["area_name_en"], c["area_name_ar"], c["area_code"], c["image_url"],
    )

    # standings (rows grouped back into their provider tables)
    standings_json = None
    if scrape and scrape["has_standings"]:
        rows = conn.execute(
            """SELECT s.*, t.name_en AS team_name_en, t.name_ar AS team_name_ar,
                      t.short_name_en AS team_short_en, t.code AS team_code,
                      t.crest_url AS team_crest
               FROM standings s JOIN teams t ON t.id = s.team_id
               WHERE s.competition_id = %s AND s.stage = 'total'
               ORDER BY s.table_name IS NULL DESC, s.table_name, s.position""",
            (comp_id,),
        ).fetchall()
        if rows:
            tables: List[Dict[str, Any]] = []
            current_name = "__first__"
            table_rows: List[Dict[str, Any]] = []
            for r in rows:
                if r["table_name"] != current_name:
                    if table_rows:
                        tables.append({"name": None if current_name == "__none__" else current_name,
                                       "rows": table_rows})
                    current_name = r["table_name"] if r["table_name"] is not None else "__none__"
                    table_rows = []
                table_rows.append({
                    "position": r["position"],
                    "team": {
                        "id": r["team_id"],
                        "nameEn": r["team_name_en"],
                        "nameAr": r["team_name_ar"],
                        "shortNameEn": r["team_short_en"],
                        "code": r["team_code"],
                        "crestUrl": img_path(conn, r["team_crest"]),
                    },
                    "played": r["played"],
                    "win": r["win"],
                    "draw": r["draw"],
                    "lose": r["lose"],
                    "goalsFor": r["goals_for"],
                    "goalsAgainst": r["goals_against"],
                    "goalDiff": r["goal_diff"],
                    "points": r["points"],
                    "form": [
                        {"wdl": f.get("wdl"), "matchId": f.get("match_id")}
                        for f in json.loads(r["form_json"] or "[]")
                    ],
                    "markers": json.loads(r["markers_json"] or "[]"),
                })
            if table_rows:
                tables.append({"name": None if current_name == "__none__" else current_name,
                               "rows": table_rows})

            markers = []
            for mrow in conn.execute(
                """SELECT marker_id, name, type FROM standings_markers
                   WHERE competition_id = %s""", (comp_id,)
            ).fetchall():
                markers.append({
                    "id": mrow["marker_id"],
                    "nameEn": mrow["name"],
                    "nameAr": MARKER_AR_NAMES.get(mrow["marker_id"]),
                    "type": mrow["type"],
                })
            standings_json = {"tables": tables, "markers": markers}

    gamesets = [_gameset_json(r) for r in conn.execute(
        """SELECT g.*, (
               SELECT COUNT(*) FROM matches m
               WHERE m.competition_id = g.competition_id
                 AND m.gameset_id = g.game_set_type_id
           ) AS match_count
           FROM gamesets g WHERE g.competition_id = %s
           ORDER BY g.sort_order, g.id""", (comp_id,)
    ).fetchall()]

    return {
        "competition": comp_json,
        "season": season_json,
        "standings": standings_json,
        "gamesets": gamesets,
        "generatedAt": utcnow(),
    }


def build_competition_matches(conn: psycopg.Connection, comp_id: str,
                              gameset_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """One gameset's matches (or the active round's when gameset is None)."""
    comp = conn.execute(
        "SELECT id FROM competitions WHERE id = %s", (comp_id,)
    ).fetchone()
    if comp is None:
        return None

    gameset_row = None
    if gameset_id:
        gameset_row = conn.execute(
            "SELECT * FROM gamesets WHERE competition_id = %s AND game_set_type_id = %s",
            (comp_id, gameset_id),
        ).fetchone()
        if gameset_row is None:
            return None
    else:
        gameset_row = conn.execute(
            """SELECT * FROM gamesets WHERE competition_id = %s
               ORDER BY is_active DESC, sort_order LIMIT 1""", (comp_id,)
        ).fetchone()
        if gameset_row is None:
            return None

    c = conn.execute("SELECT * FROM competitions WHERE id = %s", (comp_id,)).fetchone()
    comp_json = _competition_json(
        conn, c["id"], c["name_en"], c["name_ar"],
        c["area_name_en"], c["area_name_ar"], c["area_code"], c["image_url"],
    )

    extra = "AND m.gameset_id = %(gsid)s"
    rows = conn.execute(
        _COMP_MATCH_SQL.format(extra=extra),
        {"cid": comp_id, "gsid": gameset_row["game_set_type_id"]},
    ).fetchall()

    # count query for matchCount without the join weight
    match_count = conn.execute(
        "SELECT COUNT(*) AS n FROM matches WHERE competition_id = %s AND gameset_id = %s",
        (comp_id, gameset_row["game_set_type_id"]),
    ).fetchone()["n"]

    return {
        "gameset": {
            "gameSetTypeId": gameset_row["game_set_type_id"],
            "nameEn": gameset_row["name_en"],
            "nameAr": gameset_row["name_ar"],
            "isActive": bool(gameset_row["is_active"]),
            "sortOrder": gameset_row["sort_order"],
            "matchCount": match_count,
        },
        "competition": comp_json,
        "matches": [_match_row(conn, r, comp_json) for r in rows],
    }


# ---------------------------------------------------------------------------
# team payload (profile + recent results + upcoming fixtures + squad) for the
# team dialog / team page
# ---------------------------------------------------------------------------

# how many finished matches / upcoming fixtures the team payload carries
TEAM_RECENT_LIMIT = 8
TEAM_UPCOMING_LIMIT = 8

_TEAM_MATCH_SQL = """
SELECT m.id, m.kickoff_utc, m.status, m.period,
       m.home_score, m.away_score, m.home_agg_score, m.away_agg_score,
       m.home_red_cards, m.away_red_cards,
       m.round_name, m.gameset_name, m.gameset_name_ar,
       m.slug_en, m.slug_ar,
       v.name_en AS venue_en, v.name_ar AS venue_ar,
       c.id AS c_id, c.name_en AS c_name_en, c.name_ar AS c_name_ar,
       c.area_name_en AS c_area_en, c.area_name_ar AS c_area_ar, c.area_code AS c_area_code,
       c.image_url AS c_image,
       th.id AS h_id, th.name_en AS h_name_en, th.name_ar AS h_name_ar,
       th.short_name_en AS h_short, th.code AS h_code, th.crest_url AS h_crest,
       ta.id AS a_id, ta.name_en AS a_name_en, ta.name_ar AS a_name_ar,
       ta.short_name_en AS a_short, ta.code AS a_code, ta.crest_url AS a_crest
FROM matches m
JOIN competitions c ON c.id = m.competition_id
JOIN teams th ON th.id = m.home_team_id
JOIN teams ta ON ta.id = m.away_team_id
LEFT JOIN venues v ON v.id = m.venue_id
WHERE (m.home_team_id = %(tid)s OR m.away_team_id = %(tid)s)
"""


def _team_match_rows(conn: psycopg.Connection, team_id: str,
                     extra: str, params: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    """Team match list under the shared row builder (adds crestUrl + comp)."""
    rows = conn.execute(_TEAM_MATCH_SQL + extra + " LIMIT " + str(limit), params).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        comp_json = _competition_json(
            conn, r["c_id"], r["c_name_en"], r["c_name_ar"],
            r["c_area_en"], r["c_area_ar"], r["c_area_code"], r["c_image"],
        )
        out.append(_match_row(conn, r, comp_json))
    return out


def build_team(conn: psycopg.Connection, team_id: str) -> Optional[Dict[str, Any]]:
    """Team profile: ref + recent results + upcoming fixtures + known squad."""
    team_row = conn.execute("SELECT * FROM teams WHERE id = %s", (team_id,)).fetchone()
    if team_row is None:
        return None

    team = _team_ref(conn, team_row)

    # finished / live / abandoned matches, most recent first. kickoff_utc is
    # ISO-8601 TEXT so it sorts chronologically on its own.
    recent = _team_match_rows(
        conn, team_id,
        " AND m.status != 'FIXTURE' ORDER BY m.kickoff_utc DESC, m.id",
        {"tid": team_id}, TEAM_RECENT_LIMIT,
    )
    # upcoming fixtures, soonest first (kickoff in the future OR kickoff not
    # yet passed today; a delayed fixture whose time passed stays visible)
    upcoming = _team_match_rows(
        conn, team_id,
        " AND m.status = 'FIXTURE' AND m.kickoff_utc >= %(now)s"
        " ORDER BY m.kickoff_utc, m.id",
        {"tid": team_id, "now": utcnow()}, TEAM_UPCOMING_LIMIT,
    )

    # squad: players stored with this club as their current club. Only
    # players whose profile was fetched carry position/shirt metadata - a
    # sparse-but-correct list beats a wrong one, so show what we have.
    squad: List[Dict[str, Any]] = []
    for p in conn.execute(
        """SELECT id, name_en, name_ar, image_url, position, shirt_number
           FROM players WHERE current_club_id = %s AND name_en IS NOT NULL
           ORDER BY CASE position
                      WHEN 'GOALKEEPER' THEN 1 WHEN 'DEFENDER' THEN 2
                      WHEN 'MIDFIELDER' THEN 3 WHEN 'FORWARD' THEN 4 ELSE 5 END,
                    shirt_number IS NULL, shirt_number, name_en""",
        (team_id,),
    ).fetchall():
        squad.append({
            "id": p["id"],
            "nameEn": p["name_en"],
            "nameAr": p["name_ar"],
            "position": p["position"],
            "shirtNumber": p["shirt_number"],
            "imageUrl": img_path(conn, p["image_url"]),
        })

    return {
        "team": team,
        "recentMatches": recent,
        "upcomingMatches": upcoming,
        "squad": squad,
        "generatedAt": utcnow(),
    }


# ---------------------------------------------------------------------------
# player payload (bio + career history) for the player dialog / player page
# ---------------------------------------------------------------------------

def build_player(conn: psycopg.Connection, player_id: str) -> Optional[Dict[str, Any]]:
    """Player profile: bilingual bio + full career timeline."""
    p = conn.execute("SELECT * FROM players WHERE id = %s", (player_id,)).fetchone()
    if p is None:
        return None

    # current club as a TeamRef when the club row exists (crest + both names),
    # else synthesized from the stored club-name strings
    club_json: Optional[Dict[str, Any]] = None
    if p["current_club_id"]:
        club_row = conn.execute(
            "SELECT * FROM teams WHERE id = %s", (p["current_club_id"],)
        ).fetchone()
        club_json = _team_ref(conn, club_row) if club_row is not None else None
    if club_json is None and (p["current_club_name_en"] or p["current_club_name_ar"]):
        club_json = {
            "id": p["current_club_id"],
            "nameEn": p["current_club_name_en"],
            "nameAr": p["current_club_name_ar"],
            "shortNameEn": None,
            "code": None,
            "crestUrl": None,
        }

    # career timeline (provider order = most recent season first). Crests come
    # from the teams table when the club is known to us.
    career: List[Dict[str, Any]] = []
    for e in conn.execute(
        """SELECT c.*, t.crest_url AS t_crest
           FROM player_career_entries c
           LEFT JOIN teams t ON t.id = c.team_id
           WHERE c.player_id = %s
           ORDER BY c.sort_order, c.id""",
        (player_id,),
    ).fetchall():
        career.append({
            "team": {
                "id": e["team_id"],
                "nameEn": e["team_name_en"],
                "nameAr": e["team_name_ar"],
                "crestUrl": img_path(conn, e["t_crest"]),
            },
            "seasonName": e["season_name"],
            "competition": {
                "id": e["competition_id"],
                "nameEn": e["competition_name_en"],
                "nameAr": e["competition_name_ar"],
            },
            "appearances": e["appearances"],
            "goals": e["goals"],
            "assists": e["assists"],
            "yellowCards": e["yellow_cards"],
            "redCards": e["red_cards"],
            "minutesPlayed": e["minutes_played"],
            "isLoan": bool(e["is_loan"]),
        })

    return {
        "player": {
            "id": p["id"],
            "nameEn": p["name_en"],
            "nameAr": p["name_ar"],
            "fullNameEn": p["full_name_en"],
            "fullNameAr": p["full_name_ar"],
            "imageUrl": img_path(conn, p["image_url"]),
            "position": p["position"],
            "shirtNumber": p["shirt_number"],
            "heightCm": p["height_cm"],
            "weightKg": p["weight_kg"],
            "birthDate": p["birth_date"],
            "age": p["age"],
            "nationalityEn": p["nationality_en"],
            "nationalityAr": p["nationality_ar"],
            "placeOfBirthEn": p["place_of_birth_en"],
            "placeOfBirthAr": p["place_of_birth_ar"],
        },
        "currentClub": club_json,
        "career": career,
        "profileFetched": bool(p["profile_fetched_at"]),
        "generatedAt": utcnow(),
    }


# ---------------------------------------------------------------------------
# scraping glue (on-demand + scheduled) - the ONLY place the app hits goal.com
# ---------------------------------------------------------------------------
_scrape_lock = threading.Lock()
# page dates / match ids we already tried on demand, with the attempt time:
# entries expire after ON_DEMAND_RETRY_SEC so a FAILED one-shot fetch (goal.com
# hiccup, timeout) is retried later instead of being blocked until restart.
_attempted_dates: Dict[str, float] = {}
_enrich_attempted: Dict[str, float] = {}


def _prune_attempts(book: Dict[str, float], now: float) -> None:
    for k in [k for k, ts in book.items() if now - ts > ON_DEMAND_RETRY_SEC * 2]:
        book.pop(k, None)


def _scrape_listing_dates(dates: List[str], arabic: bool = True) -> None:
    """Refresh the goal.com EN (+AR unless told otherwise) listing pages.

    `arabic=False` skips the Arabic listing pages: scores/status are
    language-independent, AR names change a few times per season, so the
    minute-by-minute cycles only need the EN page (see AR_LISTING_SEC).
    """
    from .pipeline import scrape_date_listings

    db = Database(API_DB_URL)
    try:
        for d in dates:
            try:
                scrape_date_listings(db, d, arabic=arabic, kooora=False)
            except Exception as exc:  # noqa: BLE001
                log.warning("listing scrape failed for %s: %s", d, exc)
        # matches observed finishing during this scrape -> standings refresh
        _note_finished_competitions(db)
    finally:
        db.close()


def _ensure_date_scraped(date: str, tz_min: int) -> None:
    """On-demand: scrape the listing pages covering a date nobody asked for yet."""
    pages = page_dates_for_local_day(date, tz_min)
    now = time.time()
    with _scrape_lock:
        _prune_attempts(_attempted_dates, now)
        if all(now - _attempted_dates.get(p, 0.0) < ON_DEMAND_RETRY_SEC
               for p in pages):
            return
        for p in pages:
            _attempted_dates[p] = now
    log.info("on-demand listing scrape for %s (pages %s)", date, pages)
    _scrape_listing_dates(pages)
    _invalidate_listing_cache()


def _ensure_match_detail(match_id: str) -> bool:
    """On-demand: fetch a match's detail pages when the DB has none yet."""
    from .pipeline import enrich_match

    now = time.time()
    with _scrape_lock:
        _prune_attempts(_enrich_attempted, now)
        if now - _enrich_attempted.get(match_id, 0.0) < ON_DEMAND_RETRY_SEC:
            return False
        _enrich_attempted[match_id] = now
    db = Database(API_DB_URL)
    try:
        row = db.conn.execute(
            "SELECT slug_en FROM matches WHERE id = %s", (match_id,)
        ).fetchone()
        if row is None:
            return False
        log.info("on-demand detail fetch for %s", match_id)
        result = enrich_match(db, match_id, row["slug_en"], arabic=True)
        # detail pages are often what first observes a live match ending
        _note_finished_competitions(db)
        return result
    finally:
        db.close()


# player profile on-demand attempts (same expiry discipline as matches)
_player_attempted: Dict[str, float] = {}


def _ensure_player_profile(player_id: str) -> bool:
    """On-demand: fetch a player's profile pages when the DB has none yet.

    Only fires for players WITHOUT profile_fetched_at (lineups/events store
    bare name rows); a failed fetch is retried after ON_DEMAND_RETRY_SEC.
    """
    from .pipeline import enrich_player

    now = time.time()
    with _scrape_lock:
        _prune_attempts(_player_attempted, now)
        if now - _player_attempted.get(player_id, 0.0) < ON_DEMAND_RETRY_SEC:
            return False
        _player_attempted[player_id] = now
    db = Database(API_DB_URL)
    try:
        row = db.conn.execute(
            "SELECT profile_fetched_at FROM players WHERE id = %s", (player_id,)
        ).fetchone()
        if row is None or row["profile_fetched_at"]:
            return False
        log.info("on-demand player profile fetch for %s", player_id)
        return enrich_player(db, player_id, slug_en=None, arabic=True)
    finally:
        db.close()


# competition data (standings + rounds) TTL + per-competition locks
COMPETITION_TTL_SEC = int(os.environ.get("COMPETITION_TTL_SEC", "1800"))
# FALLBACK warm cycle for recently-viewed competitions (see run_scheduler).
# The primary freshness driver is event-driven: the moment a scrape observes
# a match finishing, the leagues users actually open are re-scraped in the
# background (~1 min after the final whistle). This slower cycle only catches
# what events cannot: postponed/rescheduled matches, provider corrections,
# results missed while the process was down, season roll-over.
COMP_REFRESH_SEC = int(os.environ.get("COMP_REFRESH_SEC", "1800"))
COMP_REFRESH_MAX = int(os.environ.get("COMP_REFRESH_MAX", "8"))
COMP_VIEW_TRACK_SEC = int(os.environ.get("COMP_VIEW_TRACK_SEC", "21600"))  # 6h
# match-end events: standings only change when a match ends, so refresh the
# affected league right away instead of waiting for the warm cycle
COMP_EVENT_REFRESH = os.environ.get("COMP_EVENT_REFRESH", "1") not in ("0", "false", "no")
COMP_EVENT_DEBOUNCE_SEC = int(os.environ.get("COMP_EVENT_DEBOUNCE_SEC", "300"))
_comp_scrape_locks: Dict[str, threading.Lock] = {}
_comp_scrape_locks_guard = threading.Lock()


def _comp_lock(comp_id: str) -> threading.Lock:
    with _comp_scrape_locks_guard:
        if comp_id not in _comp_scrape_locks:
            _comp_scrape_locks[comp_id] = threading.Lock()
        return _comp_scrape_locks[comp_id]


# recently-viewed competitions (kept warm by the scheduler) + in-flight refreshes
_comp_view_lock = threading.Lock()
_comp_last_viewed: Dict[str, float] = {}
_comp_refreshing: set = set()
# match-end bookkeeping: last event-driven refresh per competition (debounce)
# and competitions whose result arrived while nobody had them open
_comp_event_refreshed_at: Dict[str, float] = {}
_comp_pending_result: Dict[str, float] = {}


def _track_competition_view(comp_id: str) -> None:
    """Remember which competitions users actually open (scheduler warms these)."""
    with _comp_view_lock:
        _comp_last_viewed[comp_id] = time.time()


def _comp_scrape_row(conn, comp_id):
    """The competition_scrapes bookkeeping row (TTL source) or None."""
    return conn.execute(
        "SELECT * FROM competition_scrapes WHERE competition_id = %s", (comp_id,)
    ).fetchone()


def _comp_data_stale(row) -> bool:
    """True when standings/rounds data is missing or older than the TTL."""
    if row is None:
        return True
    now = datetime.now(timezone.utc)

    def _fresh(ts) -> bool:
        if not ts:
            return False
        try:
            parsed = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return False
        return (now - parsed).total_seconds() < COMPETITION_TTL_SEC

    if row["has_standings"] and not _fresh(row["standings_at"]):
        return True
    return not _fresh(row["matches_at"])


def _kick_competition_refresh(comp_id: str, force: bool = False) -> bool:
    """Serve-now / refresh-later: re-scrape a competition in the background.

    This NEVER blocks the HTTP request - the slow goal.com round trip
    (3 paced requests + a full season of upserts) happens on a daemon
    thread. The per-competition lock inside _ensure_competition_scraped
    serializes concurrent refreshes and scrape_competition_if_stale()
    skips the work entirely when the data is still fresh.
    Returns True when a refresh was started, False if one is in flight.
    """
    with _comp_view_lock:
        if comp_id in _comp_refreshing:
            return False
        _comp_refreshing.add(comp_id)

    def _worker():
        try:
            _ensure_competition_scraped(comp_id, force=force)
        finally:
            with _comp_view_lock:
                _comp_refreshing.discard(comp_id)

    threading.Thread(target=_worker, name="comp-refresh", daemon=True).start()
    return True


def _note_finished_competitions(db) -> int:
    """Match-end hook: refresh standings the moment a match is observed ended.

    Standings only ever change when a match in that competition finishes, so
    instead of waiting for the next warm tick (COMP_REFRESH_SEC) we re-scrape
    the affected league right away. Every listing scrape / detail enrichment
    records transitions into an ended status in Database.newly_finished_comps;
    this drains that set and:

      * leagues users actually opened (recently viewed)  -> immediate
        background re-scrape, force=True (the table genuinely changed, so
        the 30-min TTL does not apply). A per-competition debounce collapses
        end-of-round bursts - ten matches finishing within minutes of each
        other cost one or two scrapes, not ten.
      * leagues nobody viewed / debounce-blocked / refresh already in flight
        -> remembered as a 'pending result', so the NEXT open of that league
        serves what we have, flags refreshing=true and re-scrapes. No wasted
        goal.com round trips for leagues nobody is looking at.

    Returns the number of refreshes kicked.
    """
    comps = getattr(db, "newly_finished_comps", None)
    if not comps:
        return 0
    ended = set(comps)
    comps.clear()
    if not COMP_EVENT_REFRESH:
        return 0

    now = time.time()
    with _comp_view_lock:
        viewed = {cid for cid, ts in _comp_last_viewed.items()
                  if now - ts < COMP_VIEW_TRACK_SEC}
        todo = [cid for cid in ended
                if cid in viewed
                and now - _comp_event_refreshed_at.get(cid, 0.0) >= COMP_EVENT_DEBOUNCE_SEC]
        for cid in todo:
            _comp_event_refreshed_at[cid] = now
        # keep the debounce map bounded
        for cid in [c for c, ts in _comp_event_refreshed_at.items()
                    if now - ts > 3600]:
            _comp_event_refreshed_at.pop(cid, None)

    kicked = []
    for cid in todo:
        if _kick_competition_refresh(cid, force=True):
            kicked.append(cid)
    if kicked:
        log.info("match ended -> refreshing standings now: %s",
                 ", ".join(sorted(kicked)[:8]))

    # everything NOT refreshed right now -> pending-result marker, consumed
    # by the next open of that league (see _consume_pending_result)
    with _comp_view_lock:
        for cid in ended:
            if cid not in kicked:
                _comp_pending_result[cid] = now
        for cid in [c for c, ts in _comp_pending_result.items()
                    if now - ts > 7200]:
            _comp_pending_result.pop(cid, None)
    return len(kicked)


def _consume_pending_result(comp_id: str) -> bool:
    """True when a match ended in this competition after its last standings
    scrape while nobody had it open - the data we are about to serve is
    missing that result even though it is still TTL-fresh."""
    now = time.time()
    with _comp_view_lock:
        hit = _comp_pending_result.pop(comp_id, None) is not None
        for cid in [c for c, ts in _comp_pending_result.items()
                    if now - ts > 7200]:
            _comp_pending_result.pop(cid, None)
    return hit


def _ensure_competition_scraped(comp_id: str, force: bool = False) -> None:
    """On-demand: scrape standings + all rounds when missing or stale (TTL)."""
    from .pipeline import scrape_competition_if_stale

    with _comp_lock(comp_id):
        try:
            db = Database(API_DB_URL)
            try:
                if force:
                    from .pipeline import scrape_competition
                    scrape_competition(db, comp_id)
                else:
                    scrape_competition_if_stale(db, comp_id, ttl_sec=COMPETITION_TTL_SEC)
            finally:
                db.close()
        except Exception as exc:  # noqa: BLE001
            log.warning("competition scrape failed for %s: %s", comp_id, exc)


def _kick_enrichment_thread(dates: List[str], max_details: int = 250) -> None:
    """Background detail enrichment for freshly scraped dates (fire&forget)."""
    from .pipeline import enrich_date

    def _worker():
        db = Database(API_DB_URL)
        try:
            for d in dates:
                try:
                    enrich_date(db, d, None, only_missing=True,
                                max_details=max_details, arabic=True)
                    _note_finished_competitions(db)
                except Exception as exc:  # noqa: BLE001
                    log.warning("background enrich failed for %s: %s", d, exc)
        finally:
            db.close()

    threading.Thread(target=_worker, name="enrich", daemon=True).start()


# ---------------------------------------------------------------------------
# listing response cache (tiny; the DB read is cheap, this just smooths polls)
# ---------------------------------------------------------------------------
_listing_cache: Dict[str, Tuple[float, Dict[str, Any], str]] = {}
_listing_cache_lock = threading.Lock()


def _listing_cached(conn, date, today, major_only, tz_min) -> Tuple[Dict[str, Any], str]:
    """Cached (listing, etag); the etag is computed once per rebuild so ETag
    revalidation costs nothing extra on the hot poll path."""
    key = f"{date}|{today}|{int(major_only)}|{tz_min}"
    ttl = 15.0 if date == today else 300.0
    now = time.time()
    with _listing_cache_lock:
        hit = _listing_cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1], hit[2]
    listing = build_listing(conn, date, today, major_only, tz_min)
    etag = _content_etag(listing)
    with _listing_cache_lock:
        _listing_cache[key] = (now, listing, etag)
        if len(_listing_cache) > 400:            # keep it bounded
            for k in list(_listing_cache)[:200]:
                _listing_cache.pop(k, None)
    return listing, etag


def _invalidate_listing_cache() -> None:
    with _listing_cache_lock:
        _listing_cache.clear()


# last AR detail fetch per live match (see the live-enrich scheduler job: EN
# detail pages carry the fast-changing data and are fetched every cycle; the
# AR page only adds Arabic names, so it is fetched every AR_DETAIL_SEC per
# match instead - roughly halving the busiest goal.com path on match days)
_detail_ar_ts: Dict[str, float] = {}


def _prune_detail_ar_ts(now: float) -> None:
    if len(_detail_ar_ts) > max(4 * LIVE_ENRICH_MAX, 64):
        for k in [k for k, ts in _detail_ar_ts.items() if now - ts > 43200]:
            _detail_ar_ts.pop(k, None)


# ---------------------------------------------------------------------------
# scheduler leadership (PostgreSQL advisory lock)
# ---------------------------------------------------------------------------
# Visible to /api/health so operators can see which process is the leader.
_SCHED_STATE: Dict[str, Any] = {"role": SCHEDULER_ROLE, "leader": False,
                                "standby_since": None}


class _SchedulerLeader:
    """Exactly-one-leader election for the scrape scheduler.

    Run several API processes against one database (gunicorn workers,
    replicas) and each would start its own scheduler - multiplying every
    goal.com request by the process count. A session-level advisory lock
    on a DEDICATED connection solves it: the first process that grabs it
    becomes the leader and scrapes; the others idle and re-check, taking
    over automatically when the leader's connection (process) dies.
    """

    def __init__(self) -> None:
        self._conn = None
        self._leader = False

    def is_leader(self) -> bool:
        """True when THIS process holds (or just acquired) leadership."""
        if SCHEDULER_ROLE == "force":
            _SCHED_STATE["leader"] = True
            return True
        if SCHEDULER_ROLE == "off" or self._leader:
            return self._leader
        try:
            if self._conn is None:
                # dedicated, NON-pooled connection: the advisory lock must
                # live exactly as long as this process
                self._conn = backend.connect(API_DB_URL)
            row = self._conn.execute(
                "SELECT pg_try_advisory_lock(%s) AS got", (SCHED_LOCK_KEY,)
            ).fetchone()
            if row and row["got"]:
                self._leader = True
                _SCHED_STATE["leader"] = True
                log.info("scheduler leadership acquired - this process scrapes")
        except Exception as exc:  # noqa: BLE001
            log.warning("scheduler leadership check failed: %s", exc)
            self._reset()
        return self._leader

    def _reset(self) -> None:
        self._leader = False
        _SCHED_STATE["leader"] = False
        try:
            if self._conn is not None:
                self._conn.close()
        except Exception:  # noqa: BLE001
            pass
        self._conn = None

    def release(self) -> None:
        """Explicitly step down (best effort - process exit releases anyway)."""
        if self._conn is not None:
            try:
                self._conn.execute("SELECT pg_advisory_unlock(%s)", (SCHED_LOCK_KEY,))
            except Exception:  # noqa: BLE001
                pass
        self._reset()


# ---------------------------------------------------------------------------
# scheduler (in-process cron)
# ---------------------------------------------------------------------------
def _around_active(conn) -> bool:
    """True while anything is happening: a live match or a kickoff soon.

    Decides whether the neighbouring-day listings need the fast cadence
    (REFRESH_AROUND_SEC) or can idle (REFRESH_AROUND_IDLE_SEC): tomorrow's
    fixtures page changes a few times a day, not every 5 minutes.
    """
    horizon = (datetime.now(timezone.utc)
               + timedelta(seconds=AROUND_ACTIVE_LOOKAHEAD_SEC)
               ).strftime("%Y-%m-%dT%H:%M:%SZ")
    row = conn.execute(
        """SELECT 1 FROM matches
           WHERE status = 'LIVE'
              OR (status = 'FIXTURE' AND kickoff_utc < %s)
           LIMIT 1""",
        (horizon,),
    ).fetchone()
    return row is not None


def run_scheduler() -> None:
    """Keep the database fresh: listings around 'now' + live match details."""
    from .pipeline import enrich_date, enrich_match, scrape_date_listings

    log.info("scheduler started (today=%ss around=%ss/%ss-idle live=%ss "
             "backfill=%ss comp-warm=%ss ar-listing=%ss ar-detail=%ss role=%s)",
             REFRESH_TODAY_SEC, REFRESH_AROUND_SEC, REFRESH_AROUND_IDLE_SEC,
             ENRICH_LIVE_SEC, ENRICH_BACKFILL_SEC, COMP_REFRESH_SEC,
             AR_LISTING_SEC, AR_DETAIL_SEC, SCHEDULER_ROLE)
    if SCHEDULER_ROLE == "off":
        return

    leader = _SchedulerLeader()
    last_listing = 0.0
    last_around = 0.0
    last_live = 0.0
    last_backfill = 0.0
    last_comp_warm = 0.0
    # slow AR cycles, one timer per job kind (names are slow-changing data)
    last_ar_today = 0.0
    last_ar_around = 0.0
    around_interval = REFRESH_AROUND_SEC
    announced_standby = False

    while True:
        now = time.time()
        today = utc_today()

        # scale-out safety: exactly ONE process runs the scrape jobs; the
        # others stand by and take over if the leader dies
        if not leader.is_leader():
            if not announced_standby:
                announced_standby = True
                _SCHED_STATE["standby_since"] = now
                log.info("scheduler standby - another process is the leader "
                         "(re-checking every 15s; takeover if it dies)")
            time.sleep(15)
            continue
        announced_standby = False

        try:
            # 1) today's listing page (live-scores) - every minute. This is
            #    also the ear that hears the final whistle: match-end events
            #    observed here refresh standings ~1 min after matches finish.
            #    The EN page carries the scores; the AR page (names only) is
            #    fetched on the slow AR cycle - halving this hottest path.
            if REFRESH_TODAY_SEC and now - last_listing >= REFRESH_TODAY_SEC:
                last_listing = now
                ar_due = now - last_ar_today >= AR_LISTING_SEC
                if ar_due:
                    last_ar_today = now
                db = Database(API_DB_URL)
                try:
                    scrape_date_listings(db, today, arabic=ar_due, kooora=False)
                    _note_finished_competitions(db)
                finally:
                    db.close()
                _invalidate_listing_cache()

            # 2) neighbouring days (yesterday results / tomorrow fixtures).
            #    Fast cadence only while something is actually happening (a
            #    live match or a kickoff within the lookahead); idle days -
            #    nights, quiet weekdays - drop to REFRESH_AROUND_IDLE_SEC.
            if REFRESH_AROUND_SEC and now - last_around >= around_interval:
                last_around = now
                try:
                    with backend.connection(API_DB_URL) as conn:
                        active = _around_active(conn)
                except Exception:  # noqa: BLE001
                    active = True            # when in doubt, stay fast
                around_interval = (REFRESH_AROUND_SEC if active
                                   else REFRESH_AROUND_IDLE_SEC)
                ar_due = now - last_ar_around >= AR_LISTING_SEC
                if ar_due:
                    last_ar_around = now
                _scrape_listing_dates([
                    (datetime.fromisoformat(today) - timedelta(days=1)).date().isoformat(),
                    (datetime.fromisoformat(today) + timedelta(days=1)).date().isoformat(),
                ], arabic=ar_due)
                _invalidate_listing_cache()

            # 3) refresh details of matches currently live. The EN detail
            #    page is fetched every cycle (scores/events/minutes); the AR
            #    detail page (names only) every AR_DETAIL_SEC per match.
            #    When an EN-only cycle observes the final whistle, one extra
            #    bilingual fetch completes the record so the closing events
            #    keep their Arabic names too.
            if ENRICH_LIVE_SEC and now - last_live >= ENRICH_LIVE_SEC:
                last_live = now
                db = Database(API_DB_URL)
                try:
                    cutoff = (datetime.now(timezone.utc)
                              - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    rows = db.conn.execute(
                        """SELECT id, slug_en FROM matches
                           WHERE status = 'LIVE'
                             AND kickoff_utc >= %s
                           ORDER BY kickoff_utc LIMIT %s""",
                        (cutoff, LIVE_ENRICH_MAX),
                    ).fetchall()
                    for r in rows:
                        mid = r["id"]
                        ar_due = now - _detail_ar_ts.get(mid, 0.0) >= AR_DETAIL_SEC
                        enrich_match(db, mid, r["slug_en"], arabic=ar_due)
                        if ar_due:
                            _detail_ar_ts[mid] = now
                            _prune_detail_ar_ts(now)
                            continue
                        st = db.conn.execute(
                            "SELECT status FROM matches WHERE id = %s", (mid,)
                        ).fetchone()
                        if st and (st["status"] or "").upper() in config.MATCH_ENDED_STATUSES:
                            log.info("final whistle seen on an EN cycle - "
                                     "bilingual completion fetch for %s", mid)
                            enrich_match(db, mid, r["slug_en"], arabic=True)
                            _detail_ar_ts[mid] = time.time()
                    # a live match whose detail page came back finished -> event
                    _note_finished_competitions(db)
                finally:
                    db.close()

            # 4) slow backfill of finished matches still missing details
            if ENRICH_BACKFILL_SEC and now - last_backfill >= ENRICH_BACKFILL_SEC:
                last_backfill = now
                db = Database(API_DB_URL)
                try:
                    yesterday = (datetime.fromisoformat(today)
                                 - timedelta(days=1)).date().isoformat()
                    enrich_date(db, yesterday, None, only_missing=True,
                                max_details=200, arabic=True)
                    enrich_date(db, today, None, only_missing=True,
                                max_details=200, arabic=True)
                    _note_finished_competitions(db)
                finally:
                    db.close()

            # 5) FALLBACK warm cycle for recently-viewed competitions
            #    (standings + rounds). Match ends are already handled
            #    event-driven by _note_finished_competitions - within about a
            #    minute of the final whistle - so this slower cycle only
            #    catches what events cannot: postponements, provider
            #    corrections, results missed while the process was down.
            if COMP_REFRESH_SEC and now - last_comp_warm >= COMP_REFRESH_SEC:
                last_comp_warm = now
                with _comp_view_lock:
                    viewed = sorted(_comp_last_viewed.items(),
                                    key=lambda kv: -kv[1])[:COMP_REFRESH_MAX]
                    for cid in [c for c, ts in _comp_last_viewed.items()
                                if now - ts > COMP_VIEW_TRACK_SEC * 4]:
                        _comp_last_viewed.pop(cid, None)
                for cid, viewed_at in viewed:
                    if now - viewed_at < COMP_VIEW_TRACK_SEC:
                        _kick_competition_refresh(cid)

        except Exception as exc:  # noqa: BLE001  - never let the scheduler die
            log.error("scheduler cycle failed: %s", exc)

        time.sleep(5)


# ---------------------------------------------------------------------------
# image cache pre-warm
# ---------------------------------------------------------------------------
def warm_image_cache(db_url: Optional[str] = None, days: int = 10,
                     workers: int = 8) -> Tuple[int, int]:
    """Pre-download every crest/logo referenced by recent matches.

    Returns (ok, failed). Warming the disk cache avoids first-load bursts
    where hundreds of browsers race for cold images.
    """
    from concurrent.futures import ThreadPoolExecutor

    global API_DB_URL
    if db_url:
        API_DB_URL = db_url

    with backend.connection(API_DB_URL, pooled=False) as conn:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        urls = [r["url"] for r in conn.execute(
            """SELECT DISTINCT url FROM (
                   SELECT th.crest_url AS url FROM matches m JOIN teams th ON th.id = m.home_team_id
                        WHERE m.kickoff_utc >= %(c)s AND th.crest_url IS NOT NULL
                   UNION
                   SELECT ta.crest_url AS url FROM matches m JOIN teams ta ON ta.id = m.away_team_id
                        WHERE m.kickoff_utc >= %(c)s AND ta.crest_url IS NOT NULL
                   UNION
                   SELECT c.image_url AS url FROM matches m JOIN competitions c ON c.id = m.competition_id
                        WHERE m.kickoff_utc >= %(c)s AND c.image_url IS NOT NULL
               ) AS recent_images""",
            {"c": cutoff},
        ).fetchall()]

    IMG_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ok = failed = 0

    def _one(url: str) -> bool:
        token = _make_token(url)
        return _fetch_image(token, url) is not None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(_one, urls):
            if result:
                ok += 1
            else:
                failed += 1
    return ok, failed


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
def create_app(db_url: Optional[str] = None) -> Flask:
    global API_DB_URL
    if db_url:
        API_DB_URL = db_url

    app = Flask(__name__)
    app.config["JSON_AS_ASCII"] = False

    # ---- per-request connection ------------------------------------------
    def get_conn() -> psycopg.Connection:
        if "db" not in g:
            # the context manager commits on success / rolls back on error
            # when it is closed in the teardown handler below
            g.db_ctx = backend.connection(API_DB_URL)
            g.db = g.db_ctx.__enter__()
        return g.db

    @app.teardown_appcontext
    def close_conn(_exc):
        ctx = g.pop("db_ctx", None)
        g.pop("db", None)
        if ctx is not None:
            ctx.__exit__(*sys.exc_info())

    # ---- GET /api/matches -------------------------------------------------
    @app.get("/api/matches")
    def api_matches():
        today = request.args.get("today") or utc_today()
        date = request.args.get("date") or today
        if not DATE_RE.match(date):
            date = today
        if not DATE_RE.match(today):
            today = utc_today()
        major_only = request.args.get("major", "1") != "0"
        try:
            tz_min = max(-840, min(840, int(request.args.get("tz", "0"))))
        except ValueError:
            tz_min = 0

        conn = get_conn()
        listing, etag = _listing_cached(conn, date, today, major_only, tz_min)

        # nothing stored for this day yet? scrape the covering pages once
        if listing["totalMatches"] == 0:
            _ensure_date_scraped(date, tz_min)
            listing, etag = _listing_cached(conn, date, today, major_only, tz_min)
            if listing["totalMatches"] > 0 and date != today:
                _kick_enrichment_thread([date])

        return _etag_response(listing, etag)

    # ---- GET /api/match/<id> ----------------------------------------------
    @app.get("/api/match/<match_id>")
    def api_match(match_id: str):
        if not re.match(r"^[A-Za-z0-9_-]{4,64}$", match_id):
            return jsonify({"error": "invalid match id"}), 400

        conn = get_conn()
        detail = build_detail(conn, match_id)

        # no detail rows yet? fetch the detail pages once, synchronously
        if detail is None or (not detail["events"]
                              and not (detail["lineups"]["home"] or detail["lineups"]["away"])
                              and detail["status"] in ("RESULT", "LIVE", "AET", "PEN")):
            _ensure_match_detail(match_id)
            detail = build_detail(conn, match_id)

        if detail is None:
            return jsonify({"error": "match not found"}), 404
        return _etag_response(detail)

    # ---- GET /api/competition/<id> -----------------------------------------
    @app.get("/api/competition/<comp_id>")
    def api_competition(comp_id: str):
        if not re.match(r"^[A-Za-z0-9_-]{4,64}$", comp_id):
            return jsonify({"error": "invalid competition id"}), 400

        conn = get_conn()
        _track_competition_view(comp_id)
        comp = build_competition(conn, comp_id)
        if comp is None:
            return jsonify({"error": "competition not found"}), 404

        # Serve straight from the database. Only the very FIRST open (no
        # scrape record yet) fills the data synchronously - after that this
        # endpoint NEVER waits on goal.com again: stale data is served
        # immediately and refreshed on a background thread, and the response
        # carries refreshing=true so the UI can quietly re-fetch the fresh
        # copy a few seconds later.
        #
        # A match that ended while nobody had this league open leaves a
        # 'pending result' marker: the data below is still TTL-fresh but is
        # missing that result, so treat it as stale (force the re-scrape).
        pending = _consume_pending_result(comp_id)
        scrape = _comp_scrape_row(conn, comp_id)
        if scrape is None:
            _ensure_competition_scraped(comp_id)
            comp = build_competition(conn, comp_id)
            if comp is None:
                return jsonify({"error": "competition not found"}), 404
        elif _comp_data_stale(scrape) or pending:
            _kick_competition_refresh(comp_id, force=pending)
            comp = {**comp, "refreshing": True}

        return _etag_response(comp)

    # ---- GET /api/competition/<id>/matches?gameset=... ----------------------
    @app.get("/api/competition/<comp_id>/matches")
    def api_competition_matches(comp_id: str):
        if not re.match(r"^[A-Za-z0-9_-]{4,64}$", comp_id):
            return jsonify({"error": "invalid competition id"}), 400
        gameset = request.args.get("gameset") or None
        if gameset and not re.match(r"^[A-Za-z0-9_-]{4,64}$", gameset):
            return jsonify({"error": "invalid gameset id"}), 400

        conn = get_conn()
        _track_competition_view(comp_id)
        payload = build_competition_matches(conn, comp_id, gameset)

        if payload is None:
            # unknown competition (or gameset): one synchronous discovery
            # attempt, then give up - same contract as before
            _ensure_competition_scraped(comp_id)
            payload = build_competition_matches(conn, comp_id, gameset)
            if payload is None:
                return jsonify({"error": "competition not found"}), 404

        # An empty round used to trigger a full SYNCHRONOUS competition
        # scrape - the main reason opening a league felt slow. Now:
        #   * empty round + fresh data  -> legitimately empty, serve it
        #   * empty round + stale data  -> serve now, refresh in the
        #     background (refreshing=true tells the UI to re-fetch)
        if payload["gameset"]["matchCount"] == 0:
            scrape = _comp_scrape_row(conn, comp_id)
            if scrape is None:
                _ensure_competition_scraped(comp_id)
                payload = build_competition_matches(conn, comp_id, gameset)
                if payload is None:
                    return jsonify({"error": "competition not found"}), 404
            elif _comp_data_stale(scrape):
                _kick_competition_refresh(comp_id)
                return _etag_response({**payload, "refreshing": True})

        return _etag_response(payload)

    # ---- GET /api/team/<id> --------------------------------------------------
    @app.get("/api/team/<team_id>")
    def api_team(team_id: str):
        if not re.match(r"^[A-Za-z0-9_-]{4,64}$", team_id):
            return jsonify({"error": "invalid team id"}), 400

        conn = get_conn()
        team = build_team(conn, team_id)
        if team is None:
            return jsonify({"error": "team not found"}), 404
        return _etag_response(team)

    # ---- GET /api/player/<id> ------------------------------------------------
    @app.get("/api/player/<player_id>")
    def api_player(player_id: str):
        if not re.match(r"^[A-Za-z0-9_-]{4,64}$", player_id):
            return jsonify({"error": "invalid player id"}), 400

        conn = get_conn()
        player = build_player(conn, player_id)

        # known player but never profile-fetched? pull the profile pages once,
        # synchronously (same contract as the match detail endpoint)
        if player is not None and not player["profileFetched"]:
            _ensure_player_profile(player_id)
            player = build_player(conn, player_id)
            if player is None:
                return jsonify({"error": "player not found"}), 404

        if player is None:
            return jsonify({"error": "player not found"}), 404
        return _etag_response(player)

    # ---- GET /api/img?t=... ------------------------------------------------
    @app.get("/api/img")
    def api_img():
        token = request.args.get("t", "")
        if not token or len(token) > 128 or not re.match(r"^[0-9a-f]+$", token):
            return Response("bad request", status=400)

        conn = get_conn()
        row = conn.execute(
            "SELECT url FROM image_tokens WHERE token = %s", (token,)
        ).fetchone()
        if row is None:
            return Response("unknown token", status=404)
        url = row["url"]

        parts = urlsplit(url)
        if parts.scheme != "https" or not _img_host_allowed(parts.hostname or ""):
            return Response("forbidden", status=403)

        path = _fetch_image(token, url)
        if path is None:
            return Response("upstream error", status=502)

        resp = _send_cached_image(path, token)
        return resp

    # in-memory LRU for hot images: under load the same crests are requested
    # constantly, and a disk read per request is pure waste. Bounded by
    # IMG_MEM_CACHE_BYTES; 0 disables the layer.
    from collections import OrderedDict

    _img_mem: "OrderedDict[str, Tuple[bytes, str]]" = OrderedDict()
    _img_mem_size = [0]                     # running byte total (mutable)
    _img_mem_lock = threading.Lock()

    def _img_cache_headers(resp: Response, etag: str) -> None:
        resp.headers["ETag"] = etag
        resp.headers["Cache-Control"] = (
            "public, max-age=86400, stale-while-revalidate=604800")
        resp.headers["X-Content-Type-Options"] = "nosniff"

    def _send_cached_image(path: Path, token: str) -> Response:
        # The token is the HMAC of the upstream URL, and the disk file only
        # appears after a successful download - so token+size are a solid
        # strong validator. Revalidations after the daily max-age expire (or
        # during stale-while-revalidate) collapse to 304s.
        etag = f'"i-{token}"'
        if etag in (request.headers.get("If-None-Match") or ""):
            resp = Response(status=304)
            _img_cache_headers(resp, etag)
            return resp

        data: Optional[bytes] = None
        ctype: Optional[str] = None
        if IMG_MEM_CACHE_BYTES:
            with _img_mem_lock:
                hit = _img_mem.get(token)
                if hit is not None:
                    _img_mem.move_to_end(token)      # keep it hot
                    data, ctype = hit
        if data is None:
            ctype = _TYPE_BY_EXT.get(path.suffix, "application/octet-stream")
            with open(path, "rb") as fh:
                data = fh.read()
            if IMG_MEM_CACHE_BYTES and len(data) <= IMG_MEM_CACHE_BYTES:
                with _img_mem_lock:
                    if token in _img_mem:
                        _img_mem_size[0] -= len(_img_mem[token][0])
                    _img_mem[token] = (data, ctype)
                    _img_mem_size[0] += len(data)
                    while _img_mem_size[0] > IMG_MEM_CACHE_BYTES and _img_mem:
                        _, (old, _c) = _img_mem.popitem(last=False)   # coldest
                        _img_mem_size[0] -= len(old)
        resp = Response(data, status=200, mimetype=ctype)
        _img_cache_headers(resp, etag)
        return resp

    # ---- GET /api/cron/refresh ---------------------------------------------
    @app.get("/api/cron/refresh")
    def api_cron_refresh():
        if CRON_SECRET:
            supplied = request.args.get("secret", "") or \
                (request.headers.get("Authorization", "").removeprefix("Bearer ").strip())
            if supplied != CRON_SECRET:
                return jsonify({"error": "unauthorized"}), 401

        def _job():
            today = utc_today()
            _scrape_listing_dates([
                (datetime.fromisoformat(today) - timedelta(days=1)).date().isoformat(),
                today,
                (datetime.fromisoformat(today) + timedelta(days=1)).date().isoformat(),
            ])
            _invalidate_listing_cache()
            _kick_enrichment_thread([today])

        threading.Thread(target=_job, name="cron-refresh", daemon=True).start()
        return jsonify({"ok": True, "triggered": True})

    # ---- GET /api/health ----------------------------------------------------
    @app.get("/api/health")
    def api_health():
        conn = get_conn()
        try:
            counts = {t: conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                      for t in ("competitions", "teams", "players", "matches",
                                "match_events", "lineups")}
            last_runs = [dict(r) for r in conn.execute(
                """SELECT run_mode, target, status, matches_stored, details_fetched,
                          finished_at FROM scrape_runs
                   ORDER BY id DESC LIMIT 10""").fetchall()]
            latest_match = conn.execute(
                "SELECT MAX(last_seen_at2) AS v FROM matches").fetchone()["v"]
        except psycopg.Error as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({
            "ok": True,
            "db": backend.display_dsn(API_DB_URL),
            "counts": counts,
            "latestMatchSeen": latest_match,
            "lastRuns": last_runs,
            "scheduler": {"todaySec": REFRESH_TODAY_SEC,
                          "aroundSec": REFRESH_AROUND_SEC,
                          "aroundIdleSec": REFRESH_AROUND_IDLE_SEC,
                          "liveSec": ENRICH_LIVE_SEC,
                          "backfillSec": ENRICH_BACKFILL_SEC,
                          "compWarmSec": COMP_REFRESH_SEC,
                          "compEventRefresh": COMP_EVENT_REFRESH,
                          "compEventDebounceSec": COMP_EVENT_DEBOUNCE_SEC,
                          "arListingSec": AR_LISTING_SEC,
                          "arDetailSec": AR_DETAIL_SEC,
                          "role": SCHEDULER_ROLE,
                          "leader": _SCHED_STATE.get("leader", False)},
            "goalLoad": {"onDemandRetrySec": ON_DEMAND_RETRY_SEC,
                        "liveEnrichMax": LIVE_ENRICH_MAX},
            "competitionEvents": {
                "pendingResults": len(_comp_pending_result),
            },
        })

    return app


def run(host: str = "127.0.0.1", port: int = 9000, db_url: Optional[str] = None,
        schedule: bool = True, debug: bool = False) -> None:
    """Entry point used by `python -m scraper.cli api`."""
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    app = create_app(db_url)

    if schedule and SCHEDULER_ROLE != "off":
        threading.Thread(target=run_scheduler, name="scheduler", daemon=True).start()
    else:
        log.info("scheduler disabled (%s - external cron mode; call /api/cron/refresh)",
                 "SCHEDULER_ROLE=off" if schedule else "--no-schedule")

    log.info("API serving on http://%s:%d (db: %s)", host, port,
             backend.display_dsn(API_DB_URL))
    app.run(host=host, port=port, debug=debug, threaded=True, use_reloader=False)
