"""JSON API server over the football PostgreSQL database - the backend of
the web frontend. PURE READ-ONLY: this process never scrapes goal.com; it
serves what the database holds and records data gaps for the worker.

Architecture
------------
    goal.com EN+AR  --worker-->  PostgreSQL (FOOTBALL_DB_URL)
                                     |                      ^
                                     |  SQL reads           | refresh_jobs /
                                     v                      | competition_views
                       this API  (Flask, default :8000) -----+
                         /api/matches     day listing   (bilingual, grouped)
                         /api/match/<id>  full detail   (events/lineups/stats)
                         /api/competition/<id> standings + rounds
                         /api/team/<id>   team profile  (results/fixtures/squad)
                         /api/player/<id> player profile (bio + career history)
                         /api/img?t=...   image proxy   (URLs stay hidden)
                         /api/cron/refresh  ask the worker for a refresh run
                         /api/health      db stats + last runs
                                     |
                                     v
                       Next.js frontend (:3000) - pure consumer, never scrapes

    (python -m scraper.cli worker - the scraper process: freshness scheduler
     + refresh_jobs consumer. See scraper/worker.py.)

Day listings are LOCAL-calendar correct for every user timezone: matches are
selected by kickoff falling inside the requester's local-day UTC window
(the same fix the frontend had, now done once, in SQL, over the database).

Every provider image URL is replaced by an opaque local path /api/img?t=...
before any JSON leaves the server. The token is a deterministic HMAC prefix;
the URL mapping lives in the image_tokens table and never reaches the client.

Stale-while-revalidate, process-split edition: when this API serves data
that is missing, empty or past its TTL it NEVER blocks on goal.com. It
serves what it has, flags `refreshing: true` where the contract has one,
and upserts a refresh_jobs row (plus a competition_views row for leagues
users open). The worker picks the request up within a few seconds, scrapes
the data and the frontend's existing re-fetch chain lands the fresh copy.

Conditional responses: the JSON endpoints emit strong ETags and answer
If-None-Match with 304, so browser polls collapse to a few hundred bytes
while the data is unchanged - and the Next.js proxies forward the header,
making the whole browser -> Next -> API chain revalidation-only.

Shared response cache (Redis, optional): with REDIS_URL set, every JSON
endpoint serves from a cache-aside layer in front of PostgreSQL - a hit
costs ONE Redis GET (and a matching If-None-Match answers 304 without
even parsing the payload) instead of the full SQL query chain. The
worker DELs the affected keys the moment fresh data lands, so TTLs are
only the safety net; without REDIS_URL the API behaves exactly as
before (in-process listing cache only). See scraper/apicache.py.

Run:
    python -m scraper.cli api --port 8000        (plus, somewhere: worker)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg
from flask import Flask, Response, g, jsonify, request
from urllib.parse import urlsplit

from . import config
from .db import backend
from .db.database import utcnow
from . import goal_order
from . import jobs
from . import apicache
from .imgcache import (TYPE_BY_EXT as _TYPE_BY_EXT,
                       fetch_image as _fetch_image, img_host_allowed, img_path)
from .major import is_major_competition

log = logging.getLogger("scraper.api")

# ---------------------------------------------------------------------------
# environment / configuration
# ---------------------------------------------------------------------------
API_DB_URL = os.environ.get("FOOTBALL_DB_URL", config.DEFAULT_DB_URL)

# shared secret for the cron endpoint (empty = open)
CRON_SECRET = os.environ.get("API_CRON_SECRET", "")

# how long a finished on-demand job is remembered before the API may
# re-request it (same ON_DEMAND_RETRY_SEC semantics as before the split -
# a FAILED fetch no longer blocks that data, it just rate-limits retries)
ON_DEMAND_RETRY_SEC = int(os.environ.get("ON_DEMAND_RETRY_SEC", "600"))

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
# shared response cache (Redis, optional - see scraper/apicache.py)
# ---------------------------------------------------------------------------
def _response_from_cache(hit: Tuple[str, str]) -> Response:
    """304/200 served straight from the cache: no SQL, no serialization.

    The stored body is byte-identical to what jsonify produced when the
    entry was written, so cache hits are indistinguishable from fresh
    builds (same headers, same ETag, same bytes). A poll whose
    If-None-Match equals the stored ETag collapses to a 304 without
    even parsing the payload - that is the win over the ETag-only flow,
    which still ran the full SQL chain before comparing tags.
    """
    etag, body = hit
    if etag in (request.headers.get("If-None-Match") or ""):
        resp = Response(status=304)
    else:
        resp = Response(body, status=200, mimetype="application/json")
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "no-cache"
    return resp


def _cache_and_respond(key: str, payload: Dict[str, Any], ttl: int,
                       etag: Optional[str] = None) -> Response:
    """Serve `payload` (ETag + no-cache, like _etag_response) and store the
    exact response body in the shared cache for `ttl` seconds.

    With the cache disabled (no REDIS_URL) this is identical to
    _etag_response - the store is simply skipped.
    """
    if etag is None:
        etag = _content_etag(payload)
    resp = jsonify(payload)
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "no-cache"
    if ttl > 0:
        apicache.put(key, etag, resp.get_data(as_text=True), ttl)
    return resp


# per-endpoint TTL policy (seconds; see scraper/apicache.py for the knobs)
def _listing_ttl(listing: Dict[str, Any], date: str, today: str) -> int:
    if listing.get("totalMatches") == 0:
        return apicache.TTL_LISTING_EMPTY    # a gap the worker may be filling
    if date == today:
        return apicache.TTL_LISTING_TODAY
    if date < today:
        return apicache.TTL_LISTING_PAST     # finished scores are immutable
    return apicache.TTL_LISTING_FUTURE


def _match_ttl(detail: Dict[str, Any]) -> int:
    if detail.get("refreshing"):
        return apicache.TTL_REFRESHING       # thin detail, fill in flight
    status = (detail.get("status") or "").upper()
    if status == "LIVE":
        return apicache.TTL_MATCH_LIVE
    if status in ("RESULT", "AET", "PEN"):
        return apicache.TTL_MATCH_DONE       # full detail + final score
    return apicache.TTL_MATCH_UPCOMING


def _player_ttl(player: Dict[str, Any]) -> int:
    if not player.get("profileFetched"):
        return apicache.TTL_PLAYER_STUB      # worker fetches it right now
    return apicache.TTL_PLAYER


def _payload_ttl(payload: Dict[str, Any], base: int) -> int:
    """A refreshing=true payload is always served with the short net TTL."""
    return apicache.TTL_REFRESHING if payload.get("refreshing") else base


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
# data-gap handoff: the API never scrapes. When a request notices missing /
# empty / stale data it upserts a refresh_jobs row on the request's own
# connection; the worker (scraper/worker.py) picks it up within a few
# seconds, scrapes the data and the client's next poll / re-fetch lands it.
# ---------------------------------------------------------------------------
def _enqueue(conn, kind: str, ref: str,
             payload: Optional[Dict[str, Any]] = None,
             force: bool = False) -> None:
    jobs.enqueue(conn, kind, ref, payload=payload, force=force,
                 retry_sec=ON_DEMAND_RETRY_SEC)


def _track_competition_view(conn, comp_id: str) -> None:
    """Which leagues users open - the worker keeps these warm and refreshes
    their standings the moment one of their matches ends."""
    jobs.track_view(conn, comp_id)


def _day_needs_scrape(conn, date: str, tz_min: int) -> bool:
    """True when the listing pages covering this local day have no recent
    SUCCESSFUL scrape - i.e. the day might be missing rather than empty.

    Without this guard every live-score poll of an empty (summer-break) day
    would re-enqueue a day_listing job and the worker would re-scrape it
    forever. With it, a day that goal.com was asked about within the retry
    window and still has no matches is treated as genuinely empty.
    """
    cutoff = (datetime.now(timezone.utc)
              - timedelta(seconds=ON_DEMAND_RETRY_SEC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        rows = conn.execute(
            """SELECT target FROM scrape_runs
                WHERE run_mode IN ('date', 'backfill', 'upcoming')
                  AND status = 'ok'
                  AND target = ANY(%s)
                  AND finished_at > %s""",
            (page_dates_for_local_day(date, tz_min), cutoff),
        ).fetchall()
    except psycopg.Error:
        return True                     # when in doubt, request the scrape
    covered = {r["target"] for r in rows}
    return len(covered) < len(page_dates_for_local_day(date, tz_min))


# competition data (standings + rounds) TTL - the freshness window the
# API uses to decide whether served data is stale (refreshing=true) while
# the worker re-scrapes it
COMPETITION_TTL_SEC = int(os.environ.get("COMPETITION_TTL_SEC", "1800"))


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


# ---------------------------------------------------------------------------
# listing response cache (tiny; the DB read is cheap, this just smooths polls)
# ---------------------------------------------------------------------------
_listing_cache: Dict[str, Tuple[float, Dict[str, Any], str]] = {}
_listing_cache_lock = threading.Lock()


def _listing_cached(conn, date, today, major_only, tz_min) -> Tuple[Dict[str, Any], str]:
    """Cached (listing, etag); the etag is computed once per rebuild so ETag
    revalidation costs nothing extra on the hot poll path.

    The in-process dict is the FALLBACK for cache-less deployments (no
    REDIS_URL). When the shared Redis cache is enabled it is bypassed:
    per-process entries would keep serving their own copy for up to
    ttl seconds after the worker dropped the shared key, undoing the
    worker-driven invalidation.
    """
    key = f"{date}|{today}|{int(major_only)}|{tz_min}"
    ttl = 15.0 if date == today else 300.0
    now = time.time()
    if not apicache.enabled():
        with _listing_cache_lock:
            hit = _listing_cache.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1], hit[2]
    listing = build_listing(conn, date, today, major_only, tz_min)
    etag = _content_etag(listing)
    # an EMPTY listing caches with the short TTL even for past/future days:
    # the worker may be filling the day right now (the old in-process
    # scheduler invalidated this cache itself - the split took that away)
    if listing["totalMatches"] == 0 and ttl > 15.0:
        ttl = 15.0
    if not apicache.enabled():
        with _listing_cache_lock:
            _listing_cache[key] = (now, listing, etag)
            if len(_listing_cache) > 400:            # keep it bounded
                for k in list(_listing_cache)[:200]:
                    _listing_cache.pop(k, None)
    return listing, etag


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
def create_app(db_url: Optional[str] = None) -> Flask:
    global API_DB_URL
    if db_url:
        API_DB_URL = db_url

    # ---- schema ensure -----------------------------------------------------
    # The API no longer runs the scraper (and therefore never constructs a
    # Database, which is what applied the schema). It still needs the two
    # handoff tables on day one - apply the idempotent schema script once
    # per process, best effort: a database that is briefly unreachable at
    # startup must not stop the server from booting (per-request errors
    # will surface the real problem anyway).
    def _ensure_schema() -> None:
        from .db.database import SCHEMA_PATH
        try:
            with backend.connection(API_DB_URL, pooled=False) as conn:
                with open(SCHEMA_PATH, encoding="utf-8") as fh:
                    backend.run_script(conn, fh.read())
            log.info("schema ensured (refresh_jobs + competition_views ready)")
        except Exception as exc:  # noqa: BLE001
            log.error("schema ensure failed (is the database up?): %s", exc)

    _ensure_schema()

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

        # shared-cache fast path: one Redis GET replaces the SQL chain (the
        # empty-day enqueue below already ran on the miss that filled this
        # entry, and its retry-window guard would swallow a repeat anyway)
        key = apicache.k_listing(date, today, major_only, tz_min)
        hit = apicache.get(key)
        if hit is not None:
            return _response_from_cache(hit)

        conn = get_conn()
        listing, etag = _listing_cached(conn, date, today, major_only, tz_min)

        # Nothing stored for this day yet? Ask the worker to scrape the
        # covering pages (refresh_jobs row) and serve what we have - the
        # frontend's live-score poll re-fetches and lands the data a few
        # seconds later. The enqueue is guarded: a day whose covering pages
        # were scraped successfully within the retry window is genuinely
        # empty (mid-summer break), not missing.
        if listing["totalMatches"] == 0 and _day_needs_scrape(conn, date, tz_min):
            _enqueue(conn, jobs.KIND_DAY_LISTING, date, payload={"tz": tz_min})

        return _cache_and_respond(key, listing, _listing_ttl(listing, date, today),
                                  etag=etag)

    # ---- GET /api/match/<id> ----------------------------------------------
    @app.get("/api/match/<match_id>")
    def api_match(match_id: str):
        if not re.match(r"^[A-Za-z0-9_-]{4,64}$", match_id):
            return jsonify({"error": "invalid match id"}), 400

        # shared-cache fast path (the detail-gap enqueue below already ran
        # on the miss that filled this entry; the worker drops the key the
        # moment the fresh detail lands, so a hit is never a stale fill)
        key = apicache.k_match(match_id)
        hit = apicache.get(key)
        if hit is not None:
            return _response_from_cache(hit)

        conn = get_conn()
        detail = build_detail(conn, match_id)

        # No detail rows yet? Record the gap for the worker (it fetches the
        # detail pages within a few seconds) and answer with what we have -
        # the frontend retries a 404 with a short backoff and lands the data.
        # A THIN detail (row exists, no events/lineups yet for a started or
        # finished match) carries refreshing=true so the dialog re-fetches
        # a few seconds later instead of showing empty tabs.
        thin = (detail is not None and not detail["events"]
                and not (detail["lineups"]["home"] or detail["lineups"]["away"])
                and detail["status"] in ("RESULT", "LIVE", "AET", "PEN"))
        if detail is None or thin:
            _enqueue(conn, jobs.KIND_MATCH_DETAIL, match_id)
            if thin:
                detail = {**detail, "refreshing": True}

        if detail is None:
            return jsonify({"error": "match not found"}), 404
        return _cache_and_respond(key, detail, _match_ttl(detail))

    # ---- GET /api/competition/<id> -----------------------------------------
    @app.get("/api/competition/<comp_id>")
    def api_competition(comp_id: str):
        if not re.match(r"^[A-Za-z0-9_-]{4,64}$", comp_id):
            return jsonify({"error": "invalid competition id"}), 400

        # shared-cache fast path (view tracking + staleness enqueue below
        # already ran on the miss that filled this entry; the worker drops
        # the key on every standings refresh)
        key = apicache.k_competition(comp_id)
        hit = apicache.get(key)
        if hit is not None:
            return _response_from_cache(hit)

        conn = get_conn()
        _track_competition_view(conn, comp_id)
        comp = build_competition(conn, comp_id)

        # Serve straight from the database - this endpoint NEVER waits on
        # goal.com. Missing / stale data becomes a refresh_jobs row for the
        # worker; the response carries refreshing=true so the UI quietly
        # re-fetches a few seconds later and lands the fresh copy.
        #
        # A match that ended while nobody had this league open leaves a
        # comp_pending marker: the data below is still TTL-fresh but is
        # missing that result, so treat it as stale (force the re-scrape).
        pending = jobs.has_pending_marker(conn, comp_id)
        scrape = _comp_scrape_row(conn, comp_id)
        if scrape is None and comp is not None:
            _enqueue(conn, jobs.KIND_COMP_REFRESH, comp_id)
            comp = {**comp, "refreshing": True}
        elif scrape is None:
            # competition row entirely unknown: ask the worker for a forced
            # discovery scrape; until it lands this is a genuine 404
            _enqueue(conn, jobs.KIND_COMP_DISCOVERY, comp_id)
            return jsonify({"error": "competition not found"}), 404
        elif _comp_data_stale(scrape) or pending:
            _enqueue(conn, jobs.KIND_COMP_REFRESH, comp_id, force=pending)
            comp = {**comp, "refreshing": True}

        if comp is None:
            return jsonify({"error": "competition not found"}), 404
        return _cache_and_respond(key, comp,
                                  _payload_ttl(comp, apicache.TTL_COMPETITION))

    # ---- GET /api/competition/<id>/matches?gameset=... ----------------------
    @app.get("/api/competition/<comp_id>/matches")
    def api_competition_matches(comp_id: str):
        if not re.match(r"^[A-Za-z0-9_-]{4,64}$", comp_id):
            return jsonify({"error": "invalid competition id"}), 400
        gameset = request.args.get("gameset") or None
        if gameset and not re.match(r"^[A-Za-z0-9_-]{4,64}$", gameset):
            return jsonify({"error": "invalid gameset id"}), 400

        # shared-cache fast path (same reasoning as the endpoints above)
        key = apicache.k_comp_matches(comp_id, gameset)
        hit = apicache.get(key)
        if hit is not None:
            return _response_from_cache(hit)

        conn = get_conn()
        _track_competition_view(conn, comp_id)
        payload = build_competition_matches(conn, comp_id, gameset)

        if payload is None:
            # unknown competition (or gameset): ask the worker for one
            # discovery scrape, then 404 until it lands
            _enqueue(conn, jobs.KIND_COMP_DISCOVERY, comp_id)
            return jsonify({"error": "competition not found"}), 404

        # An empty round never blocks on goal.com:
        #   * empty round + fresh data  -> legitimately empty, serve it
        #   * empty round + stale data  -> serve now, refresh in the
        #     background (refreshing=true tells the UI to re-fetch)
        if payload["gameset"]["matchCount"] == 0:
            scrape = _comp_scrape_row(conn, comp_id)
            if scrape is None:
                _enqueue(conn, jobs.KIND_COMP_REFRESH, comp_id)
                return _cache_and_respond(
                    key, {**payload, "refreshing": True},
                    apicache.TTL_REFRESHING)
            if _comp_data_stale(scrape):
                _enqueue(conn, jobs.KIND_COMP_REFRESH, comp_id)
                return _cache_and_respond(
                    key, {**payload, "refreshing": True},
                    apicache.TTL_REFRESHING)

        return _cache_and_respond(key, payload,
                                  _payload_ttl(payload, apicache.TTL_COMP_MATCHES))

    # ---- GET /api/team/<id> --------------------------------------------------
    @app.get("/api/team/<team_id>")
    def api_team(team_id: str):
        if not re.match(r"^[A-Za-z0-9_-]{4,64}$", team_id):
            return jsonify({"error": "invalid team id"}), 400

        key = apicache.k_team(team_id)
        hit = apicache.get(key)
        if hit is not None:
            return _response_from_cache(hit)

        conn = get_conn()
        team = build_team(conn, team_id)
        if team is None:
            return jsonify({"error": "team not found"}), 404
        return _cache_and_respond(key, team, apicache.TTL_TEAM)

    # ---- GET /api/player/<id> ------------------------------------------------
    @app.get("/api/player/<player_id>")
    def api_player(player_id: str):
        if not re.match(r"^[A-Za-z0-9_-]{4,64}$", player_id):
            return jsonify({"error": "invalid player id"}), 400

        key = apicache.k_player(player_id)
        hit = apicache.get(key)
        if hit is not None:
            return _response_from_cache(hit)

        conn = get_conn()
        player = build_player(conn, player_id)

        # known player but never profile-fetched? record the gap for the
        # worker and serve the stub row we have (name from lineups/events);
        # profileFetched=false tells the UI to re-fetch once the profile
        # lands. Unknown ids stay a plain 404 - nothing to enrich.
        if player is not None and not player["profileFetched"]:
            _enqueue(conn, jobs.KIND_PLAYER_PROFILE, player_id)

        if player is None:
            return jsonify({"error": "player not found"}), 404
        return _cache_and_respond(key, player, _player_ttl(player))

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
        if parts.scheme != "https" or not img_host_allowed(parts.hostname or ""):
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

        # The API never scrapes: the poke becomes a refresh_jobs row and the
        # worker runs it within a few seconds (force=True - an external
        # cron replaces the scheduler, so pokes are never rate-limited).
        conn = get_conn()
        _enqueue(conn, jobs.KIND_CRON_REFRESH, utc_today(), force=True)
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
            pending_jobs = conn.execute(
                "SELECT COUNT(*) AS n FROM refresh_jobs WHERE done_at IS NULL"
            ).fetchone()["n"]
        except psycopg.Error as exc:
            return jsonify({"ok": False, "error": str(exc)}), 500
        return jsonify({
            "ok": True,
            "db": backend.display_dsn(API_DB_URL),
            "role": "api (read-only - scraper worker is a separate process)",
            "cache": apicache.status(),
            "counts": counts,
            "latestMatchSeen": latest_match,
            "lastRuns": last_runs,
            "pendingJobs": pending_jobs,
        })

    return app


def run(host: str = "127.0.0.1", port: int = 9000, db_url: Optional[str] = None,
        debug: bool = False) -> None:
    """Entry point used by `python -m scraper.cli api` (read-only API).

    Data freshness comes from `python -m scraper.cli worker` - run it next to
    this server (same machine, same FOOTBALL_DB_URL) or the database goes
    stale while the API keeps happily serving what it has.
    """
    logging.basicConfig(level=logging.DEBUG if debug else logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    app = create_app(db_url)

    log.info("API serving on http://%s:%d (db: %s, read-only - "
             "run `python -m scraper.cli worker` for freshness)",
             host, port, backend.display_dsn(API_DB_URL))
    app.run(host=host, port=port, debug=debug, threaded=True, use_reloader=False)
