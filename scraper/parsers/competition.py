"""Parsers for goal.com competition pages (standings + per-round matches).

Two sources per competition:

1. Table page  /{lang}/{slug}/table/{competition_id}
   __NEXT_DATA__ content.standings = {tables: [...], markers: [...]} with the
   FULL table (20+ rows) in English. The Arabic /table URL only renders a
   top-5 summary widget, so Arabic team names are merged from the matches
   API below (teams table cross-fill) instead.

2. Internal JSON API  /api/competition-matches?id={competition_id}&edition={lang}
   Returns every gameset (round) of the current season WITH its matches -
   one request per language. EN edition gives structure + English names,
   AR edition gives Arabic team/venue/competition names and Arabic gameset
   names ("الجولة 1") plus Arabic link slugs.

The URL slug is mostly ignored by the server (only the competition ID
matters), but a few competitions 404 on arbitrary slugs, so we slugify the
English competition name - which works for every league checked.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from .. import config
from ..http_client import fetch_json_quiet, fetch_next_data_quiet

log = logging.getLogger("scraper.competition")

COMPETITION_MATCHES_API = "https://www.goal.com/api/competition-matches"


def _slugify(name: Optional[str]) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "x"


def table_url(comp_id: str, name_en: Optional[str], lang: str = "en") -> str:
    return f"{config.GOAL_BASE}/{lang}/{_slugify(name_en)}/table/{comp_id}"


# ---------------------------------------------------------------------------
# standings (table page)
# ---------------------------------------------------------------------------
def parse_standings_page(next_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Parse the EN table page -> seasons + standings + markers legend."""
    content = next_data["props"]["pageProps"]["content"]
    standings = content.get("standings")
    if not isinstance(standings, dict):
        return None

    seasons = [
        {"id": s.get("id"), "name": s.get("name"), "is_active": 1 if s.get("active") else 0}
        for s in (content.get("seasons") or [])
        if s.get("id")
    ]
    active = next((s for s in seasons if s["is_active"]), seasons[0] if seasons else None)

    tables: List[Dict[str, Any]] = []
    for tbl in standings.get("tables") or []:
        rows = []
        for rank in tbl.get("rankings") or []:
            team = rank.get("team") or {}
            if not team.get("id"):
                continue
            rows.append(
                {
                    "position": rank.get("position"),
                    "team": {
                        "id": team.get("id"),
                        "name_en": team.get("long") or team.get("name"),
                        "short_name_en": team.get("name") or team.get("code"),
                        "code": team.get("code"),
                        "crest_url": (team.get("image") or {}).get("url"),
                    },
                    "played": rank.get("played"),
                    "win": rank.get("win"),
                    "draw": rank.get("draw"),
                    "lose": rank.get("lose"),
                    "goals_for": rank.get("goalsFor"),
                    "goals_against": rank.get("goalsAgainst"),
                    "goal_diff": rank.get("goalsDifference"),
                    "points": rank.get("points"),
                    "form": [
                        {"wdl": f.get("wdl"), "match_id": (f.get("match") or {}).get("id")}
                        for f in (rank.get("form") or [])
                    ],
                    "markers": [m.get("id") for m in (rank.get("markers") or []) if m.get("id")],
                }
            )
        if rows:
            tables.append({"name": tbl.get("name"), "rows": rows})

    if not tables:
        return None

    markers = [
        {"id": m.get("id"), "name": m.get("name"), "type": m.get("type")}
        for m in (standings.get("markers") or [])
        if m.get("id")
    ]

    comp = content.get("competition") or {}
    return {
        "competition": {
            "id": comp.get("id"),
            "name_en": comp.get("name"),
            "area_name_en": (comp.get("area") or {}).get("name"),
            "image_url": (comp.get("image") or {}).get("url"),
        },
        "seasons": seasons,
        "active_season": active,
        "stage": "total",
        "tables": tables,
        "markers": markers,
    }


def fetch_standings(comp_id: str, name_en: Optional[str]) -> Optional[Dict[str, Any]]:
    """Fetch + parse the English table page for one competition.

    Returns None when the competition has no table (cups, qualifiers) -
    those simply have no standings feature.
    """
    data = fetch_next_data_quiet(table_url(comp_id, name_en, "en"))
    if not data:
        return None
    parsed = parse_standings_page(data)
    if parsed:
        log.info("standings for %s: %d tables, %d rows",
                 comp_id, len(parsed["tables"]),
                 sum(len(t["rows"]) for t in parsed["tables"]))
    return parsed


# ---------------------------------------------------------------------------
# per-round matches (internal JSON API)
# ---------------------------------------------------------------------------
def _period_str(period: Any) -> Optional[str]:
    if isinstance(period, dict):
        label = period.get("type") or "LIVE"
        minute = period.get("minute")
        extra = period.get("extra")
        if minute is not None:
            label += f" {minute}"
            if extra:
                label += f"+{extra}"
        return label
    return period if isinstance(period, str) else None


def _team_fields(team: Dict[str, Any], lang: str) -> Dict[str, Any]:
    image = team.get("image") or {}
    if lang == "ar":
        return {"id": team.get("id"), "name_ar": team.get("name")}
    return {
        "id": team.get("id"),
        "name_en": team.get("name"),
        "short_name_en": team.get("codeName"),
        "code": team.get("codeName"),
        "crest_url": image.get("url"),
    }


def _normalize_match(m: Dict[str, Any], gameset_name: Optional[str],
                     lang: str) -> Optional[Dict[str, Any]]:
    """Competition-API match row -> listing-compatible row (for upserts)."""
    team_a = m.get("teamA") or {}
    team_b = m.get("teamB") or {}
    if not team_a.get("id") or not team_b.get("id"):
        return None

    score = m.get("score") or {}
    agg = m.get("aggregateScore") or {}
    pens = m.get("penaltyScore") or {}
    reds = m.get("redCards") or {}
    link = m.get("link") or {}

    row: Dict[str, Any] = {
        "match_id": m.get("id"),
        "kickoff_utc": m.get("startDate"),
        "status": m.get("status"),
        "period": _period_str(m.get("period")),
        "home_team": _team_fields(team_a, lang),
        "away_team": _team_fields(team_b, lang),
        "home_score": score.get("teamA"),
        "away_score": score.get("teamB"),
        "home_agg_score": agg.get("teamA"),
        "away_agg_score": agg.get("teamB"),
        "home_pen_score": pens.get("teamA"),
        "away_pen_score": pens.get("teamB"),
        "home_red_cards": reds.get("teamA", 0) or 0,
        "away_red_cards": reds.get("teamB", 0) or 0,
        "round_name": (m.get("round") or {}).get("name") if lang == "en" else None,
        "gameset_name": gameset_name if lang == "en" else None,
        "gameset_name_ar": gameset_name if lang == "ar" else None,
        "last_updated_at": m.get("lastUpdatedAt"),
    }
    venue_name = (m.get("venue") or {}).get("name")
    if lang == "en":
        row["venue_name_en"] = venue_name
        row["slug_en"] = link.get("slug")
    else:
        row["venue_name_ar"] = venue_name
        row["slug_ar"] = link.get("slug")

    comp = m.get("competition") or {}
    area = comp.get("area") or {}
    competition = {"id": comp.get("id"), "image_url": None}
    if lang == "ar":
        competition["name_ar"] = comp.get("name")
        competition["area_name_ar"] = area.get("name")
    else:
        competition["name_en"] = comp.get("name")
        competition["area_name_en"] = area.get("name")
    row["competition"] = competition
    return row


def parse_competition_matches(payload: Dict[str, Any], lang: str = "en") -> List[Dict[str, Any]]:
    """Normalize the /api/competition-matches payload into listing rows.

    Every returned row is stamped with its gameset name + gameSetTypeId so
    round grouping survives the database round trip.
    """
    rows: List[Dict[str, Any]] = []
    for gs in payload.get("gamesets") or []:
        gs_name = gs.get("name")
        gs_type_id = gs.get("gameSetTypeId")
        for m in gs.get("matches") or []:
            row = _normalize_match(m, gs_name, lang)
            if row:
                # round key used by the matches.gameset_id column
                row["gameset_id"] = gs_type_id
                rows.append(row)
    return rows


def fetch_competition_matches(comp_id: str, lang: str = "en") -> Optional[Dict[str, Any]]:
    """Fetch all gamesets + matches for a competition in one language.

    Returns {"matches": [listing-compatible rows], "gamesets": [meta]} or
    None when the endpoint has no data for this competition.
    """
    payload = fetch_json_quiet(
        COMPETITION_MATCHES_API, params={"id": comp_id, "edition": lang}
    )
    if not payload:
        return None
    rows = parse_competition_matches(payload, lang)
    # gameset metadata (kept even for empty gamesets -> round list)
    meta = [
        {
            "game_set_type_id": gs.get("gameSetTypeId"),
            "name": gs.get("name"),
            "is_active": 1 if gs.get("active") else 0,
            "match_count": len(gs.get("matches") or []),
        }
        for gs in (payload.get("gamesets") or [])
        if gs.get("gameSetTypeId")
    ]
    log.info("competition %s (%s): %d matches in %d gamesets",
             comp_id, lang, len(rows), len(meta))
    return {"matches": rows, "gamesets": meta}
