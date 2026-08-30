"""Parsers for goal.com (English + Arabic).

goal.com serves the SAME data in ~40 locales with IDENTICAL entity IDs
(matches, teams, competitions, players). We use the English pages for
structure + English names, and the Arabic pages purely to fill Arabic name
columns. Because both languages come from the same site, EN/AR rows are
always consistent.

Pages used (per date, page type depends on whether the date is past/today):
  * EN listing : /en/results/{date} | /en/live-scores | /en/fixtures/{date}
  * AR listing : /ar/النتائج/{date} | /ar/مباريات-جارية-حاليًا | /ar/مواعيد-المباريات/{date}
  * EN detail  : /en/match/{slug}/{id}
  * AR detail  : /ar/المباراة/{slug}/{id}
    (the slug is ignored by the server - only the match ID matters)
"""

from __future__ import annotations

import logging
from datetime import date as date_cls, datetime, timezone
from typing import Any, Dict, List, Optional

from .. import config
from ..http_client import fetch_next_data, fetch_next_data_quiet

log = logging.getLogger("scraper.goal")


# ---------------------------------------------------------------------------
# URL selection
# ---------------------------------------------------------------------------
def day_type_for(date: str, today: Optional[str] = None) -> str:
    """Classify a date as past / today / future (UTC-based)."""
    if today is None:
        today = datetime.now(timezone.utc).date().isoformat()
    if date < today:
        return "past"
    if date > today:
        return "future"
    return "today"


LISTING_URLS = {
    ("en", "past"): config.GOAL_RESULTS_URL,
    ("en", "today"): config.GOAL_LIVE_URL,
    ("en", "future"): config.GOAL_FIXTURES_URL,
    ("ar", "past"): config.GOAL_AR_RESULTS_URL,
    ("ar", "today"): config.GOAL_AR_LIVE_URL,
    ("ar", "future"): config.GOAL_AR_FIXTURES_URL,
}


def listing_url(date: str, lang: str = "en", today: Optional[str] = None) -> str:
    day_type = day_type_for(date, today)
    url = LISTING_URLS[(lang, day_type)]
    return url.format(date=date) if "{date}" in url else url


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def has_arabic(text: Optional[str]) -> bool:
    if not text:
        return False
    return any("\u0600" <= ch <= "\u06FF" for ch in text)


def _period_str(period: Any) -> Optional[str]:
    """Flatten the provider's period object (live matches) to a string."""
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


def _team_fields(team: Dict[str, Any], lang: str = "en") -> Dict[str, Any]:
    image = team.get("image") or {}
    if lang == "ar":
        # Arabic pages put the Arabic name in every field (incl. code) - only
        # take the display name so the English 3-letter code stays intact.
        return {"id": team.get("id"), "name_ar": team.get("name"), "crest_url": None}
    return {
        "id": team.get("id"),
        "name_en": team.get("long") or team.get("full") or team.get("name") or team.get("short"),
        "short_name_en": team.get("short") or team.get("code"),
        "code": team.get("code"),
        "crest_url": image.get("url"),
    }


def _person(person: Optional[Dict[str, Any]], lang: str = "en") -> Dict[str, Any]:
    person = person or {}
    image = person.get("image") or {}
    out = {
        "id": person.get("id"),
        "image_url": None if (image.get("isPlaceholder") or not image.get("url")) else image.get("url"),
        "verified": bool(person.get("verified")),
    }
    name = person.get("name")
    if lang == "ar":
        out["name_ar"] = name if has_arabic(name) else None
    else:
        out["name_en"] = name
    return out


# ---------------------------------------------------------------------------
# fixtures listing (works for results / live-scores / fixtures pages alike)
# ---------------------------------------------------------------------------
def parse_fixtures_page(next_data: Dict[str, Any], lang: str = "en") -> List[Dict[str, Any]]:
    """Return normalized match rows from a listing page in either language."""
    content = next_data["props"]["pageProps"]["content"]
    live_scores = content.get("liveScores") or []
    rows: List[Dict[str, Any]] = []

    for block in live_scores:
        comp = block.get("competition") or {}
        comp_image = comp.get("image") or {}
        comp_id = comp.get("id")
        if not comp_id:
            continue
        area = comp.get("area") or {}

        competition: Dict[str, Any] = {"id": comp_id, "image_url": comp_image.get("url")}
        if lang == "ar":
            competition["name_ar"] = comp.get("name")
            competition["area_name_ar"] = area.get("name")
        else:
            competition["name_en"] = comp.get("name")
            competition["area_name_en"] = area.get("name")
            competition["area_code"] = area.get("code")

        for m in block.get("matches") or []:
            team_a = _team_fields(m.get("teamA") or {}, lang)
            team_b = _team_fields(m.get("teamB") or {}, lang)
            if not team_a.get("id") or not team_b.get("id"):
                continue  # TBD opponents in future cups

            score = m.get("score") or {}
            agg = m.get("agg") or {}
            reds = m.get("redCards") or {}
            gameset = m.get("gameset") or {}
            link = m.get("link") or {}

            row: Dict[str, Any] = {
                "match_id": m.get("id"),
                "kickoff_utc": m.get("startDate"),
                "status": m.get("status"),
                "period": _period_str(m.get("period")),
                "home_team": team_a,
                "away_team": team_b,
                "home_score": score.get("teamA"),
                "away_score": score.get("teamB"),
                "home_agg_score": agg.get("teamA"),
                "away_agg_score": agg.get("teamB"),
                "home_red_cards": reds.get("teamA", 0),
                "away_red_cards": reds.get("teamB", 0),
                "gameset_name": gameset.get("name") if lang == "en" else None,
                "gameset_name_ar": gameset.get("name") if lang == "ar" else None,
                "last_updated_at": m.get("lastUpdatedAt"),
                "competition": competition,
            }
            if lang == "en":
                row["venue_name_en"] = (m.get("venue") or {}).get("name")
                row["round_name"] = (m.get("round") or {}).get("name")
                row["slug_en"] = link.get("slug")
            else:
                row["venue_name_ar"] = (m.get("venue") or {}).get("name")
                row["slug_ar"] = link.get("slug")
            rows.append(row)
    return rows


def fetch_fixtures(date: str, lang: str = "en", today: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetch + parse a goal.com listing page for one date in one language."""
    url = listing_url(date, lang, today)
    data = fetch_next_data(url)
    rows = parse_fixtures_page(data, lang)
    log.info("goal.com/%s %s: %d matches", lang, date, len(rows))
    return rows


# ---------------------------------------------------------------------------
# match detail
# ---------------------------------------------------------------------------
def parse_match_detail(next_data: Dict[str, Any], lang: str = "en") -> Optional[Dict[str, Any]]:
    """Normalize the rich match payload (lineups, events, stats, ...)."""
    match = next_data["props"]["pageProps"]["content"].get("match")
    if not match or not match.get("id"):
        return None

    comp = match.get("competition") or {}
    season = match.get("season") or {}
    venue = match.get("venue") or {}
    score = match.get("score") or {}
    agg = match.get("agg") or {}
    penalty = match.get("penalty") or {}
    ht = match.get("halfTime") or {}
    ft = match.get("fullTime") or {}
    et = match.get("extraTime") or {}

    referee = match.get("referee")
    if isinstance(referee, list):
        referee = referee[0].get("name") if referee and isinstance(referee[0], dict) else None
    elif isinstance(referee, dict):
        referee = referee.get("name")

    area = comp.get("area") or {}
    competition: Dict[str, Any] = {"id": comp.get("id"), "image_url": (comp.get("image") or {}).get("url")}
    if lang == "ar":
        competition["name_ar"] = comp.get("name")
        competition["area_name_ar"] = area.get("name")
    else:
        competition["name_en"] = comp.get("name")
        competition["area_name_en"] = area.get("name")
        competition["area_code"] = area.get("code")

    # ---- events (source list is newest-first -> reverse for chronological) --
    events: List[Dict[str, Any]] = []
    for order, ev in enumerate(reversed(match.get("events") or [])):
        minute = (ev.get("period") or {}).get("minute")
        extra = (ev.get("period") or {}).get("extra")
        ev_type = ev.get("type") or "UNKNOWN"
        side = ev.get("side")

        row = {
            "sort_order": order,
            "team_side": "home" if side == "TEAM_A" else "away" if side == "TEAM_B" else None,
            "event_type": ev_type,
            "minute": minute,
            "extra_minute": extra,
            "player": _person(ev.get("scorer") or ev.get("player"), lang),
            "related_player": _person(ev.get("assist") or ev.get("in"), lang),
            # VAR review details: outcome/decision tell whether a goal or
            # penalty was cancelled (e.g. offside) or confirmed
            "outcome": ev.get("outcome"),
            "decision": ev.get("decision"),
        }
        if ev_type == "SUBSTITUTION":
            row["player"] = _person(ev.get("out"), lang)          # player going off
            row["related_player"] = _person(ev.get("in"), lang)   # player coming on

        after = ev.get("score") or {}
        row["home_score_after"] = after.get("teamA")
        row["away_score_after"] = after.get("teamB")
        events.append(row)

    # ---- lineups -----------------------------------------------------------
    lineups = {"confirmed": bool((match.get("lineups") or {}).get("confirmed")), "teams": {}}
    for side_key, team_key in (("teamA", "home"), ("teamB", "away")):
        side = (match.get("lineups") or {}).get(side_key) or {}
        team_ref = match.get("teamA") if side_key == "teamA" else match.get("teamB")

        entries: List[Dict[str, Any]] = []
        for is_starter, players in ((1, side.get("lineup") or []), (0, side.get("substitutes") or [])):
            for p in players:
                person = _person(p.get("person"), lang)
                pitch = p.get("pitchPosition") or {}
                entries.append(
                    {
                        "person": person,
                        "is_starter": is_starter,
                        "shirt_number": p.get("shirtNumber"),
                        "position_x": pitch.get("x") if is_starter else None,
                        "position_y": pitch.get("y") if is_starter else None,
                        "is_captain": 1 if p.get("isCaptain") else 0,
                        "rating": p.get("score"),
                    }
                )

        lineups["teams"][team_key] = {
            "team_id": team_ref.get("id") if team_ref else None,
            "formation": side.get("formation"),
            "manager": _person(side.get("manager"), lang),
            "entries": entries,
        }

    # ---- stats (summary section only - avoids duplicated stat rows) --------
    stats: List[Dict[str, Any]] = []
    for s in ((match.get("stats") or {}).get("summary")) or []:
        if s.get("type") and s.get("teamA") is not None:
            stats.append(
                {
                    "stat_type": s["type"],
                    "home_value": s.get("teamA"),
                    "away_value": s.get("teamB"),
                }
            )

    venue_out: Dict[str, Any] = {"latitude": venue.get("latitude"), "longitude": venue.get("longitude")}
    if lang == "ar":
        venue_out["name_ar"] = venue.get("name")
    else:
        venue_out["name_en"] = venue.get("name")

    return {
        "match_id": match["id"],
        "kickoff_utc": match.get("startDate"),
        "status": match.get("status"),
        "period": _period_str(match.get("period")),
        "home_score": score.get("teamA"),
        "away_score": score.get("teamB"),
        "home_agg_score": agg.get("teamA"),
        "away_agg_score": agg.get("teamB"),
        "home_pen_score": penalty.get("teamA"),
        "away_pen_score": penalty.get("teamB"),
        "home_score_ht": ht.get("teamA"),
        "away_score_ht": ht.get("teamB"),
        "home_score_ft": ft.get("teamA"),
        "away_score_ft": ft.get("teamB"),
        "home_score_et": et.get("teamA"),
        "away_score_et": et.get("teamB"),
        "venue": venue_out,
        "referee": referee,
        "round_name": (match.get("round") or {}).get("name") if lang == "en" else None,
        "gameset_name": (match.get("gameset") or {}).get("name") if lang == "en" else None,
        "gameset_name_ar": (match.get("gameset") or {}).get("name") if lang == "ar" else None,
        "season": {"id": season.get("id"), "name": season.get("name"),
                   "is_active": 1 if season.get("active") else 0},
        "competition": competition,
        "home_team": _team_fields(match.get("teamA") or {}, lang),
        "away_team": _team_fields(match.get("teamB") or {}, lang),
        "events": events,
        "lineups": lineups,
        "stats": stats,
    }


def fetch_match_detail(match_id: str, lang: str = "en",
                       slug: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetch one match detail page in one language (slug is optional)."""
    template = config.GOAL_AR_MATCH_URL if lang == "ar" else config.GOAL_MATCH_URL
    url = template.format(slug=slug or "x", match_id=match_id)
    data = fetch_next_data_quiet(url)
    return parse_match_detail(data, lang) if data else None


# ---------------------------------------------------------------------------
# bilingual merge
# ---------------------------------------------------------------------------
def merge_ar_detail(en_detail: Dict[str, Any], ar_detail: Dict[str, Any]) -> None:
    """Fill Arabic names into an English detail payload (in place).

    Player identities are matched by the shared sportfeeds ID, so the merge
    is exact - no fuzzy name matching.
    """
    # Arabic player names by person id (from lineups + events)
    ar_names: Dict[str, str] = {}
    for side in ("home", "away"):
        team = (ar_detail.get("lineups") or {}).get("teams", {}).get(side) or {}
        for entry in team.get("entries", []):
            person = entry.get("person") or {}
            if person.get("id") and person.get("name_ar"):
                ar_names[person["id"]] = person["name_ar"]
    for ev in ar_detail.get("events", []):
        for key in ("player", "related_player"):
            person = ev.get(key) or {}
            if person.get("id") and person.get("name_ar"):
                ar_names.setdefault(person["id"], person["name_ar"])

    def _fill(person: Dict[str, Any]) -> None:
        if person.get("id"):
            ar_name = ar_names.get(person["id"])
            if ar_name:
                person["name_ar"] = ar_name

    # events
    for ev in en_detail.get("events", []):
        _fill(ev.get("player") or {})
        _fill(ev.get("related_player") or {})

    # lineups + managers
    for side in ("home", "away"):
        en_team = (en_detail.get("lineups") or {}).get("teams", {}).get(side) or {}
        ar_team = (ar_detail.get("lineups") or {}).get("teams", {}).get(side) or {}
        for entry in en_team.get("entries", []):
            _fill(entry.get("person") or {})
        en_manager = en_team.get("manager") or {}
        ar_manager = ar_team.get("manager") or {}
        if ar_manager.get("name_ar"):
            en_manager["name_ar"] = ar_manager["name_ar"]

    # venue + gameset Arabic names
    if (ar_detail.get("venue") or {}).get("name_ar"):
        en_detail.setdefault("venue", {})["name_ar"] = ar_detail["venue"]["name_ar"]
    if ar_detail.get("gameset_name_ar"):
        en_detail["gameset_name_ar"] = ar_detail["gameset_name_ar"]


def fetch_match_detail_bilingual(match_id: str, slug: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetch EN detail (full structure) + AR detail (Arabic names) and merge."""
    en_detail = fetch_match_detail(match_id, lang="en", slug=slug)
    if en_detail is None:
        return None
    ar_detail = fetch_match_detail(match_id, lang="ar")
    if ar_detail is not None:
        merge_ar_detail(en_detail, ar_detail)
    else:
        log.debug("no Arabic detail for %s - storing English only", match_id)
    return en_detail


# ---------------------------------------------------------------------------
# Player profile + career history
#
# goal.com player page: /en/player/{slug}/{player_id}
#   * /ar/اللاعب/{slug}/{player_id} mirrors it with Arabic names
#   * slug is ignored by the server (same convention as match pages)
#
# The page's __NEXT_DATA__.props.pageProps.content.player holds:
#   - id, name, image, position, shirtNumber, height, weight, age, birthDate
#   - nationality {name, code}, placeOfBirth, countryOfBirth
#   - currentClub {id, name, image}    (may be missing for retired players)
#   - careerHistory: [{team, season, competition, appearances, goals,
#                      assists, yellowCards, redCards, minutes, isLoan}]
#   - statistics: aggregated career totals (we store these as career rows too)
# ---------------------------------------------------------------------------
def _team_ref(t: Optional[Dict[str, Any]], lang: str = "en") -> Dict[str, Any]:
    """Compact team reference {id, name_en|name_ar, image_url}."""
    t = t or {}
    out: Dict[str, Any] = {"id": t.get("id"), "image_url": None}
    img = t.get("image") or {}
    if img and not img.get("isPlaceholder") and img.get("url"):
        out["image_url"] = img["url"]
    if lang == "ar":
        out["name_ar"] = t.get("name")
    else:
        out["name_en"] = t.get("name")
    return out


def parse_player_page(next_data: Dict[str, Any], lang: str = "en") -> Optional[Dict[str, Any]]:
    """Normalize a goal.com player profile page (bio + career history)."""
    player = next_data["props"]["pageProps"]["content"].get("player")
    if not player or not player.get("id"):
        return None

    image = player.get("image") or {}
    image_url = None if image.get("isPlaceholder") or not image.get("url") else image.get("url")
    nationality = player.get("nationality") or {}
    birth_place = player.get("placeOfBirth") or {}
    country_of_birth = player.get("countryOfBirth") or {}
    current_club = player.get("currentClub") or {}
    link = player.get("link") or {}

    out: Dict[str, Any] = {
        "id": player.get("id"),
        "image_url": image_url,
        "verified": bool(player.get("verified")),
        "position": player.get("position"),
        "shirt_number": player.get("shirtNumber"),
        "height_cm": player.get("height"),
        "weight_kg": player.get("weight"),
        "age": player.get("age"),
        "birth_date": player.get("birthDate"),
        "slug_en": link.get("slug") if lang == "en" else None,
        "slug_ar": link.get("slug") if lang == "ar" else None,
        "current_club_id": current_club.get("id"),
        "career": [],
    }

    if lang == "ar":
        out["name_ar"] = player.get("name")
        out["full_name_ar"] = player.get("fullName") or player.get("name")
        out["nationality_ar"] = nationality.get("name")
        out["country_of_birth_ar"] = country_of_birth.get("name")
        out["place_of_birth_ar"] = birth_place.get("name")
        out["current_club_name_ar"] = current_club.get("name")
    else:
        out["name_en"] = player.get("name")
        out["full_name_en"] = player.get("fullName") or player.get("name")
        out["nationality_en"] = nationality.get("name")
        out["country_of_birth_en"] = country_of_birth.get("name")
        out["place_of_birth_en"] = birth_place.get("name")
        out["current_club_name_en"] = current_club.get("name")

    # Career history - the provider returns the most recent season first; we
    # preserve that order via sort_order so the API can render the timeline
    # newest-first without an extra sort.
    for i, entry in enumerate(player.get("careerHistory") or []):
        team = _team_ref(entry.get("team") or entry.get("club"), lang)
        comp = entry.get("competition") or {}
        season = entry.get("season") or {}
        row = {
            "team_id": team.get("id"),
            "team_image_url": team.get("image_url"),
            "team_name_en": team.get("name_en"),
            "team_name_ar": team.get("name_ar"),
            "season_name": season.get("name") or entry.get("seasonName"),
            "competition_id": comp.get("id"),
            "competition_name_en": comp.get("name") if lang == "en" else None,
            "competition_name_ar": comp.get("name") if lang == "ar" else None,
            "appearances": entry.get("appearances"),
            "goals": entry.get("goals"),
            "assists": entry.get("assists"),
            "yellow_cards": entry.get("yellowCards"),
            "red_cards": entry.get("redCards"),
            "minutes_played": entry.get("minutes") or entry.get("minutesPlayed"),
            "is_loan": 1 if entry.get("isLoan") or entry.get("loan") else 0,
            "sort_order": i,
        }
        out["career"].append(row)
    return out


def fetch_player(player_id: str, lang: str = "en",
                 slug: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetch one player's profile page in one language (slug is optional)."""
    template = config.GOAL_AR_PLAYER_URL if lang == "ar" else config.GOAL_PLAYER_URL
    url = template.format(slug=slug or "x", player_id=player_id)
    data = fetch_next_data_quiet(url)
    return parse_player_page(data, lang) if data else None


def fetch_player_bilingual(player_id: str,
                            slug: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Fetch EN profile (full bio + career) + AR profile (Arabic names) and merge.

    Player IDs are shared between EN and AR pages so the merge is exact.
    """
    en = fetch_player(player_id, lang="en", slug=slug)
    if en is None:
        return None
    ar = fetch_player(player_id, lang="ar")
    if ar is None:
        log.debug("no Arabic profile for player %s - storing English only", player_id)
        return en

    # Fill Arabic name fields onto the English payload
    for k in ("name_ar", "full_name_ar", "nationality_ar", "country_of_birth_ar",
              "place_of_birth_ar", "current_club_name_ar", "slug_ar"):
        if ar.get(k):
            en[k] = ar[k]

    # Merge Arabic team / competition names into career rows by (team_id,
    # season_name, competition_id). Use a stable key so partial AR data
    # (e.g. competition_name_ar missing) doesn't break the merge.
    ar_names: Dict[str, str] = {}
    for r in ar.get("career", []):
        key = "|".join(str(r.get(k) or "") for k in
                       ("team_id", "season_name", "competition_id"))
        if r.get("team_name_ar"):
            ar_names[key + "|team"] = r["team_name_ar"]
        if r.get("competition_name_ar"):
            ar_names[key + "|comp"] = r["competition_name_ar"]
    for r in en["career"]:
        key = "|".join(str(r.get(k) or "") for k in
                       ("team_id", "season_name", "competition_id"))
        if not r.get("team_name_ar"):
            r["team_name_ar"] = ar_names.get(key + "|team")
        if not r.get("competition_name_ar"):
            r["competition_name_ar"] = ar_names.get(key + "|comp")
    return en
