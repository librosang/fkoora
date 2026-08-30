"""Command line interface for the football scraper.

Examples
--------
    # scrape a single day (goal.com EN + AR listings)
    python -m scraper.cli date 2026-08-26

    # single day + bilingual lineups/events/stats for played matches
    python -m scraper.cli date 2026-08-25 --details

    # backfill a date range (listings only - fast)
    python -m scraper.cli backfill --from 2025-08-01 --to 2026-05-31

    # backfill with details for the big competitions
    python -m scraper.cli backfill --from 2026-01-01 --to 2026-05-31 --details

    # backfill with details for EVERY competition (slow!)
    python -m scraper.cli backfill --from 2026-01-01 --to 2026-05-31 --details --all

    # ONE-TIME historical bootstrap (last 10 years back + 1 year forward),
    # slow + resumable. Re-run any time to resume from where it left off.
    # Pulls listings + match details + player profiles + career history.
    python -m scraper.cli bootstrap --years-back 10 --days-ahead 365 --slow --details

    # bootstrap detached (survives the shell) -> logs to bootstrap.log
    ./scripts/bootstrap_historical.sh

    # fetch a single player's profile + career history
    python -m scraper.cli players <player_id>

    # backfill every player seen so far whose profile is still missing
    python -m scraper.cli players --missing

    # upcoming fixtures for the next 14 days
    python -m scraper.cli upcoming --days 14

    # enrich already-stored matches that have no details yet
    python -m scraper.cli enrich --date 2026-08-26 --all

    # database overview
    python -m scraper.cli stats

    # launch the local web view (kooora-style UI, mobile-first)
    python -m scraper.cli serve --port 8765

    # launch the JSON API backend (used by the Next.js frontend)
    python -m scraper.cli api --port 8000

    # one-shot refresh for an external crontab (listings + live details)
    python -m scraper.cli refresh

The database is PostgreSQL; point it at a server with --db or the
FOOTBALL_DB_URL environment variable (default: a local `football` database).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date as date_cls, datetime, timedelta, timezone
from typing import List, Optional

from . import config
from .db import backend
from .db.database import Database
from .pipeline import default_filter, enrich_date, make_competition_filter, scrape_date_listings

log = logging.getLogger("scraper")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def valid_date(s: str) -> str:
    try:
        date_cls.fromisoformat(s)
        return s
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid date {s!r} - use YYYY-MM-DD")


def date_range(start: str, end: str):
    d0 = date_cls.fromisoformat(start)
    d1 = date_cls.fromisoformat(end)
    if d1 < d0:
        raise SystemExit("error: --to date is before --from date")
    while d0 <= d1:
        yield d0.isoformat()
        d0 += timedelta(days=1)


def build_filter(args) -> Optional:
    if getattr(args, "all", False):
        return None
    if getattr(args, "leagues", None):
        return make_competition_filter(args.leagues)
    return default_filter


def open_db(args) -> Database:
    url = getattr(args, "db", None) or None
    log.info("database: %s", backend.display_dsn(backend.resolve_dsn(url)))
    return Database(url)


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
def cmd_date(args) -> None:
    db = open_db(args)
    try:
        n = scrape_date_listings(db, args.date, arabic=not args.no_arabic,
                                 kooora=args.kooora)
        print(f"[{args.date}] {n} matches stored (goal.com EN + AR listings)")
        if args.details:
            comp_filter = build_filter(args)
            done = enrich_date(db, args.date, comp_filter,
                               only_missing=not args.refresh_details,
                               max_details=args.max_details,
                               arabic=not args.no_arabic)
            print(f"[{args.date}] {done} match details fetched (bilingual)")
    finally:
        db.close()


def cmd_backfill(args) -> None:
    db = open_db(args)
    comp_filter = build_filter(args)
    dates = list(date_range(args.date_from, args.date_to))
    print(f"backfilling {len(dates)} days ({dates[0]} .. {dates[-1]})")
    total = 0
    try:
        for i, d in enumerate(dates, 1):
            try:
                total += scrape_date_listings(db, d, arabic=not args.no_arabic,
                                              kooora=args.kooora)
            except Exception as exc:  # noqa: BLE001
                log.error("day %s failed: %s", d, exc)
                continue
            print(f"  [{i}/{len(dates)}] {d}: cumulative {total} matches", flush=True)
        if args.details:
            print("fetching match details (EN + AR per match) ...")
            done = 0
            for i, d in enumerate(dates, 1):
                n = enrich_date(db, d, comp_filter,
                                only_missing=not args.refresh_details,
                                max_details=args.max_details,
                                arabic=not args.no_arabic)
                done += n
                print(f"  [{i}/{len(dates)}] {d}: +{n} details (total {done})", flush=True)
    finally:
        db.close()


def cmd_bootstrap(args) -> None:
    """One-time, slow, polite historical bootstrap.

    Walks every day from `today - years_back` up to `today + days_ahead`,
    scraping EN + AR listings for each day, (optionally) enriching the
    major-competition matches with bilingual lineups/events/stats, and
    (optionally) fetching player profiles + career history for every player
    that appeared in a stored match on that date.

    Designed to be launched ONCE on a fresh database. Re-running it is safe
    and cheap: any date that already has a successful `scrape_runs` row of
    the matching mode is skipped, and players whose profile was already
    fetched are skipped too, so the walk simply resumes from where it left
    off.
    """
    from .pipeline import bootstrap_historical

    db = open_db(args)
    comp_filter = build_filter(args)
    try:
        progress_before = db.bootstrap_progress()
        print(
            f"bootstrap: prior progress  listings={progress_before.get('date', 0)} "
            f"details={progress_before.get('details', 0)}"
        )
        summary = bootstrap_historical(
            db,
            years_back=args.years_back,
            days_ahead=args.days_ahead,
            today_iso=args.today,
            details=args.details,
            players=not args.no_players,
            slow=not args.no_slow,
            arabic=not args.no_arabic,
            kooora=args.kooora,
            competition_filter=comp_filter,
            # add_filter_args() defaults max_details to None; for bootstrap we
            # want a sensible per-day cap so a single dense match day doesn't
            # dominate the walk. The user can override with --max-details.
            max_details_per_day=args.max_details if args.max_details is not None else 200,
            max_players_per_day=args.max_players,
            day_pause_sec=args.day_pause,
        )
        print("-" * 60)
        print("bootstrap summary")
        print("-" * 60)
        for k in ("days_planned", "listings_done", "listings_skipped",
                  "details_done", "details_skipped", "players_done",
                  "errors", "elapsed_sec"):
            print(f"  {k:<18} {summary[k]:>10}")
        print("-" * 60)
        print(f"progress log: {config.BOOTSTRAP_PROGRESS_LOG}")
        print("re-run the same command any time to resume from where it left off")
    finally:
        db.close()


def cmd_players(args) -> None:
    """Fetch player profiles + career history (bilingual).

    Examples:
      python -m scraper.cli players <player_id>          # one player
      python -m scraper.cli players --date 2026-08-25     # all players from one day
      python -m scraper.cli players --from 2026-08-01 --to 2026-08-31
      python -m scraper.cli players --missing             # every player in the DB
                                                          # whose profile is missing
    """
    from .pipeline import enrich_player, enrich_players_for_date, player_ids_from_date

    db = open_db(args)
    try:
        done = 0
        if args.player_id:
            ok = enrich_player(db, args.player_id, slug_en=None,
                               arabic=not args.no_arabic)
            done = 1 if ok else 0
            print(f"player {args.player_id}: {'ok' if ok else 'failed'}")
        elif args.missing:
            # Players that have been seen (via lineups/events) but never
            # had their profile fetched. Backfill them in one pass.
            rows = db.conn.execute(
                "SELECT id FROM players WHERE profile_fetched_at IS NULL ORDER BY id"
            ).fetchall()
            print(f"backfilling {len(rows)} players without profile ...")
            for i, r in enumerate(rows, 1):
                ok = enrich_player(db, r["id"], slug_en=None,
                                   arabic=not args.no_arabic)
                if ok:
                    done += 1
                if i % 25 == 0:
                    print(f"  [{i}/{len(rows)}] cumulative {done}", flush=True)
        elif args.date:
            n = enrich_players_for_date(db, args.date,
                                         arabic=not args.no_arabic,
                                         only_missing=not args.refresh)
            print(f"{args.date}: {n} player profiles fetched")
            done = n
        elif args.date_from and args.date_to:
            dates = list(date_range(args.date_from, args.date_to))
            print(f"players for {len(dates)} days ({dates[0]} .. {dates[-1]})")
            for i, d in enumerate(dates, 1):
                n = enrich_players_for_date(db, d, arabic=not args.no_arabic,
                                            only_missing=not args.refresh)
                done += n
                print(f"  [{i}/{len(dates)}] {d}: +{n} (total {done})", flush=True)
        else:
            raise SystemExit(
                "error: pass a player id, --date, --from/--to, or --missing"
            )
        print(f"enriched {done} players")
    finally:
        db.close()


def cmd_upcoming(args) -> None:
    db = open_db(args)
    today = date_cls.today()
    dates = [(today + timedelta(days=i)).isoformat() for i in range(1, args.days + 1)]
    total = 0
    try:
        for d in dates:
            try:
                total += scrape_date_listings(db, d, arabic=not args.no_arabic,
                                              kooora=args.kooora)
            except Exception as exc:  # noqa: BLE001
                log.error("day %s failed: %s", d, exc)
        print(f"upcoming: {total} fixtures stored across {len(dates)} days")
    finally:
        db.close()


def cmd_enrich(args) -> None:
    db = open_db(args)
    comp_filter = build_filter(args)
    try:
        if args.date:
            dates = [args.date]
        elif args.date_from and args.date_to:
            dates = list(date_range(args.date_from, args.date_to))
        else:
            # every listed_date that has matches without details
            rows = db.conn.execute(
                """SELECT DISTINCT listed_date FROM matches
                   WHERE listed_date IS NOT NULL AND detail_fetched_at IS NULL
                   ORDER BY listed_date"""
            ).fetchall()
            dates = [r["listed_date"] for r in rows]
        done = 0
        for d in dates:
            done += enrich_date(db, d, comp_filter,
                                only_missing=not args.refresh_details,
                                max_details=args.max_details,
                                arabic=not args.no_arabic)
            print(f"  {d}: cumulative {done} details", flush=True)
        print(f"enriched {done} matches")
    finally:
        db.close()


def cmd_stats(args) -> None:
    db = open_db(args)
    try:
        s = db.stats()
        width = max(len(k) for k in s)
        print("database overview")
        print("-" * (width + 12))
        for k, v in s.items():
            print(f"  {k:<{width}}  {v:>10,}")
    finally:
        db.close()


def cmd_show(args) -> None:
    db = open_db(args)
    try:
        rows = db.conn.execute(
            """SELECT m.match_date, m.kickoff_utc, m.status,
                      c.name_en AS comp_en, c.name_ar AS comp_ar,
                      th.name_en AS home_en, th.name_ar AS home_ar,
                      ta.name_en AS away_en, ta.name_ar AS away_ar,
                      m.home_score, m.away_score, v.name_en AS venue
               FROM matches m
               JOIN competitions c ON c.id = m.competition_id
               JOIN teams th ON th.id = m.home_team_id
               JOIN teams ta ON ta.id = m.away_team_id
               LEFT JOIN venues v ON v.id = m.venue_id
               WHERE m.match_date = %s
               ORDER BY m.kickoff_utc""",
            (args.date,),
        ).fetchall()
        if not rows:
            print(f"no matches found for {args.date} - scrape it first:")
            print(f"  python -m scraper.cli date {args.date}")
            return
        print(f"matches on {args.date} ({len(rows)})")
        print("-" * 100)
        for r in rows:
            score = f"{r['home_score']}-{r['away_score']}" if r["home_score"] is not None else "  vs  "
            print(f"{r['kickoff_utc'][11:16]}  {score}  {r['status']:<8} "
                  f"{(r['comp_en'] or '?')[:28]:<28} {r['home_en']} vs {r['away_en']}")
            if args.arabic:
                print(f"{'':14}AR: {r['comp_ar']} | {r['home_ar']} ضد {r['away_ar']}")
    finally:
        db.close()


def cmd_serve(args) -> None:
    from .webapp import run as run_webapp

    run_webapp(host=args.host, port=args.port,
               db_url=getattr(args, "db", None), debug=args.debug)


def cmd_api(args) -> None:
    from . import api as api_module

    api_module.run(host=args.host, port=args.port,
                   db_url=getattr(args, "db", None),
                   schedule=not args.no_schedule, debug=args.debug)


def cmd_refresh(args) -> None:
    """One-shot scrape run - the body of an external crontab entry."""
    from .pipeline import enrich_date, enrich_match, scrape_date_listings

    today = date_cls.today().isoformat()
    dates = [(date_cls.today() + timedelta(days=i)).isoformat()
             for i in range(-1, args.days + 1)]
    db = open_db(args)
    try:
        for d in dates:
            try:
                scrape_date_listings(db, d, arabic=not args.no_arabic, kooora=args.kooora)
            except Exception as exc:  # noqa: BLE001
                log.error("day %s failed: %s", d, exc)
        # live matches: refresh details so dialogs stay current
        if args.details:
            cutoff = (datetime.now(timezone.utc)
                      - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            for r in db.conn.execute(
                """SELECT id, slug_en FROM matches WHERE status = 'LIVE'
                     AND kickoff_utc >= %s ORDER BY kickoff_utc""",
                (cutoff,),
            ).fetchall():
                enrich_match(db, r["id"], r["slug_en"], arabic=not args.no_arabic)
            # and slowly fill in anything finished that is still missing details
            for d in dates:
                enrich_date(db, d, None, only_missing=True,
                            max_details=args.max_details, arabic=not args.no_arabic)
        print(f"refreshed {len(dates)} days (today {today}, +/- {args.days})")
    finally:
        db.close()


def cmd_cache_images(args) -> None:
    from .api import warm_image_cache

    print(f"pre-downloading crests/logos for matches of the last {args.days} days ...")
    ok, fail = warm_image_cache(getattr(args, "db", None), days=args.days,
                                workers=args.workers)
    print(f"cached {ok} images ({fail} failed)")


def cmd_cache_crests(args) -> None:
    from .webapp import cache_all_crests

    print("downloading team crests + competition logos (one-time cache) ...")
    ok, fail = cache_all_crests(getattr(args, "db", None))
    print(f"cached {ok} images ({fail} failed)")


def cmd_standings(args) -> None:
    """Scrape standings + all rounds' matches for one or more competitions."""
    from .pipeline import scrape_competition

    db = open_db(args)
    try:
        comp_ids: List[str] = list(getattr(args, "ids", None) or [])
        if args.major:
            from .major import is_major_competition
            for r in db.conn.execute(
                """SELECT c.id, c.name_en, c.area_name_en, COUNT(*) AS n
                   FROM competitions c JOIN matches m ON m.competition_id = c.id
                   GROUP BY c.id ORDER BY n DESC"""
            ).fetchall():
                if is_major_competition({"name_en": r["name_en"],
                                         "area_name_en": r["area_name_en"]}):
                    if r["id"] not in comp_ids:
                        comp_ids.append(r["id"])
        if not comp_ids:
            raise SystemExit("error: pass competition ids and/or --major")

        for cid in comp_ids:
            try:
                summary = scrape_competition(db, cid)
                print(f"{cid}: {summary['standings_rows']} standings rows, "
                      f"{summary['gamesets']} rounds, "
                      f"{summary['matches_stored']} matches stored"
                      f"{'' if summary['has_standings'] else ' (no table - cup)'}")
            except Exception as exc:  # noqa: BLE001
                log.error("competition %s failed: %s", cid, exc)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------
def add_filter_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--all", action="store_true",
                   help="include every competition (default: top leagues + cups)")
    p.add_argument("--leagues", nargs="*", default=None,
                   help="custom competition substrings; add @area to disambiguate, "
                        "e.g. --leagues 'premier league@england' 'serie a@italy'")
    p.add_argument("--max-details", type=int, default=None,
                   help="cap the number of detail pages per day (testing)")
    p.add_argument("--refresh-details", action="store_true",
                   help="re-fetch details even if already fetched")


def add_source_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--no-arabic", action="store_true",
                   help="skip goal.com Arabic pages (English names only)")
    p.add_argument("--kooora", action="store_true",
                   help="additionally merge kooora.com as an Arabic fallback")
    p.add_argument("--db", default=None, help="PostgreSQL URL (default: FOOTBALL_DB_URL env)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scraper",
        description="Bilingual (EN/AR) football scraper: goal.com EN + AR -> PostgreSQL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Examples")[1] if "Examples" in __doc__ else None,
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    # date ---------------------------------------------------------------
    p = sub.add_parser("date", help="scrape one day (goal.com EN + AR)")
    p.add_argument("date", type=valid_date)
    p.add_argument("--details", action="store_true", help="also fetch lineups/events/stats")
    add_source_args(p)
    add_filter_args(p)
    p.set_defaults(func=cmd_date)

    # backfill -----------------------------------------------------------
    p = sub.add_parser("backfill", help="scrape a historical date range")
    p.add_argument("--from", dest="date_from", required=True, type=valid_date)
    p.add_argument("--to", dest="date_to", required=True, type=valid_date)
    p.add_argument("--details", action="store_true", help="also fetch match details")
    add_source_args(p)
    add_filter_args(p)
    p.set_defaults(func=cmd_backfill)

    # bootstrap ----------------------------------------------------------
    p = sub.add_parser(
        "bootstrap",
        help="ONE-TIME slow historical walk (last N years + next M days)",
        description=(
            "One-time, polite, slow historical bootstrap. Walks every calendar day "
            "from today-years_back up to today+days_ahead, scraping EN + AR listings "
            "for each day and (with --details) enriching the major-competition "
            "matches with bilingual lineups/events/stats. Resumable: dates that "
            "already have a successful scrape_runs row of the matching mode are "
            "skipped, so an interrupted run can simply be re-launched."
        ),
    )
    p.add_argument("--years-back", type=int,
                   default=config.BOOTSTRAP_DEFAULT_YEARS_BACK,
                   help=f"how many years into the past to walk "
                        f"(default {config.BOOTSTRAP_DEFAULT_YEARS_BACK})")
    p.add_argument("--days-ahead", type=int,
                   default=config.BOOTSTRAP_DEFAULT_DAYS_AHEAD,
                   help=f"how many days into the future to walk "
                        f"(default {config.BOOTSTRAP_DEFAULT_DAYS_AHEAD})")
    p.add_argument("--today", type=valid_date, default=None,
                   help="anchor date for the walk (default: today in local time)")
    p.add_argument("--details", action="store_true", default=True,
                   help="also enrich match details for major competitions (default on)")
    p.add_argument("--no-details", dest="details", action="store_false",
                   help="listings only - skip the detail enrichment pass")
    p.add_argument("--no-players", action="store_true",
                   help="skip the player profile + career history pass (default on)")
    p.add_argument("--max-players", type=int, default=None,
                   help="cap player profiles per day (default: unlimited)")
    p.add_argument("--no-slow", action="store_true",
                   help="use the normal rate-limit profile instead of the slow one "
                        "(faster but less polite - not recommended for the first run)")
    p.add_argument("--day-pause", type=float, default=None,
                   help=f"seconds to sleep between days (default: "
                        f"{config.BOOTSTRAP_DAY_PAUSE_SEC} in slow mode, 0 otherwise)")
    # --max-details and --refresh-details come from add_filter_args() below;
    # we keep the per-day cap at 200 (the filter default) so the first walk
    # doesn't try to fetch every detail of every historical day.
    add_source_args(p)
    add_filter_args(p)
    p.set_defaults(func=cmd_bootstrap)

    # upcoming -----------------------------------------------------------
    p = sub.add_parser("upcoming", help="scrape future fixtures")
    p.add_argument("--days", type=int, default=config.DEFAULT_UPCOMING_DAYS,
                   help=f"days ahead to look (default {config.DEFAULT_UPCOMING_DAYS})")
    add_source_args(p)
    p.set_defaults(func=cmd_upcoming)

    # enrich -------------------------------------------------------------
    p = sub.add_parser("enrich", help="fetch details for already-stored matches")
    p.add_argument("--date", type=valid_date, default=None)
    p.add_argument("--from", dest="date_from", type=valid_date, default=None)
    p.add_argument("--to", dest="date_to", type=valid_date, default=None)
    add_source_args(p)
    add_filter_args(p)
    p.set_defaults(func=cmd_enrich)

    # players ------------------------------------------------------------
    p = sub.add_parser(
        "players",
        help="fetch player profiles + career history (bilingual)",
        description=(
            "Fetch the goal.com player profile page (bio + career history) for "
            "one player, every player that appeared in matches on a date, a date "
            "range, or every player in the DB whose profile is still missing. "
            "Idempotent: re-running simply refreshes the player's bio + career."
        ),
    )
    p.add_argument("player_id", nargs="?", default=None,
                   help="single sportfeeds player ID to fetch")
    p.add_argument("--date", type=valid_date, default=None,
                   help="fetch profiles for every player seen on this date")
    p.add_argument("--from", dest="date_from", type=valid_date, default=None)
    p.add_argument("--to", dest="date_to", type=valid_date, default=None)
    p.add_argument("--missing", action="store_true",
                   help="backfill every player in the DB whose profile_fetched_at is NULL")
    p.add_argument("--refresh", action="store_true",
                   help="re-fetch profiles even if already fetched")
    p.add_argument("--db", default=None, help="PostgreSQL URL (default: FOOTBALL_DB_URL env)")
    p.add_argument("--no-arabic", action="store_true",
                   help="skip goal.com Arabic pages (English names only)")
    p.set_defaults(func=cmd_players)

    # show ---------------------------------------------------------------
    p = sub.add_parser("show", help="print matches stored for a date")
    p.add_argument("date", type=valid_date)
    p.add_argument("--arabic", action="store_true", help="also print Arabic names")
    p.add_argument("--db", default=None, help="PostgreSQL URL (default: FOOTBALL_DB_URL env)")
    p.set_defaults(func=cmd_show)

    # stats --------------------------------------------------------------
    p = sub.add_parser("stats", help="database row counts")
    p.add_argument("--db", default=None, help="PostgreSQL URL (default: FOOTBALL_DB_URL env)")
    p.set_defaults(func=cmd_stats)

    # serve ---------------------------------------------------------------
    p = sub.add_parser("serve", help="launch the local web view (kooora-style)")
    p.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=8765, help="port (default 8765)")
    p.add_argument("--debug", action="store_true", help="Flask debug/reload mode")
    p.add_argument("--db", default=None, help="PostgreSQL URL (default: FOOTBALL_DB_URL env)")
    p.set_defaults(func=cmd_serve)

    # api -----------------------------------------------------------------
    p = sub.add_parser("api", help="launch the JSON API backend (frontend data source)")
    p.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    p.add_argument("--port", type=int, default=9000, help="port (default 8000)")
    p.add_argument("--no-schedule", action="store_true",
                   help="disable the built-in scheduler (use an external crontab + 'refresh')")
    p.add_argument("--debug", action="store_true", help="debug logging")
    p.add_argument("--db", default=None, help="PostgreSQL URL (default: FOOTBALL_DB_URL env)")
    p.set_defaults(func=cmd_api)

    # refresh ---------------------------------------------------------------
    p = sub.add_parser("refresh",
                       help="one-shot scrape run (for external crontabs)")
    p.add_argument("--days", type=int, default=1,
                   help="days ahead to refresh besides yesterday+today (default 1)")
    p.add_argument("--details", action="store_true", default=True,
                   help="also refresh live/backfill details (default on)")
    p.add_argument("--max-details", type=int, default=200,
                   help="cap per-day backfilled details (default 200)")
    add_source_args(p)
    p.set_defaults(func=cmd_refresh)

    # cache-images ---------------------------------------------------------
    p = sub.add_parser("cache-images",
                       help="pre-download all recent crests/logos for the API image proxy")
    p.add_argument("--days", type=int, default=10,
                   help="warm images referenced by matches from the last N days (default 10)")
    p.add_argument("--workers", type=int, default=8, help="parallel downloads (default 8)")
    p.add_argument("--db", default=None, help="PostgreSQL URL (default: FOOTBALL_DB_URL env)")
    p.set_defaults(func=cmd_cache_images)

    # cache-crests ---------------------------------------------------------
    p = sub.add_parser("cache-crests", help="pre-download all crests/logos for the web view")
    p.add_argument("--db", default=None, help="PostgreSQL URL (default: FOOTBALL_DB_URL env)")
    p.set_defaults(func=cmd_cache_crests)

    # standings ------------------------------------------------------------
    p = sub.add_parser("standings",
                       help="scrape standings + all rounds for competitions")
    p.add_argument("ids", nargs="*", help="competition ids (sportfeeds IDs from the DB)")
    p.add_argument("--major", action="store_true",
                   help="scrape every major competition present in the database")
    p.add_argument("--db", default=None, help="PostgreSQL URL (default: FOOTBALL_DB_URL env)")
    p.set_defaults(func=cmd_standings)

    return parser


def main(argv: Optional[List[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted - progress so far is saved")
        sys.exit(130)


if __name__ == "__main__":
    main()
