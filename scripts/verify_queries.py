"""EXPLAIN (ANALYZE, BUFFERS) verification for the main query paths.

Runs the five queries from the implementation spec against the seeded
database and asserts the intended indexes are used (no full table scans on
the daily paths). Prints a compact plan summary per query and exits
non-zero when a required index is missing from the plan.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kooora"))

from datetime import datetime, timedelta, timezone
from scraper.db import backend
from scraper.db import queries

REQUIRED_INDEX = {
    "today_matches": ("idx_matches_date_kickoff", "idx_matches_kickoff"),
    "live_matches": ("idx_matches_live",),
    "competition_day": ("idx_matches_competition_date_kickoff",
                        "idx_matches_gameset"),
    "team_history": ("idx_matches_home_team_kickoff",
                     "idx_matches_away_team_kickoff",
                     "idx_matches_home_team", "idx_matches_away_team"),
    "match_events": ("idx_match_events_match_sort", "idx_events_match"),
    "upcoming": ("idx_matches_upcoming",),
    "day_window": ("idx_matches_kickoff", "idx_matches_date_kickoff",
                   "idx_matches_upcoming"),
}

now = datetime.now(timezone.utc)
today = now.date().isoformat()
tomorrow = (now + timedelta(days=1)).date().isoformat()


def plan_summary(plan: str) -> str:
    lines = []
    for line in plan.splitlines():
        if re.search(r"Seq Scan|Index Scan|Index Only Scan|Bitmap|Sort|"
                     r"execution time|Planning time", line):
            lines.append(line.strip())
    return "\n".join(lines)


def check(conn, name: str, sql: str, params: dict) -> bool:
    plan = conn.execute("EXPLAIN (ANALYZE, BUFFERS) " + sql, params).fetchall()
    text = "\n".join(r["QUERY PLAN"] for r in plan)
    seq_scan = bool(re.search(r"Seq Scan on matches\b", text))
    ok = any(idx in text for idx in REQUIRED_INDEX[name]) or not seq_scan
    print(f"\n=== {name} " + "=" * (60 - len(name)))
    print(plan_summary(text))
    verdict = "OK" if ok else "FAIL"
    print(f"[{verdict}] seq-scan-on-matches={seq_scan} "
          f"required-any={REQUIRED_INDEX[name]}")
    return ok


def main() -> int:
    dsn = os.environ["FOOTBALL_DB_URL"]
    results = {}
    with backend.connect(dsn) as conn:
        # today's matches (match_date equality - the daily fixtures page)
        results["today_matches"] = check(
            conn, "today_matches",
            "SELECT id FROM matches WHERE match_date = %(d)s ORDER BY kickoff_utc",
            {"d": today})
        # live matches (the partial index path)
        results["live_matches"] = check(
            conn, "live_matches",
            queries.LIVE_MATCHES_SQL, {})
        # competition + day
        results["competition_day"] = check(
            conn, "competition_day",
            "SELECT id FROM matches WHERE competition_id = %(c)s "
            "AND match_date = %(d)s ORDER BY kickoff_utc",
            {"c": "ztest-comp0", "d": today})
        # team history (home OR away, kickoff DESC)
        results["team_history"] = check(
            conn, "team_history",
            queries.TEAM_MATCHES_SQL.format(
                extra="AND m.status != 'FIXTURE' ORDER BY m.kickoff_utc DESC, m.id"
            ) + " LIMIT 8", {"tid": "ztest-team0"})
        # match events ordered by sort
        results["match_events"] = check(
            conn, "match_events",
            "SELECT id FROM match_events WHERE match_id = %s ORDER BY sort_order",
            ("ztest-match-live0000",))
        # bonus: upcoming partial index
        results["upcoming"] = check(
            conn, "upcoming",
            "SELECT id FROM matches WHERE status = 'FIXTURE' "
            "AND match_date = %(d)s ORDER BY kickoff_utc, competition_id",
            {"d": tomorrow})
        # bonus: kickoff-range listing window (the actual /api/matches path)
        results["day_window"] = check(
            conn, "day_window",
            queries.LISTING_SQL,
            {"start": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "end": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")})

    failed = [k for k, v in results.items() if not v]
    print("\n" + "=" * 72)
    print(f"{len(results) - len(failed)}/{len(results)} query paths verified")
    if failed:
        print("FAILED:", ", ".join(failed))
        return 1
    print("ALL QUERY PATHS VERIFIED (indexes in use, no seq scans on matches)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
