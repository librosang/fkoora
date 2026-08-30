"""Scraping pipeline: orchestration of goal.com EN + AR into the database.

Flow for one date:
  1. goal.com EN listing (results / live-scores / fixtures - auto-selected)
       -> upsert competitions / teams / matches with English names
  2. goal.com AR listing (same page type in Arabic)
       -> fill name_ar on competitions / teams / venues / gamesets
  3. (optional) match details, bilingual:
       -> EN detail page: lineups, events, stats, scores, referee, season
       -> AR detail page: Arabic player / manager / venue names (merged by ID)

kooora.com remains available as an extra Arabic fallback (--kooora flag).
"""

from __future__ import annotations

import contextlib
import logging
import time
from datetime import date as date_cls, datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional

from . import config
from .db.database import Database
from .major import is_major_competition
from .parsers import competition as competition_parser
from .parsers import goal as goal_parser
from .parsers import kooora as kooora_parser

log = logging.getLogger("scraper.pipeline")


# ---------------------------------------------------------------------------
# competition filter
# ---------------------------------------------------------------------------
def _rule_matches(frag: str, name: str) -> bool:
    """'=x' means exact match, otherwise substring."""
    if frag.startswith("="):
        return name == frag[1:]
    return frag in name


def _excluded(name: str) -> bool:
    return any(frag in name for frag in config.DEFAULT_COMPETITION_EXCLUDE)


def default_filter(comp: dict) -> bool:
    """The shared "major leagues & cups" classification (scraper/major.py).

    Used to decide which matches get expensive detail enrichment; the JSON
    API uses the same function for the frontend's major-only toggle, so what
    we enrich is exactly what the UI shows by default.
    """
    return is_major_competition(comp)


def make_competition_filter(patterns: Optional[List[str]]):
    """Return predicate(comp_row) -> bool. None means 'match everything'.

    Each pattern is a name substring, optionally suffixed with "@area"
    to disambiguate leagues that share a name, and optionally prefixed
    with "=" for an exact match, e.g.:
        "premier league@england", "=serie a@italy", "=laliga", "saudi"
    """
    if patterns is None or not patterns:
        return None
    parsed = []
    for p in patterns:
        p = p.lower().strip()
        if not p:
            continue
        if "@" in p:
            name_frag, area_frag = p.split("@", 1)
            parsed.append((name_frag.strip(), area_frag.strip() or None))
        else:
            parsed.append((p, None))

    def _match(comp: dict) -> bool:
        name = (comp.get("name_en") or "").lower()
        area = (comp.get("area_name_en") or "").lower()
        return any(
            _rule_matches(nf, name) and (af is None or af in area)
            for nf, af in parsed
        )

    return _match


# ---------------------------------------------------------------------------
# step 1 + 2: listings (EN + AR)
# ---------------------------------------------------------------------------
def scrape_date_listings(db: Database, date: str, arabic: bool = True,
                         kooora: bool = False) -> int:
    """Scrape goal.com listings for one date in both languages.

    Returns the number of matches stored from the English listing.
    """
    run_id = db.start_run("date", date, "goal" + ("+ar" if arabic else ""))

    # ---- goal.com English listing ------------------------------------------
    try:
        rows = goal_parser.fetch_fixtures(date, lang="en")
    except Exception as exc:  # noqa: BLE001
        log.error("goal.com EN listing failed for %s: %s", date, exc)
        db.finish_run(run_id, "error", error=str(exc))
        raise

    for row in rows:
        db.upsert_competition(row["competition"])
        db.upsert_team(row["home_team"])
        db.upsert_team(row["away_team"])
        db.upsert_match_from_listing(row, listed_date=date)
    db.commit()
    n_comps = len({row["competition"]["id"] for row in rows})

    # ---- goal.com Arabic listing (fill Arabic names, same IDs) -------------
    n_arabic = 0
    if arabic:
        try:
            ar_rows = goal_parser.fetch_fixtures(date, lang="ar")
            for row in ar_rows:
                db.upsert_competition(row["competition"])
                db.upsert_team(row["home_team"])
                db.upsert_team(row["away_team"])
                db.upsert_match_from_listing(row, listed_date=date)
                db.update_match_venue_ar(row["match_id"], row.get("venue_name_ar"))
                n_arabic += 1
            db.commit()
        except Exception as exc:  # noqa: BLE001
            # Arabic enrichment is best-effort: keep going if the AR page fails
            log.warning("goal.com AR listing failed for %s: %s", date, exc)

    # ---- optional kooora fallback -------------------------------------------
    if kooora:
        try:
            for row in kooora_parser.fetch_fixtures(date):
                db.upsert_competition(row["competition"])
                db.upsert_team(row["home_team"])
                db.upsert_team(row["away_team"])
                db.upsert_match_from_listing(row, listed_date=date)
                db.update_match_venue_ar(row["match_id"], row.get("venue_name_ar"))
            db.commit()
        except Exception as exc:  # noqa: BLE001
            log.warning("kooora fallback failed for %s: %s", date, exc)

    db.finish_run(run_id, "ok",
                  competitions_found=n_comps,
                  matches_found=len(rows),
                  matches_stored=len(rows))
    log.info("%s stored %d matches (%d enriched with Arabic names)",
             date, len(rows), n_arabic)
    return len(rows)


# ---------------------------------------------------------------------------
# step 3: match details (bilingual)
# ---------------------------------------------------------------------------
def enrich_match(db: Database, match_id: str, slug_en: Optional[str],
                 arabic: bool = True) -> bool:
    """Fetch one match's detail pages (EN + AR) and store everything."""
    try:
        if arabic:
            detail = goal_parser.fetch_match_detail_bilingual(match_id, slug=slug_en)
        else:
            detail = goal_parser.fetch_match_detail(match_id, lang="en", slug=slug_en)
    except Exception as exc:  # noqa: BLE001
        log.warning("detail fetch failed for %s: %s", match_id, exc)
        return False
    if not detail:
        log.debug("no detail payload for %s", match_id)
        return False
    db.apply_match_detail(detail)
    db.commit()
    return True


# ---------------------------------------------------------------------------
# step 3b: player profiles + career history (bilingual)
#
# Pulls /en/player/{slug}/{id} (full bio + career history) and merges Arabic
# names from /ar/اللاعب/{slug}/{id}. Players are discovered from lineups +
# events as matches get enriched, so this function is normally called as a
# second pass over already-stored matches.
# ---------------------------------------------------------------------------
def enrich_player(db: Database, player_id: str, slug_en: Optional[str] = None,
                  arabic: bool = True) -> bool:
    """Fetch one player's profile + career history and store it.

    Returns True on success. Slug is optional - goal.com's player page only
    cares about the ID (same convention as match pages).
    """
    try:
        if arabic:
            profile = goal_parser.fetch_player_bilingual(player_id, slug=slug_en)
        else:
            profile = goal_parser.fetch_player(player_id, lang="en", slug=slug_en)
    except Exception as exc:  # noqa: BLE001
        log.warning("player profile fetch failed for %s: %s", player_id, exc)
        return False
    if not profile:
        log.debug("no profile payload for player %s", player_id)
        return False
    db.apply_player_profile(profile)
    db.commit()
    return True


def player_ids_from_date(db: Database, date: str,
                         only_missing_profile: bool = True) -> List[str]:
    """Return the set of player IDs that appeared in matches on `date`.

    Sources: lineups, event scorers/assists/subs, managers (we keep managers
    out because they are not always players). When `only_missing_profile` is
    set, players whose profile was already fetched are skipped - this makes
    the bootstrap walk's player pass cheap on subsequent runs.
    """
    sql = """
        SELECT DISTINCT player_id FROM (
            SELECT player_id FROM lineups WHERE match_id IN
                (SELECT id FROM matches WHERE listed_date = %s)
            UNION
            SELECT player_id FROM match_events WHERE match_id IN
                (SELECT id FROM matches WHERE listed_date = %s)
        ) AS p
    """
    params: List[Any] = [date, date]
    if only_missing_profile:
        sql += " WHERE player_id IN (SELECT id FROM players WHERE profile_fetched_at IS NULL)"
    rows = db.conn.execute(sql, params).fetchall()
    return [r["player_id"] for r in rows if r["player_id"]]


def enrich_players_for_date(db: Database, date: str,
                            arabic: bool = True,
                            only_missing: bool = True,
                            max_players: Optional[int] = None,
                            batch_pause_sec: float = 0.0) -> int:
    """Fetch player profiles for every player seen in matches on `date`.

    Used by the bootstrap walk after the listing+detail pass. Returns the
    number of profiles fetched.
    """
    player_ids = player_ids_from_date(db, date, only_missing_profile=only_missing)
    if max_players:
        player_ids = player_ids[:max_players]
    done = 0
    for i, pid in enumerate(player_ids, 1):
        log.info("  player [%d/%d] %s", i, len(player_ids), pid[:14])
        if enrich_player(db, pid, slug_en=None, arabic=arabic):
            done += 1
        if batch_pause_sec and i % 10 == 0:
            time.sleep(batch_pause_sec)
    return done


# ---------------------------------------------------------------------------
# step 4: competition feature (standings + all rounds' matches)
# ---------------------------------------------------------------------------
def scrape_competition(db: Database, comp_id: str,
                       standings: bool = True, matches: bool = True) -> Dict[str, Any]:
    """Scrape one competition: table page (EN) + every round's matches (EN+AR).

    * Standings come from /en/{slug}/table/{id} (full table, English names).
      Cups have no table page -> has_standings=False is recorded instead.
    * Round matches come from the internal competition-matches API, both
      editions: EN (structure) + AR (Arabic team/venue/gameset names, slugs).
      One request per language returns EVERY gameset of the season.

    Returns a small summary dict (used by the CLI + API logs).
    """
    comp_row = db.conn.execute(
        "SELECT id, name_en, name_ar FROM competitions WHERE id = %s", (comp_id,)
    ).fetchone()
    if comp_row is None:
        raise ValueError(f"unknown competition id: {comp_id}")

    summary: Dict[str, Any] = {
        "competition_id": comp_id,
        "standings_rows": 0,
        "gamesets": 0,
        "matches_stored": 0,
        "has_standings": False,
    }

    # ---- standings (EN table page) -----------------------------------------
    season_id: Optional[str] = None
    if standings:
        table = competition_parser.fetch_standings(comp_id, comp_row["name_en"])
        if table:
            season_id = (table.get("active_season") or {}).get("id")
            for season in table.get("seasons", []):
                db.upsert_season({
                    "id": season["id"],
                    "competition_id": comp_id,
                    "name": season["name"],
                    "is_active": season.get("is_active", 0),
                })
            if table.get("competition", {}).get("id"):
                db.upsert_competition(table["competition"])
            db.replace_standings(comp_id, season_id, "total", table["tables"])
            db.replace_standings_markers(comp_id, season_id, table.get("markers") or [])
            summary["standings_rows"] = sum(len(t["rows"]) for t in table["tables"])
            summary["has_standings"] = True
            summary["markers"] = table.get("markers") or []
            db.commit()
            log.info("standings %s: %d rows (season %s)",
                     comp_id, summary["standings_rows"], season_id)
        else:
            log.info("standings %s: no table page (cup?)", comp_id)

    # ---- all rounds' matches (EN + AR) --------------------------------------
    if matches:
        en = competition_parser.fetch_competition_matches(comp_id, lang="en")
        ar = competition_parser.fetch_competition_matches(comp_id, lang="ar")

        gameset_meta: List[Dict[str, Any]] = []
        if en:
            gameset_meta = en.get("gamesets") or []
            for row in en.get("matches") or []:
                db.upsert_competition(row["competition"])
                db.upsert_team(row["home_team"])
                db.upsert_team(row["away_team"])
                db.upsert_match_from_listing(row, listed_date=(row.get("kickoff_utc") or "")[:10])
                summary["matches_stored"] += 1
        if ar:
            if not gameset_meta:
                gameset_meta = ar.get("gamesets") or []
            for row in ar.get("matches") or []:
                db.upsert_competition(row["competition"])
                db.upsert_team(row["home_team"])
                db.upsert_team(row["away_team"])
                db.upsert_match_from_listing(row, listed_date=(row.get("kickoff_utc") or "")[:10])
                db.update_match_venue_ar(row["match_id"], row.get("venue_name_ar"))

        # gamesets list (merge EN names + AR names by gameSetTypeId)
        ar_names = {g["game_set_type_id"]: g.get("name") for g in (ar or {}).get("gamesets") or []}
        merged: List[Dict[str, Any]] = []
        for g in gameset_meta:
            gst = g["game_set_type_id"]
            merged.append({
                "game_set_type_id": gst,
                "name_en": g.get("name"),
                "name_ar": ar_names.get(gst) or g.get("name"),
                "is_active": g.get("is_active", 0),
            })
        if merged:
            db.upsert_gamesets(comp_id, season_id, merged)
            summary["gamesets"] = len(merged)
        db.commit()
        log.info("competition matches %s: %d gamesets, %d matches",
                 comp_id, summary["gamesets"], summary["matches_stored"])

    db.mark_competition_scrape(
        comp_id, season_id,
        has_standings=summary["has_standings"],
        standings=bool(summary["has_standings"]) and standings,
        matches=matches,
    )
    db.commit()
    return summary


def scrape_competition_if_stale(db: Database, comp_id: str,
                                ttl_sec: int = 1800) -> Dict[str, Any]:
    """Re-scrape a competition when its data is missing or older than TTL."""
    row = db.get_competition_scrape(comp_id)
    now = datetime.now(timezone.utc)
    fresh = lambda ts: bool(ts) and (now - datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)).total_seconds() < ttl_sec  # noqa: E731

    need_standings = row is None or (row["has_standings"] and not fresh(row["standings_at"]))
    need_matches = row is None or not fresh(row["matches_at"])

    if not need_standings and not need_matches:
        return {"competition_id": comp_id, "fresh": True}

    return scrape_competition(db, comp_id, standings=need_standings, matches=need_matches)


def enrich_date(
    db: Database,
    date: str,
    competition_filter=None,
    statuses: Optional[List[str]] = None,
    only_missing: bool = True,
    max_details: Optional[int] = None,
    arabic: bool = True,
) -> int:
    """Fetch details for matches listed on `date` matching the filter."""
    statuses = statuses or list(config.DETAIL_WORTHY_STATUSES)
    run_id = db.start_run("details", date, "goal" + ("+ar" if arabic else ""))

    rows = db.conn.execute(
        """SELECT m.id, m.slug_en, m.status, c.name_en AS comp, c.name_ar AS comp_ar,
                  c.area_name_en AS area
           FROM matches m JOIN competitions c ON c.id = m.competition_id
           WHERE m.listed_date = %s AND m.status IN ({})""".format(
               ",".join(["%s"] * len(statuses))
           ),
        [date, *statuses],
    ).fetchall()

    todo = []
    for r in rows:
        if only_missing and db.conn.execute(
            "SELECT 1 FROM matches WHERE id = %s AND detail_fetched_at IS NOT NULL", (r["id"],)
        ).fetchone():
            continue
        if competition_filter is not None and not competition_filter(
            {"name_en": r["comp"], "name_ar": r["comp_ar"], "area_name_en": r["area"]}
        ):
            continue
        todo.append(r)

    if max_details:
        todo = todo[:max_details]

    done = 0
    for r in todo:
        log.info("  detail %s  [%s] %s", r["id"][:14], r["status"], r["comp"])
        if enrich_match(db, r["id"], r["slug_en"], arabic=arabic):
            done += 1

    db.finish_run(run_id, "ok", details_fetched=done)
    return done


# ---------------------------------------------------------------------------
# step 5: one-time historical bootstrap
#
# Walks every calendar day in [start_date, end_date] (inclusive), scraping
# listings in EN + AR for each day, optionally enriching finished matches of
# major competitions with lineups / events / stats.
#
# Design notes
#   * Resumable: a date that already has a successful `scrape_runs` row of
#     the matching mode ('date' for listings, 'details' for enrichment) is
#     skipped. Re-launching the run after an interrupt simply picks up where
#     it left off - no manual bookkeeping.
#   * Polite: when `slow=True` the rate-limiter is reconfigured for the
#     duration of the walk (delay 2.5s + 1.5s jitter) and an inter-day pause
#     is applied between days. The original rate-limit settings are always
#     restored, even if the walk crashes.
#   * One-time intent: historical listings are absolute (scores never change),
#     so a successful listing run for a past date is never re-scraped
#     automatically. Future fixtures inside the walk window will be picked up
#     and refreshed by the daily updater on subsequent runs.
# ---------------------------------------------------------------------------
def _walk_dates(start_iso: str, end_iso: str, direction: int = 1) -> Iterator[str]:
    """Yield every ISO date between start and end inclusive, oldest-first by
    default. `direction=-1` reverses the order (used for the past-window walk
    which the user wants to traverse 'today first, then back day by day')."""
    d0 = date_cls.fromisoformat(start_iso)
    d1 = date_cls.fromisoformat(end_iso)
    if direction < 0:
        # walk from end back to start: emit end_iso, end_iso-1day, ..., start_iso
        d0, d1 = d1, d0  # swap so d0 is the larger (newest) date
    cur = d0
    step = timedelta(days=direction)
    # yield while `cur` is still on the walk side of `d1`.
    #   direction=+1: yield while cur <= d1 (i.e. (cur-d1).days <= 0)
    #   direction=-1: yield while cur >= d1 (i.e. (cur-d1).days >= 0,
    #                 multiplied by -1 is <= 0)
    while (cur - d1).days * direction <= 0:
        yield cur.isoformat()
        cur += step


@contextlib.contextmanager
def _slow_rate_limit(enabled: bool):
    """Temporarily swap config.RATE_LIMIT_DELAY/JITTER for slow mode."""
    if not enabled:
        yield
        return
    orig_delay = config.RATE_LIMIT_DELAY
    orig_jitter = config.RATE_LIMIT_JITTER
    config.RATE_LIMIT_DELAY = config.SLOW_RATE_LIMIT_DELAY
    config.RATE_LIMIT_JITTER = config.SLOW_RATE_LIMIT_JITTER
    try:
        yield
    finally:
        config.RATE_LIMIT_DELAY = orig_delay
        config.RATE_LIMIT_JITTER = orig_jitter


def bootstrap_historical(
    db: Database,
    *,
    years_back: int = config.BOOTSTRAP_DEFAULT_YEARS_BACK,
    days_ahead: int = config.BOOTSTRAP_DEFAULT_DAYS_AHEAD,
    today_iso: Optional[str] = None,
    details: bool = True,
    players: bool = True,
    slow: bool = True,
    arabic: bool = True,
    kooora: bool = False,
    competition_filter=None,
    max_details_per_day: Optional[int] = None,
    max_players_per_day: Optional[int] = None,
    day_pause_sec: Optional[float] = None,
    progress_log: Optional[str] = None,
) -> Dict[str, Any]:
    """One-time, slow, polite walk of the last `years_back` years + the next
    `days_ahead` days.

    Returns a summary dict (listings_done, listings_skipped, details_done,
    details_skipped, players_done, players_skipped, errors, days_planned,
    elapsed_sec).

    The walk is fully resumable: dates with a prior successful scrape_runs row
    of the matching mode are skipped, so an interrupted run simply picks up
    where it left off when re-launched.

    Player enrichment (`players=True`) runs as a third pass per day, after
    listings and match details. It fetches the profile + career history of
    every player that appeared in a stored match on that date and whose
    `players.profile_fetched_at` is still NULL. Skipping players whose
    profile is already fetched makes the second+ run of the walk cheap.
    """
    today = date_cls.fromisoformat(today_iso) if today_iso else date_cls.today()
    past_start = (today - timedelta(days=years_back * 365)).isoformat()
    past_end = (today - timedelta(days=1)).isoformat()
    future_start = today.isoformat()
    future_end = (today + timedelta(days=days_ahead)).isoformat()

    # Walk past window newest-first (today-1, today-2, ... today-10y) so the
    # most recent data lands in the database first; future window oldest-first
    # (today, today+1, ... today+1y).
    past_dates = list(_walk_dates(past_start, past_end, direction=-1))
    future_dates = list(_walk_dates(future_start, future_end, direction=1))
    plan = past_dates + future_dates

    if day_pause_sec is None:
        day_pause_sec = config.BOOTSTRAP_DAY_PAUSE_SEC if slow else 0.0
    if progress_log is None:
        progress_log = config.BOOTSTRAP_PROGRESS_LOG

    log.info(
        "bootstrap: %d past days (%s .. %s) + %d future days (%s .. %s); "
        "slow=%s details=%s players=%s",
        len(past_dates), past_start, past_end,
        len(future_dates), future_start, future_end,
        slow, details, players,
    )

    summary: Dict[str, Any] = {
        "listings_done": 0,
        "listings_skipped": 0,
        "details_done": 0,
        "details_skipped": 0,
        "players_done": 0,
        "players_skipped": 0,
        "errors": 0,
        "days_planned": len(plan),
        "elapsed_sec": 0.0,
    }

    started = time.time()

    def _append_progress_line(line: str) -> None:
        try:
            with open(progress_log, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            log.warning("could not write progress log %s: %s", progress_log, exc)

    with _slow_rate_limit(slow):
        for i, d in enumerate(plan, 1):
            # ---- listings (EN + AR) -------------------------------------
            if db.listing_done_for(d):
                summary["listings_skipped"] += 1
                log.info("[%d/%d] %s: listings already done - skipping", i, len(plan), d)
            else:
                try:
                    n = scrape_date_listings(db, d, arabic=arabic, kooora=kooora)
                    summary["listings_done"] += 1
                    _append_progress_line(
                        f"{d}\tlistings\tok\t{n}"
                    )
                except Exception as exc:  # noqa: BLE001
                    summary["errors"] += 1
                    log.error("[%d/%d] %s: listings failed: %s", i, len(plan), d, exc)
                    _append_progress_line(f"{d}\tlistings\terror\t{exc}")

            # ---- details (optional, EN + AR per match) ------------------
            if details:
                if db.details_done_for(d):
                    summary["details_skipped"] += 1
                    log.debug("[%d/%d] %s: details already done", i, len(plan), d)
                else:
                    try:
                        n = enrich_date(
                            db, d, competition_filter,
                            only_missing=True,
                            max_details=max_details_per_day,
                            arabic=arabic,
                        )
                        summary["details_done"] += 1
                        _append_progress_line(f"{d}\tdetails\tok\t{n}")
                    except Exception as exc:  # noqa: BLE001
                        summary["errors"] += 1
                        log.error("[%d/%d] %s: details failed: %s",
                                  i, len(plan), d, exc)
                        _append_progress_line(f"{d}\tdetails\terror\t{exc}")

            # ---- player profiles (optional, EN + AR per player) --------
            # Player enrichment does NOT consult scrape_runs because the same
            # player can appear on many dates; the `only_missing` flag on the
            # players table is what makes it cheap on a re-run. We do skip
            # dates that have no stored matches at all (no lineups, no events,
            # nothing to enrich).
            if players:
                try:
                    n = enrich_players_for_date(
                        db, d, arabic=arabic, only_missing=True,
                        max_players=max_players_per_day,
                    )
                    summary["players_done"] += n
                    if n:
                        _append_progress_line(f"{d}\tplayers\tok\t{n}")
                except Exception as exc:  # noqa: BLE001
                    summary["errors"] += 1
                    log.error("[%d/%d] %s: players failed: %s",
                              i, len(plan), d, exc)
                    _append_progress_line(f"{d}\tplayers\terror\t{exc}")

            # ---- polite inter-day pause ---------------------------------
            if day_pause_sec > 0 and i < len(plan):
                time.sleep(day_pause_sec)

    summary["elapsed_sec"] = round(time.time() - started, 1)
    log.info(
        "bootstrap done: %d listings scraped (%d skipped), %d details scraped (%d skipped), "
        "%d player profiles fetched, %d errors, %d/%d days, %.1fs elapsed",
        summary["listings_done"], summary["listings_skipped"],
        summary["details_done"], summary["details_skipped"],
        summary["players_done"],
        summary["errors"], summary["listings_done"] + summary["listings_skipped"],
        summary["days_planned"], summary["elapsed_sec"],
    )
    return summary
