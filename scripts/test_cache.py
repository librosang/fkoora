#!/usr/bin/env python3
"""Tests for the shared Redis response cache (scraper/apicache.py).

Three suites, all running WITHOUT PostgreSQL and WITHOUT a real Redis:

1. apicache unit suite - key builders, put/get round trips, corrupt entries,
   the disabled mode, the ImportError mode, failure-driven degradation
   (down-until window + fast-fail), status(), and the invalidation helpers
   (incl. the +/-1 day listing patterns).

2. API suite - Flask test client against create_app() with the build_*
   row-builders patched (same pattern as scripts/test_split.py) and a
   FakeRedis installed via apicache._install(). Verifies: hits skip the SQL
   chain entirely, cached bodies are byte-identical, If-None-Match turns
   hits into 304s, per-endpoint TTLs, 404s are never cached, a dead Redis
   degrades to plain DB reads, and /api/health reports the cache status.

3. Worker suite - the invalidation hooks: every function that lands new
   data (listing scrape, competition scrape, match detail job, player
   profile job) must drop the matching cache keys.

Run:  python3 scripts/test_cache.py
"""
from __future__ import annotations

import contextlib
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scraper.api as api
import scraper.apicache as apicache
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
# FakeRedis: the surface apicache actually uses (get/set/delete/scan_iter/ping)
# ---------------------------------------------------------------------------
class FakeRedis:
    def __init__(self, fail: bool = False):
        self.store: Dict[str, str] = {}
        self.ttl: Dict[str, Optional[int]] = {}
        self.fail = fail          # every op raises like a dead connection

    def ping(self):
        if self.fail:
            raise ConnectionError("redis down")
        return True

    def get(self, key: str):
        if self.fail:
            raise ConnectionError("redis down")
        return self.store.get(key)

    def set(self, key: str, value: str, ex: Optional[int] = None):
        if self.fail:
            raise ConnectionError("redis down")
        self.store[key] = value
        self.ttl[key] = ex
        return True

    def delete(self, *keys: str):
        if self.fail:
            raise ConnectionError("redis down")
        n = 0
        for k in keys:
            if self.store.pop(k, None) is not None:
                self.ttl.pop(k, None)
                n += 1
        return n

    def scan_iter(self, match: Optional[str] = None, count: Optional[int] = None):
        if self.fail:
            raise ConnectionError("redis down")
        import fnmatch
        for k in list(self.store):
            if match is None or fnmatch.fnmatchcase(k, match):
                yield k


# ---------------------------------------------------------------------------
# fake database layer (same shape as scripts/test_split.py)
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows or []

    def fetchone(self) -> Optional[Dict[str, Any]]:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> List[Dict[str, Any]]:
        return self._rows


class FakeConn:
    def __init__(self, routes: List[Tuple[str, Any]]):
        self.routes = routes
        self.statements: List[Tuple[str, Any]] = []

    def execute(self, sql: str, params: Any = None):
        import re
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
# suite 1: apicache units
# ---------------------------------------------------------------------------
def run_apicache_suite() -> None:
    print("\n== apicache unit suite ==")

    # -- keys ----------------------------------------------------------------
    k = apicache.k_listing("2026-08-30", "2026-08-30", True, -60)
    check("listing key shape", k == f"{apicache.KEY_PREFIX}:listing:2026-08-30|2026-08-30|1|-60", k)
    check("match key shape", apicache.k_match("m1") == f"{apicache.KEY_PREFIX}:match:m1")
    check("comp key shape", apicache.k_competition("c1") == f"{apicache.KEY_PREFIX}:comp:c1")
    check("compmatch key shape (no gameset)",
          apicache.k_comp_matches("c1", None) == f"{apicache.KEY_PREFIX}:compmatch:c1:-")
    check("compmatch key shape (gameset)",
          apicache.k_comp_matches("c1", "g9") == f"{apicache.KEY_PREFIX}:compmatch:c1:g9")
    check("team/player key shapes",
          apicache.k_team("t1") == f"{apicache.KEY_PREFIX}:team:t1"
          and apicache.k_player("p1") == f"{apicache.KEY_PREFIX}:player:p1")

    # -- disabled mode (no REDIS_URL) ---------------------------------------
    apicache._reset()
    check("enabled() False without REDIS_URL", apicache.enabled() is False)
    check("get() is None when disabled", apicache.get("x") is None)
    apicache.put("x", '"e"', "{}", 30)          # must be a silent no-op
    check("put() no-ops when disabled", True)
    st = apicache.status()
    check("status() reports disabled",
          st == {"enabled": False, "connected": False}, str(st))

    # -- put/get round trip + corrupt entries + ttl guard --------------------
    fake = FakeRedis()
    apicache._install("redis://fake", fake)
    check("enabled() True after install", apicache.enabled() is True)
    check("get() on empty cache is a miss", apicache.get(apicache.k_match("m1")) is None)
    apicache.put(apicache.k_match("m1"), '"etag-1"', '{"a": 1}', 30)
    hit = apicache.get(apicache.k_match("m1"))
    check("put/get round trip returns (etag, body)",
          hit == ('"etag-1"', '{"a": 1}'), str(hit))
    check("stored TTL is the given one", fake.ttl[apicache.k_match("m1")] == 30)
    apicache.put(apicache.k_match("m2"), '"e"', "{}", 0)
    check("ttl=0 is not stored", apicache.k_match("m2") not in fake.store)
    apicache.put(apicache.k_match("m3"), '"e"', "{}", -5)
    check("negative ttl is not stored", apicache.k_match("m3") not in fake.store)

    fake.store[apicache.k_match("bad1")] = "not-json{"
    check("corrupt entry reads as a miss",
          apicache.get(apicache.k_match("bad1")) is None)
    fake.store[apicache.k_match("bad2")] = '{"no-e": 1}'
    check("entry without etag reads as a miss",
          apicache.get(apicache.k_match("bad2")) is None)

    # -- invalidation helpers --------------------------------------------------
    fake.store.clear(); fake.ttl.clear()
    for key in (apicache.k_match("m1"), apicache.k_player("p1"),
                apicache.k_competition("c1"),
                apicache.k_comp_matches("c1", "g1"),
                apicache.k_comp_matches("c1", "g2"),
                apicache.k_comp_matches("c9", "g1"),
                apicache.k_listing("2026-08-30", "2026-08-30", True, 0),
                apicache.k_listing("2026-08-29", "2026-08-30", True, 0),
                apicache.k_listing("2026-07-01", "2026-08-30", True, 0)):
        fake.store[key] = '{"e":"e","b":"{}"}'
    apicache.invalidate_match("m1")
    check("invalidate_match drops the match key",
          apicache.k_match("m1") not in fake.store)
    apicache.invalidate_player("p1")
    check("invalidate_player drops the player key",
          apicache.k_player("p1") not in fake.store)
    apicache.invalidate_competition("c1")
    check("invalidate_competition drops the comp key",
          apicache.k_competition("c1") not in fake.store)
    check("invalidate_competition drops ALL gameset keys of that comp",
          apicache.k_comp_matches("c1", "g1") not in fake.store
          and apicache.k_comp_matches("c1", "g2") not in fake.store)
    check("invalidate_competition keeps OTHER comps' gameset keys",
          apicache.k_comp_matches("c9", "g1") in fake.store)
    apicache.invalidate_day("2026-08-30")
    check("invalidate_day drops the day and BOTH neighbours",
          apicache.k_listing("2026-08-30", "2026-08-30", True, 0) not in fake.store
          and apicache.k_listing("2026-08-29", "2026-08-30", True, 0) not in fake.store
          and apicache.k_listing("2026-07-01", "2026-08-30", True, 0) in fake.store)
    apicache.invalidate_day("not-a-date")     # must not raise
    check("invalidate_day ignores malformed dates", True)

    # -- degradation: op failure -> down window -> fast-fail -> recovery ------
    apicache._install("redis://fake", FakeRedis(fail=True))
    t0 = time.time()
    check("failing GET degrades to None", apicache.get("k") is None)
    check("failure opens the down window",
          apicache._state["down_until"] > t0, str(apicache._state))
    n_ops = 0
    orig_call = apicache._call

    def counting_call(fn, default):
        nonlocal n_ops
        n_ops += 1
        return orig_call(fn, default)

    apicache._call = counting_call
    apicache.get("k")
    apicache.put("k", "e", "b", 10)
    apicache.invalidate_match("m")
    check("down window skips the client entirely (3 no-op calls)",
          n_ops == 3, f"n_ops={n_ops}")
    apicache._call = orig_call

    # recovery: expire the window, install a working client
    apicache._state["down_until"] = 0.0
    good = FakeRedis()
    apicache._install("redis://fake", good)
    apicache.put("k", '"e"', "b-body", 10)
    check("recovered client serves gets again", apicache.get("k") == ('"e"', "b-body"))
    st = apicache.status()
    check("status() reports enabled+connected",
          st.get("enabled") is True and st.get("connected") is True, str(st))

    # -- redis package missing (ImportError path) ------------------------------
    real_redis = sys.modules.pop("redis", None)
    sys.modules["redis"] = None                  # import redis -> ImportError
    try:
        apicache._reset()
        apicache._state["url"] = "redis://missing"
        got = apicache._client()
        check("missing redis package yields no client", got is None)
        check("missing package disables permanently",
              apicache._state["permanent"] is True)
        check("ops stay no-ops without raising",
              apicache.get("k") is None and apicache.status().get("enabled") is True)
    finally:
        if real_redis is not None:
            sys.modules["redis"] = real_redis
        else:
            sys.modules.pop("redis", None)
        apicache._reset()
        apicache._state["permanent"] = False


# ---------------------------------------------------------------------------
# suite 2: API endpoints through the cache
# ---------------------------------------------------------------------------
def run_api_suite() -> None:
    print("\n== API cache suite (Flask test client + FakeRedis) ==")

    fake = FakeRedis()
    apicache._install("redis://fake", fake)

    listing_calls = {"n": 0}
    detail_calls = {"n": 0}
    comp_calls = {"n": 0}
    compm_calls = {"n": 0}
    team_calls = {"n": 0}
    player_calls = {"n": 0}
    conn_holder: Dict[str, FakeConn] = {}

    payloads: Dict[str, Any] = {}

    def default_routes():
        return [
            (r"DELETE FROM refresh_jobs", lambda p: []),  # no pending markers
            (r"FROM scrape_runs", lambda p: []),
            (r"FROM refresh_jobs", lambda p: [{"n": 0}]),
            (r"COUNT\(\*\) AS n", lambda p: [{"n": 0}]),
            (r"MAX\(last_seen_at2\)", lambda p: [{"v": None}]),
        ]

    def make_conn(routes=None) -> FakeConn:
        return FakeConn(routes or default_routes())

    def fake_build_listing(conn, date, today, major_only, tz_min):
        listing_calls["n"] += 1
        return payloads.get("listing") or {
            "date": date, "today": today, "totalMatches": 3, "competitions": []}

    def fake_build_detail(conn, match_id):
        detail_calls["n"] += 1
        return payloads.get(f"match:{match_id}")

    def fake_build_competition(conn, comp_id):
        comp_calls["n"] += 1
        return payloads.get(f"comp:{comp_id}")

    def fake_build_comp_matches(conn, comp_id, gameset):
        compm_calls["n"] += 1
        return payloads.get(f"compm:{comp_id}:{gameset or '-'}")

    def fake_build_team(conn, team_id):
        team_calls["n"] += 1
        return payloads.get(f"team:{team_id}")

    def fake_build_player(conn, player_id):
        player_calls["n"] += 1
        return payloads.get(f"player:{player_id}")

    orig_builders = {name: getattr(api, name) for name in
                     ("build_listing", "build_detail", "build_competition",
                      "build_competition_matches", "build_team", "build_player")}
    api.build_listing = fake_build_listing
    api.build_detail = fake_build_detail
    api.build_competition = fake_build_competition
    api.build_competition_matches = fake_build_comp_matches
    api.build_team = fake_build_team
    api.build_player = fake_build_player
    api._comp_scrape_row = lambda conn, cid: payloads.get(f"scrape:{cid}")
    api._listing_cache.clear()

    import scraper.db.backend as backend

    @contextlib.contextmanager
    def fake_connection(dsn=None, pooled=True):
        conn = conn_holder.pop("next", None) or make_conn()
        conn_holder["conn"] = conn
        yield conn

    orig_connection = backend.connection
    orig_run_script = backend.run_script
    backend.connection = fake_connection
    backend.run_script = lambda conn, script: None
    api.backend = backend

    try:
        app = api.create_app("postgresql://fake")
        client = app.test_client()

        def sql_count() -> int:
            return len(conn_holder.get("conn").statements) if conn_holder.get("conn") else 0

        # -- 1) /api/matches: miss -> build+store, hit -> zero SQL ------------
        listing_calls["n"] = 0
        r1 = client.get("/api/matches?date=2026-08-15&today=2026-08-30&tz=0")
        check("listing miss serves 200", r1.status_code == 200)
        check("listing miss builds once", listing_calls["n"] == 1,
              f"n={listing_calls['n']}")
        etag1 = r1.headers.get("ETag")
        check("listing response carries an ETag", bool(etag1))
        body1 = r1.get_data(as_text=True)

        n_sql_after_miss = sql_count()
        listing_calls["n"] = 0
        r2 = client.get("/api/matches?date=2026-08-15&today=2026-08-30&tz=0")
        check("listing hit serves 200", r2.status_code == 200)
        check("listing hit does NOT rebuild", listing_calls["n"] == 0,
              f"n={listing_calls['n']}")
        check("listing hit runs ZERO new SQL", sql_count() == n_sql_after_miss,
              f"{n_sql_after_miss} -> {sql_count()}")
        check("listing hit is byte-identical",
              r2.get_data(as_text=True) == body1)
        check("listing hit keeps the same ETag", r2.headers.get("ETag") == etag1)

        r304 = client.get("/api/matches?date=2026-08-15&today=2026-08-30&tz=0",
                          headers={"If-None-Match": etag1})
        check("hit + matching If-None-Match answers 304",
              r304.status_code == 304)
        check("304 hit runs zero SQL", sql_count() == n_sql_after_miss)
        r200 = client.get("/api/matches?date=2026-08-15&today=2026-08-30&tz=0",
                          headers={"If-None-Match": '"other"'})
        check("hit + NON-matching If-None-Match answers 200",
              r200.status_code == 200)

        # past-day listing TTL (immutable finished scores -> long)
        ttl_used = fake.ttl.get(apicache.k_listing("2026-08-15", "2026-08-30", True, 0))
        check("past-day listing cached with the long TTL",
              ttl_used == apicache.TTL_LISTING_PAST, f"ttl={ttl_used}")

        # today listing -> short TTL
        client.get("/api/matches?date=2026-08-30&today=2026-08-30&tz=0")
        ttl_today = fake.ttl.get(apicache.k_listing("2026-08-30", "2026-08-30", True, 0))
        check("today listing cached with the short TTL",
              ttl_today == apicache.TTL_LISTING_TODAY, f"ttl={ttl_today}")

        # tz is part of the key
        listing_calls["n"] = 0
        client.get("/api/matches?date=2026-08-15&today=2026-08-30&tz=60")
        check("different tz is a different key (rebuild)",
              listing_calls["n"] == 1, f"n={listing_calls['n']}")

        # -- 2) /api/match: thin vs done TTLs, invalidation, no 404 cache ----
        payloads["match:m-thin-1"] = {
            "matchId": "m-thin-1", "status": "RESULT", "events": [],
            "lineups": {"confirmed": False, "home": None, "away": None},
            "stats": [], "homeTeam": {"id": "h"}, "awayTeam": {"id": "a"},
            "competition": {"id": "c"}, "kickoffUtc": None,
            "homeScore": 1, "awayScore": 0}
        r = client.get("/api/match/m-thin-1")
        check("thin detail serves refreshing=true",
              r.status_code == 200 and r.get_json().get("refreshing") is True)
        check("thin detail cached with the short net TTL",
              fake.ttl.get(apicache.k_match("m-thin-1")) == apicache.TTL_REFRESHING)
        detail_calls["n"] = 0
        client.get("/api/match/m-thin-1")
        check("thin detail hit skips the rebuild", detail_calls["n"] == 0)
        apicache.invalidate_match("m-thin-1")     # what the worker does
        detail_calls["n"] = 0
        client.get("/api/match/m-thin-1")
        check("after invalidate_match the next request rebuilds",
              detail_calls["n"] == 1, f"n={detail_calls['n']}")

        payloads["match:m-done-1"] = {
            "matchId": "m-done-1", "status": "RESULT", "events": [{"e": 1}],
            "lineups": {"confirmed": True, "home": {"id": "h"}, "away": None},
            "stats": [], "homeTeam": {"id": "h"}, "awayTeam": {"id": "a"},
            "competition": {"id": "c"}, "kickoffUtc": None,
            "homeScore": 2, "awayScore": 1}
        client.get("/api/match/m-done-1")
        check("finished full detail cached with the long TTL",
              fake.ttl.get(apicache.k_match("m-done-1")) == apicache.TTL_MATCH_DONE,
              f"ttl={fake.ttl.get(apicache.k_match('m-done-1'))}")

        r = client.get("/api/match/m-unknown-1")
        n404 = r.status_code
        check("unknown match still 404s", n404 == 404)
        check("404s are never cached",
              apicache.k_match("m-unknown-1") not in fake.store)

        # -- 3) /api/competition: hit skips view tracking entirely ------------
        payloads["comp:c-fresh-1"] = {
            "competition": {"id": "c-fresh-1"}, "standings": None,
            "gamesets": [], "generatedAt": "2026-08-30T00:00:00Z"}
        fresh = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        payloads["scrape:c-fresh-1"] = {
            "competition_id": "c-fresh-1", "has_standings": 1,
            "standings_at": fresh, "matches_at": fresh}
        client.get("/api/competition/c-fresh-1")
        n_sql_after_miss = sql_count()
        comp_calls["n"] = 0
        r = client.get("/api/competition/c-fresh-1")
        check("competition hit serves from cache (no rebuild)",
              comp_calls["n"] == 0 and r.status_code == 200)
        check("competition hit skips view-tracking SQL",
              sql_count() == n_sql_after_miss,
              f"{n_sql_after_miss} -> {sql_count()}")
        apicache.invalidate_competition("c-fresh-1")
        comp_calls["n"] = 0
        client.get("/api/competition/c-fresh-1")
        check("after invalidate_competition the next request rebuilds",
              comp_calls["n"] == 1, f"n={comp_calls['n']}")

        # -- 4) /api/competition/<id>/matches: gameset in the key --------------
        # (ids must be >= 4 chars - the routes validate that)
        payloads["compm:comp-c1xx:gset-1"] = {
            "competition": {"id": "comp-c1xx"},
            "gameset": {"id": "gset-1", "matchCount": 2},
            "matches": [], "generatedAt": "2026-08-30T00:00:00Z"}
        payloads["scrape:comp-c1xx"] = {
            "competition_id": "comp-c1xx", "has_standings": 1,
            "standings_at": fresh, "matches_at": fresh}
        r = client.get("/api/competition/comp-c1xx/matches?gameset=gset-1")
        check("round matches first request serves 200", r.status_code == 200,
              f"status={r.status_code}")
        compm_calls["n"] = 0
        client.get("/api/competition/comp-c1xx/matches?gameset=gset-1")
        check("round matches hit skips the rebuild", compm_calls["n"] == 0)
        check("round matches cached under the comp TTL",
              fake.ttl.get(apicache.k_comp_matches("comp-c1xx", "gset-1"))
              == apicache.TTL_COMP_MATCHES)
        apicache.invalidate_competition("comp-c1xx")
        compm_calls["n"] = 0
        client.get("/api/competition/comp-c1xx/matches?gameset=gset-1")
        check("invalidate_competition also drops round-match keys",
              compm_calls["n"] == 1, f"n={compm_calls['n']}")

        # -- 5) team + player: cached, correct TTLs ----------------------------
        payloads["team:team-abcd-1"] = {"team": {"id": "team-abcd-1"},
                                        "results": [], "fixtures": [],
                                        "squad": []}
        r = client.get("/api/team/team-abcd-1")
        check("team first request serves 200", r.status_code == 200)
        team_calls["n"] = 0
        client.get("/api/team/team-abcd-1")
        check("team hit skips the rebuild", team_calls["n"] == 0)
        check("team cached with the team TTL",
              fake.ttl.get(apicache.k_team("team-abcd-1")) == apicache.TTL_TEAM)

        payloads["player:p-full-1"] = {"player": {"id": "p-full-1"},
                                       "career": [{"c": 1}], "profileFetched": True}
        payloads["player:p-stub-1"] = {"player": {"id": "p-stub-1"},
                                       "career": [], "profileFetched": False}
        client.get("/api/player/p-full-1")
        client.get("/api/player/p-stub-1")
        player_calls["n"] = 0
        client.get("/api/player/p-full-1")
        check("player hit skips the rebuild", player_calls["n"] == 0)
        check("full profile cached with the long TTL",
              fake.ttl.get(apicache.k_player("p-full-1")) == apicache.TTL_PLAYER)
        check("stub profile cached with the short TTL",
              fake.ttl.get(apicache.k_player("p-stub-1")) == apicache.TTL_PLAYER_STUB)
        apicache.invalidate_player("p-full-1")
        player_calls["n"] = 0
        client.get("/api/player/p-full-1")
        check("after invalidate_player the next request rebuilds",
              player_calls["n"] == 1, f"n={player_calls['n']}")

        # -- 6) /api/health reports the cache status ---------------------------
        r = client.get("/api/health")
        st = (r.get_json() or {}).get("cache")
        check("health reports cache enabled+connected",
              st == {"enabled": True, "connected": True}, str(st))

        # -- 7) degradation: dead Redis falls back to plain DB reads -----------
        apicache._install("redis://fake", FakeRedis(fail=True))
        detail_calls["n"] = 0
        r = client.get("/api/match/m-done-1")
        check("dead Redis still serves 200 from the DB",
              r.status_code == 200 and detail_calls["n"] == 1,
              f"n={detail_calls['n']}")
        check("ETag flow intact while degraded", bool(r.headers.get("ETag")))
        st = apicache.status()
        check("status() shows the degraded window",
              st.get("enabled") is True and st.get("connected") is False, str(st))

        # -- 8) cache disabled entirely: today's behavior ----------------------
        apicache._reset()
        api._listing_cache.clear()
        detail_calls["n"] = 0
        client.get("/api/match/m-done-1")
        client.get("/api/match/m-done-1")
        check("without REDIS_URL every match request hits the DB",
              detail_calls["n"] == 2, f"n={detail_calls['n']}")
        listing_calls["n"] = 0
        client.get("/api/matches?date=2026-08-15&today=2026-08-30&tz=0")
        client.get("/api/matches?date=2026-08-15&today=2026-08-30&tz=0")
        check("without REDIS_URL the in-process listing cache still works",
              listing_calls["n"] == 1, f"n={listing_calls['n']}")
        r = client.get("/api/health")
        check("health reports the cache as disabled",
              (r.get_json() or {}).get("cache")
              == {"enabled": False, "connected": False})
    finally:
        backend.connection = orig_connection
        backend.run_script = orig_run_script
        for name, fn in orig_builders.items():
            setattr(api, name, fn)
        apicache._reset()


# ---------------------------------------------------------------------------
# suite 3: worker invalidation hooks
# ---------------------------------------------------------------------------
def run_worker_suite() -> None:
    print("\n== worker suite (invalidation after data lands) ==")

    invalidations: List[str] = []
    orig_hooks = {name: getattr(apicache, name) for name in
                  ("invalidate_day", "invalidate_competition",
                   "invalidate_match", "invalidate_player")}

    def rec(name):
        def _fn(ref):
            invalidations.append(f"{name}:{ref}")
        return _fn

    for name in orig_hooks:
        setattr(apicache, name, rec(name))

    import scraper.db.backend as backend

    @contextlib.contextmanager
    def fake_connection(dsn=None, pooled=True):
        yield FakeConn([])

    orig_connection = backend.connection
    backend.connection = fake_connection
    worker.backend = backend

    import scraper.pipeline as pipeline

    calls: List[str] = []

    def rec_pipeline(name):
        def _fn(*a, **k):
            calls.append(name)
            return True
        return _fn

    orig_pipeline = {name: getattr(pipeline, name) for name in
                     ("scrape_date_listings", "enrich_match", "enrich_player",
                      "enrich_date", "scrape_competition",
                      "scrape_competition_if_stale")}
    for name in orig_pipeline:
        setattr(pipeline, name, rec_pipeline(name))

    class FakeDB:
        def __init__(self, _url=None):
            self.conn = FakeConn([
                (r"SELECT slug_en FROM matches", lambda p: [{"slug_en": "s"}]),
                (r"SELECT profile_fetched_at FROM players",
                 lambda p: [{"profile_fetched_at": None}]),
            ])
            self.newly_finished_comps = set()

        def close(self):
            pass

        def commit(self):
            pass

    orig_database = worker.Database
    worker.Database = FakeDB

    try:
        # listing scrape -> invalidate the scraped days
        invalidations.clear(); calls.clear()
        worker._scrape_listing_dates(["2026-08-14", "2026-08-15"])
        check("listing scrape invalidates every scraped date",
              invalidations == ["invalidate_day:2026-08-14",
                                "invalidate_day:2026-08-15"],
              str(invalidations))
        check("listing scrape still scrapes + notes finished comps",
              "scrape_date_listings" in calls)

        # competition scrape -> invalidate that competition
        invalidations.clear(); calls.clear()
        worker._scrape_competition("comp-1", force=False)
        check("competition scrape invalidates the competition",
              invalidations == ["invalidate_competition:comp-1"], str(invalidations))

        # match detail job -> invalidate the match
        invalidations.clear(); calls.clear()
        worker._job_match_detail("m-1")
        check("match detail job invalidates the match",
              invalidations == ["invalidate_match:m-1"], str(invalidations))

        # player profile job -> invalidate the player
        invalidations.clear(); calls.clear()
        worker._job_player_profile("p-1")
        check("player profile job invalidates the player",
              invalidations == ["invalidate_player:p-1"], str(invalidations))

        # cron refresh path goes through _scrape_listing_dates too
        invalidations.clear(); calls.clear()
        worker._job_cron_refresh("2026-08-30")
        check("cron refresh invalidates its 3 days (yesterday/today/tomorrow)",
              len([i for i in invalidations if i.startswith("invalidate_day:")]) == 3,
              str(invalidations))
    finally:
        backend.connection = orig_connection
        worker.Database = orig_database
        for name, fn in orig_pipeline.items():
            setattr(pipeline, name, fn)
        for name, fn in orig_hooks.items():
            setattr(apicache, name, fn)
        apicache._reset()


def main() -> int:
    run_apicache_suite()
    run_api_suite()
    run_worker_suite()
    print(f"\n{'=' * 60}\nTOTAL: {PASS} passed, {FAIL} failed")
    if FAILURES:
        print("\nfailures:")
        for f in FAILURES:
            print(f"  - {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
