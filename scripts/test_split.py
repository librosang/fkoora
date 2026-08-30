#!/usr/bin/env python3
"""Tests for the api/scraper split.

Two suites, both running WITHOUT PostgreSQL:

1. API suite  - Flask test client against create_app() with the build_*
   row-builders patched to canned payloads and a FakeConn standing in for
   every database connection. Verifies the ROUTE layer: pure-DB serving,
   refresh_jobs enqueueing on gaps, refreshing flags, view tracking.

2. Worker suite - process_job_queue() with the pipeline scrape functions
   patched to recorders. Verifies claim/execute/finish dispatch, attempt
   counting, and the event-driven competition refresh decisions.

Run:  python3 scripts/test_split.py
"""
from __future__ import annotations

import contextlib
import os
import re
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scraper.api as api
import scraper.jobs as jobs
import scraper.worker as worker

PASS = 0
FAIL = 0
FAILURES: List[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        FAILURES.append(f"{name} {detail}")
        print(f"  FAIL  {name} {detail}")


# ---------------------------------------------------------------------------
# fake database layer
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows or []

    def fetchone(self) -> Optional[Dict[str, Any]]:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> List[Dict[str, Any]]:
        return self._rows


class FakeConn:
    """Routes execute() calls to canned handlers keyed by regex on the SQL.

    Also RECORDS every statement (sql, params) so tests can assert what the
    API wrote (enqueue upserts, view tracking) and what the worker claimed.
    """

    def __init__(self, routes: List[Tuple[str, Any]]):
        self.routes = routes
        self.statements: List[Tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None):
        self.statements.append((sql, params))
        for pattern, handler in self.routes:
            if re.search(pattern, sql):
                rows = handler(params) if callable(handler) else handler
                return FakeCursor(list(rows or []))
        return FakeCursor([])

    def commit(self):
        pass

    def close(self):
        pass


# ---------------------------------------------------------------------------
# API suite
# ---------------------------------------------------------------------------
def run_api_suite() -> None:
    print("\n== API suite (read-only server + job enqueueing) ==")

    listing_state = {"total": 0}
    detail_payload: Dict[str, Any] = {}
    comp_scrape_rows: Dict[str, Dict[str, Any]] = {}
    conn_holder: Dict[str, FakeConn] = {}

    def default_routes():
        return [
            (r"DELETE FROM refresh_jobs", lambda p: []),  # no pending markers
            (r"FROM scrape_runs", lambda p: []),          # nothing scraped
            (r"FROM refresh_jobs", lambda p: [{"n": 0}]),
            (r"COUNT\(\*\) AS n", lambda p: [{"n": 0}]),
            (r"MAX\(last_seen_at2\)", lambda p: [{"v": None}]),
        ]

    def make_conn(routes=None) -> FakeConn:
        return FakeConn(routes or default_routes())

    # patch the row builders (their SQL is unchanged pre/post split)
    api.build_listing = (lambda conn, date, today, major_only, tz_min: {
        "date": date, "today": today,
        "totalMatches": listing_state["total"], "competitions": []})

    def fake_build_detail(conn, match_id):
        return detail_payload.get(match_id)

    def fake_build_competition(conn, comp_id):
        if comp_id == "comp-unknown-1":
            return None                         # competition row absent
        return {"competition": {"id": comp_id}, "standings": None,
                "gamesets": [], "generatedAt": "2026-08-30T00:00:00Z"}

    def fake_build_comp_matches(conn, comp_id, gameset):
        return None                             # unknown by default

    api.build_detail = fake_build_detail
    api.build_competition = fake_build_competition
    api.build_competition_matches = fake_build_comp_matches
    api.build_team = lambda conn, team_id: None

    def fake_build_player(conn, player_id):
        return detail_payload.get(f"player:{player_id}")

    api.build_player = fake_build_player
    api._comp_scrape_row = lambda conn, cid: comp_scrape_rows.get(cid)

    import scraper.db.backend as backend

    @contextlib.contextmanager
    def fake_connection(dsn=None, pooled=True):
        conn = conn_holder.pop("next", None) or make_conn()
        conn_holder["conn"] = conn
        yield conn

    backend.connection = fake_connection
    backend.run_script = lambda conn, script: None
    api.backend = backend

    app = api.create_app("postgresql://fake")
    client = app.test_client()

    def request_conn() -> FakeConn:
        return conn_holder["conn"]

    def enqueue_params() -> List[Tuple[str, ...]]:
        return [tuple(st[1][:2]) for st in request_conn().statements
                if "INSERT INTO refresh_jobs" in st[0]
                and isinstance(st[1], tuple)]

    def enqueue_tuple(kind: str, ref: str) -> Optional[Tuple]:
        for st in request_conn().statements:
            if "INSERT INTO refresh_jobs" in st[0] and isinstance(st[1], tuple) \
               and st[1][0] == kind and st[1][1] == ref:
                return st[1]
        return None

    # -- 1) empty day listing enqueues a day_listing job ----------------------
    listing_state["total"] = 0
    r = client.get("/api/matches?date=2026-08-15&today=2026-08-30&tz=60")
    check("empty day returns 200 listing", r.status_code == 200)
    check("empty day serves totalMatches=0", r.get_json()["totalMatches"] == 0)
    check("empty day enqueues day_listing job",
          enqueue_tuple("day_listing", "2026-08-15") is not None,
          f"got {enqueue_params()}")
    check("day_listing job carries tz payload",
          enqueue_tuple("day_listing", "2026-08-15") is not None
          and '"tz": 60' in (enqueue_tuple("day_listing", "2026-08-15")[2] or ""))

    # -- 2) recently-scraped empty day does NOT re-enqueue --------------------
    c = make_conn([(r"FROM scrape_runs",
                    lambda p: [{"target": d} for d in
                               (p[0] if isinstance(p, (list, tuple)) else [])]),
                   (r"FROM refresh_jobs", lambda p: [{"n": 0}]),
                   (r"COUNT\(\*\) AS n", lambda p: [{"n": 0}]),
                   (r"MAX\(last_seen_at2\)", lambda p: [{"v": None}])])
    conn_holder["next"] = c
    r = client.get("/api/matches?date=2026-08-15&today=2026-08-30&tz=0")
    check("genuinely-empty day (freshly scraped) does not enqueue",
          enqueue_tuple("day_listing", "2026-08-15") is None,
          f"got {enqueue_params()}")

    # -- 3) non-empty day never enqueues --------------------------------------
    listing_state["total"] = 3
    api._listing_cache.clear()          # drop the cached empty listing
    r = client.get("/api/matches?date=2026-08-15&today=2026-08-30")
    check("non-empty day serves 200", r.status_code == 200)
    check("non-empty day does not enqueue", not enqueue_params(),
          f"got {enqueue_params()}")

    # -- 4) missing match detail: enqueue + 404 --------------------------------
    listing_state["total"] = 0
    detail_payload.clear()
    r = client.get("/api/match/match-aaaa-1")
    check("missing match detail returns 404", r.status_code == 404)
    check("missing match enqueues match_detail job",
          enqueue_tuple("match_detail", "match-aaaa-1") is not None,
          f"got {enqueue_params()}")

    # -- 5) thin match detail: enqueue + refreshing flag ----------------------
    detail_payload["match-aaaa-1"] = {
        "matchId": "match-aaaa-1", "status": "RESULT", "events": [],
        "lineups": {"confirmed": False, "home": None, "away": None},
        "stats": [], "homeTeam": {"id": "h"}, "awayTeam": {"id": "a"},
        "competition": {"id": "c"}, "kickoffUtc": None,
        "homeScore": 1, "awayScore": 0,
    }
    r = client.get("/api/match/match-aaaa-1")
    check("thin match detail serves 200", r.status_code == 200)
    check("thin match detail carries refreshing=true",
          r.get_json().get("refreshing") is True)
    check("thin match enqueues match_detail job",
          enqueue_tuple("match_detail", "match-aaaa-1") is not None)

    # -- 6) full match detail: no enqueue --------------------------------------
    detail_payload["match-aaaa-1"] = {
        "matchId": "match-aaaa-1", "status": "RESULT", "events": [{"e": 1}],
        "lineups": {"confirmed": True, "home": {"id": "h"}, "away": None},
        "stats": [], "homeTeam": {"id": "h"}, "awayTeam": {"id": "a"},
        "competition": {"id": "c"}, "kickoffUtc": None,
        "homeScore": 1, "awayScore": 0,
    }
    r = client.get("/api/match/match-aaaa-1")
    check("full match detail serves 200", r.status_code == 200)
    check("full match detail has NO refreshing flag",
          "refreshing" not in (r.get_json() or {}))
    check("full match does not enqueue", not enqueue_params())

    # -- 7) competition: unknown comp -> discovery job + 404 -------------------
    r = client.get("/api/competition/comp-unknown-1")
    check("unknown competition returns 404", r.status_code == 404)
    check("unknown competition enqueues comp_discovery",
          enqueue_tuple("comp_discovery", "comp-unknown-1") is not None,
          f"got {enqueue_params()}")
    views = [st for st in request_conn().statements
             if "competition_views" in st[0]]
    check("competition open tracks a view", bool(views))

    # -- 8) competition: known row but never scraped -> refresh + refreshing ---
    comp_scrape_rows["comp-never-1"] = None     # no scrape row
    r = client.get("/api/competition/comp-never-1")
    check("never-scraped competition serves 200 + refreshing",
          r.status_code == 200 and r.get_json().get("refreshing") is True)
    check("never-scraped competition enqueues comp_refresh",
          enqueue_tuple("comp_refresh", "comp-never-1") is not None)

    # -- 9) competition: stale data -> refreshing + comp_refresh ---------------
    comp_scrape_rows["comp-stale-1"] = {
        "competition_id": "comp-stale-1", "has_standings": 1,
        "standings_at": "2026-08-01T00:00:00Z", "matches_at": "2026-08-01T00:00:00Z",
    }
    r = client.get("/api/competition/comp-stale-1")
    check("stale competition serves 200 + refreshing",
          r.status_code == 200 and r.get_json().get("refreshing") is True)
    check("stale competition enqueues comp_refresh",
          enqueue_tuple("comp_refresh", "comp-stale-1") is not None)

    # -- 10) competition: fresh data -> plain 200 -------------------------------
    fresh = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    comp_scrape_rows["comp-fresh-1"] = {
        "competition_id": "comp-fresh-1", "has_standings": 1,
        "standings_at": fresh, "matches_at": fresh,
    }
    r = client.get("/api/competition/comp-fresh-1")
    check("fresh competition serves 200 without refreshing",
          r.status_code == 200 and "refreshing" not in r.get_json())
    check("fresh competition does not enqueue", not enqueue_params())

    # -- 11) player stub: serve + enqueue, no 404 -------------------------------
    detail_payload["player:play-stub-1"] = {
        "player": {"id": "play-stub-1", "nameEn": "X"}, "career": [],
        "profileFetched": False,
    }
    r = client.get("/api/player/play-stub-1")
    check("stub player serves 200", r.status_code == 200)
    check("stub player keeps profileFetched=false",
          r.get_json().get("profileFetched") is False)
    check("stub player enqueues player_profile",
          enqueue_tuple("player_profile", "play-stub-1") is not None)

    # -- 12) profiled player: plain 200, no enqueue ------------------------------
    detail_payload["player:play-full-1"] = {
        "player": {"id": "play-full-1", "nameEn": "Y"}, "career": [{"c": 1}],
        "profileFetched": True,
    }
    r = client.get("/api/player/play-full-1")
    check("profiled player serves 200", r.status_code == 200)
    check("profiled player does not enqueue", not enqueue_params())

    # -- 13) unknown player: plain 404, no enqueue -------------------------------
    detail_payload.pop("player:play-stub-1", None)
    r = client.get("/api/player/play-nope-1")
    check("unknown player returns 404", r.status_code == 404)
    check("unknown player does not enqueue", not enqueue_params())

    # -- 14) cron refresh: enqueue only (the worker scrapes) ---------------------
    r = client.get("/api/cron/refresh")
    check("cron refresh returns {ok, triggered}",
          r.status_code == 200 and r.get_json().get("triggered") is True)
    cron_job = enqueue_tuple("cron_refresh", time.strftime(
        "%Y-%m-%d", time.gmtime()))
    check("cron refresh enqueues a cron_refresh job", cron_job is not None)
    check("cron_refresh job is forced (bypasses retry window)",
          cron_job is not None and cron_job[4] is True)

    # -- 15) health: read-only role info + pending jobs --------------------------
    r = client.get("/api/health")
    body = r.get_json() or {}
    check("health returns ok", r.status_code == 200 and body.get("ok") is True)
    check("health reports read-only role", "read-only" in body.get("role", ""))
    check("health reports pendingJobs", "pendingJobs" in body)
    check("health has no scheduler block", "scheduler" not in body)

    # -- 16) invalid ids still 400 ------------------------------------------------
    r = client.get("/api/match/xx")
    check("invalid match id returns 400", r.status_code == 400)
    r = client.get("/api/competition/bad!id")
    check("invalid competition id returns 400", r.status_code == 400)

    # -- 17) the API NEVER touches goal.com ---------------------------------------
    import scraper.pipeline as pipeline_probe
    orig_fns = {name: getattr(pipeline_probe, name) for name in
                ("scrape_date_listings", "enrich_match", "enrich_player",
                 "enrich_date", "scrape_competition",
                 "scrape_competition_if_stale")}
    import time as _t
    calls: List[str] = []
    for name in orig_fns:
        def make_rec(n):
            def _fn(*a, **k):
                calls.append(n)
                return orig_fns[n](*a, **k)
            return _fn
        setattr(pipeline_probe, name, make_rec(name))
    # hit every endpoint once
    client.get("/api/matches?date=2026-08-15&today=2026-08-30")
    client.get("/api/match/match-aaaa-1")
    client.get("/api/competition/comp-fresh-1")
    client.get("/api/competition/comp-stale-1")
    client.get("/api/player/play-full-1")
    client.get("/api/cron/refresh")
    check("no endpoint calls any pipeline scrape function",
          not calls, f"pipeline touched: {calls}")
    for name, fn in orig_fns.items():
        setattr(pipeline_probe, name, fn)


# ---------------------------------------------------------------------------
# worker suite
# ---------------------------------------------------------------------------
def run_worker_suite() -> None:
    print("\n== Worker suite (job queue + event refresh) ==")

    calls: List[str] = []
    claimed_rows: List[Dict[str, Any]] = []
    finish_statements: List[Tuple[str, Any]] = []
    marker_writes: List[Tuple[str, ...]] = []
    cleared_markers: List[Any] = []
    worker_conn_statements: List[Tuple[str, Any]] = []

    def worker_routes():
        return [
            (r"UPDATE refresh_jobs\s+SET attempts", lambda p: claimed_rows),
            (r"FROM competition_views", lambda p: []),
        ]

    def make_conn() -> FakeConn:
        c = FakeConn(worker_routes())
        orig_execute = c.execute

        def execute(sql, params=None):
            worker_conn_statements.append((sql, params))
            if "INSERT INTO refresh_jobs" in sql and isinstance(params, tuple) \
               and params and params[0] == "comp_pending":
                marker_writes.append(tuple(params[:2]))
            if "DELETE FROM refresh_jobs" in sql and params:
                cleared_markers.append(params)
            if "UPDATE refresh_jobs" in sql and "attempts = attempts + 1" not in sql:
                finish_statements.append((sql, params))
            return orig_execute(sql, params)

        c.execute = execute
        return c

    import scraper.db.backend as backend_mod

    @contextlib.contextmanager
    def fake_connection(dsn=None, pooled=True):
        yield make_conn()

    orig_connection = backend_mod.connection
    backend_mod.connection = fake_connection

    import scraper.pipeline as pipeline

    def rec(name):
        def _fn(*a, **k):
            calls.append(f"{name}:{a[1] if len(a) > 1 else (a[0] if a else '')}")
            return True
        return _fn

    orig_pipeline = {name: getattr(pipeline, name) for name in
                     ("scrape_date_listings", "enrich_match", "enrich_player",
                      "enrich_date", "scrape_competition",
                      "scrape_competition_if_stale")}
    for name in orig_pipeline:
        setattr(pipeline, name, rec(name))

    # fake Database: handlers query matches/players through db.conn
    class FakeDB:
        def __init__(self, _url=None):
            # the wrapped conn: records comp_pending marker writes AND
            # answers the handlers' lookup queries
            c = make_conn()
            c.routes = [
                (r"UPDATE refresh_jobs\s+SET attempts", lambda p: claimed_rows),
                (r"DELETE FROM refresh_jobs", lambda p: []),
                (r"SELECT id, slug_en FROM matches",
                 lambda p: [{"id": "m-live-1", "slug_en": "sl"}]),
                (r"SELECT slug_en FROM matches",
                 lambda p: [{"slug_en": "slug"}]),
                (r"SELECT profile_fetched_at FROM players",
                 lambda p: [{"profile_fetched_at": None}]),
                (r"SELECT status FROM matches", lambda p: [{"status": "LIVE"}]),
            ]
            self.conn = c
            self.newly_finished_comps = set()

        def close(self):
            pass

        def commit(self):
            pass

    orig_database = worker.Database
    worker.Database = FakeDB

    def fake_scrape_comp(comp_id, force):
        calls.append(f"_scrape_competition:{comp_id}/force={force}")

    orig_scrape_comp = worker._scrape_competition
    worker._scrape_competition = fake_scrape_comp

    kicked: List[Tuple[str, bool]] = []
    orig_kick = worker._kick_competition_refresh

    def fake_kick(comp_id, force=False):
        kicked.append((comp_id, force))
        return True

    worker._kick_competition_refresh = fake_kick

    try:
        # -- 1) empty queue: nothing happens ---------------------------------
        claimed_rows.clear()
        n = worker.process_job_queue()
        check("empty queue runs 0 jobs", n == 0)

        # -- 2) day_listing job dispatch -------------------------------------
        claimed_rows.clear(); calls.clear(); finish_statements.clear()
        claimed_rows.append({"id": 1, "kind": "day_listing", "ref": "2026-08-15",
                             "payload": '{"tz": 60}', "attempts": 1})
        worker.process_job_queue()
        check("day_listing job scrapes listing pages",
              any(c.startswith("scrape_date_listings") for c in calls),
              f"got {calls}")
        check("day_listing job backfills details",
              any(c.startswith("enrich_date") for c in calls))
        check("day_listing job marked done (error=NULL)",
              any("error = NULL" in st[0] for st in finish_statements),
              f"got {finish_statements}")

        # -- 3) match_detail job dispatch ------------------------------------
        claimed_rows.clear(); calls.clear(); finish_statements.clear()
        claimed_rows.append({"id": 2, "kind": "match_detail", "ref": "match-aaaa-1",
                             "payload": None, "attempts": 1})
        worker.process_job_queue()
        check("match_detail job enriches the match",
              any(c.startswith("enrich_match") for c in calls), f"got {calls}")
        check("match_detail job marked done",
              any("error = NULL" in st[0] for st in finish_statements))

        # -- 4) player_profile job dispatch ----------------------------------
        claimed_rows.clear(); calls.clear(); finish_statements.clear()
        claimed_rows.append({"id": 3, "kind": "player_profile", "ref": "play-stub-1",
                             "payload": None, "attempts": 1})
        worker.process_job_queue()
        check("player_profile job enriches the player",
              any(c.startswith("enrich_player") for c in calls), f"got {calls}")

        # -- 5) failing job: error recorded, stays pending under the cap ------
        claimed_rows.clear(); calls.clear(); finish_statements.clear()

        def boom(*a, **k):
            raise RuntimeError("goal.com down")

        pipeline.enrich_player = boom
        claimed_rows.append({"id": 4, "kind": "player_profile", "ref": "play-stub-2",
                             "payload": None, "attempts": 1})
        worker.process_job_queue()
        check("failing job records the error",
              any(st[1] and "goal.com down" in str(st[1])
                  for st in finish_statements), f"got {finish_statements}")
        check("failed retry stays pending (CASE WHEN attempts >= cap)",
              any("CASE WHEN attempts >= " in st[0] for st in finish_statements))
        pipeline.enrich_player = rec("enrich_player")

        # -- 6) comp jobs dispatch to the right scrape path -------------------
        claimed_rows.clear(); calls.clear(); finish_statements.clear()
        cleared_markers.clear()
        claimed_rows.append({"id": 5, "kind": "comp_refresh", "ref": "comp-ttl-01",
                             "payload": None, "attempts": 1})
        claimed_rows.append({"id": 6, "kind": "comp_discovery", "ref": "comp-new-01",
                             "payload": None, "attempts": 1})
        worker.process_job_queue()
        check("comp_refresh -> _scrape_competition(force=False)",
              "_scrape_competition:comp-ttl-01/force=False" in calls,
              f"got {calls}")
        check("comp_discovery -> _scrape_competition(force=True)",
              "_scrape_competition:comp-new-01/force=True" in calls)
        check("both comp jobs marked done",
              len([st for st in finish_statements if "error = NULL" in st[0]]) == 2,
              f"got {finish_statements}")

        # -- 7) cron_refresh job: -1/0/+1 listings + today enrich -------------
        claimed_rows.clear(); calls.clear(); finish_statements.clear()
        claimed_rows.append({"id": 7, "kind": "cron_refresh", "ref": "2026-08-30",
                             "payload": None, "attempts": 1})
        worker.process_job_queue()
        check("cron_refresh scrapes listings around the ref date",
              any(c.startswith("scrape_date_listings") for c in calls))
        check("cron_refresh enriches today",
              any(c.startswith("enrich_date") for c in calls))

        # -- 8) event-driven refresh: viewed league -> force refresh ----------
        db = FakeDB()
        db.newly_finished_comps = {"comp-viewed-1", "comp-cold-01"}
        orig_viewed = jobs.viewed_competitions
        jobs.viewed_competitions = lambda conn, max_age, limit=64: [
            ("comp-viewed-1", "2026-08-30T00:00:00Z")]
        kicked.clear(); marker_writes.clear()
        worker._note_finished_competitions(db)
        jobs.viewed_competitions = orig_viewed
        check("viewed league gets a forced refresh",
              ("comp-viewed-1", True) in kicked, f"got {kicked}")
        check("cold league gets a comp_pending marker instead",
              ("comp_pending", "comp-cold-01") in marker_writes,
              f"got {marker_writes}")
        check("drained the finished set", db.newly_finished_comps == set())

        # -- 9) debounce: same league finishing twice collapses ---------------
        db.newly_finished_comps = {"comp-viewed-1"}
        kicked.clear()
        worker._note_finished_competitions(db)
        check("debounced: no second refresh within the window",
              ("comp-viewed-1", True) not in kicked, f"got {kicked}")

        # -- 10) scheduler tick smoke: state machine advances ------------------
        # (pipeline recorders make the tick side-effect free; every interval
        #  fired once because the state dict starts at 0)
        calls.clear()
        state = {"listing": 0.0, "around": 0.0, "live": 0.0, "backfill": 0.0,
                 "comp_warm": 0.0, "ar_today": 0.0, "ar_around": 0.0,
                 "around_iv": 10.0}
        worker._scheduler_tick(time.time(), state)
        check("scheduler tick scrapes today's listing",
              any(c.startswith("scrape_date_listings") for c in calls))
        check("scheduler tick enriches live matches",
              any(c.startswith("enrich_match") for c in calls))
        check("scheduler tick runs the detail backfill",
              any(c.startswith("enrich_date") for c in calls))

    finally:
        backend_mod.connection = orig_connection
        worker.Database = orig_database
        worker._scrape_competition = orig_scrape_comp
        worker._kick_competition_refresh = orig_kick
        for name, fn in orig_pipeline.items():
            setattr(pipeline, name, fn)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    run_api_suite()
    run_worker_suite()
    print(f"\n{'=' * 60}\nPASS {PASS}  FAIL {FAIL}")
    if FAILURES:
        print("failures:")
        for f in FAILURES:
            print("  -", f)
        sys.exit(1)
    print("ALL GREEN")
