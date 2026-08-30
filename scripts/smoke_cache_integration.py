#!/usr/bin/env python3
"""Integration smoke: apicache against a REAL redis-py client (fakeredis).

Unlike scripts/test_cache.py (FakeRedis standing in for the client), this
runs apicache's own connection code - redis.Redis.from_url + ping + set(ex=)
+ get + delete + scan_iter(match=) - against fakeredis, a faithful
in-process implementation of the Redis protocol. Catches redis-py API
misuse (wrong kwargs, wrong call shapes) without needing a redis-server.

Also boots the real create_app() (DB layer patched) and the real worker
invalidation helpers against the same client, and verifies the dead-server
degradation path with redis-py's real ConnectionError.

Run:  python3 scripts/smoke_cache_integration.py
"""
from __future__ import annotations

import contextlib
import logging
import os
import sys
from typing import Any, Dict, List, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis as redis_mod
import fakeredis

import scraper.api as api
import scraper.apicache as apicache

PASS = 0
FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok    {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name} {detail}")


# ---------------------------------------------------------------------------
# real redis-py client on fakeredis, through apicache's own from_url path
# ---------------------------------------------------------------------------
server = fakeredis.FakeServer()
real_from_url = redis_mod.Redis.from_url


def fake_from_url(url, **kwargs):
    # apicache passes socket timeouts etc. - fakeredis accepts and ignores them
    return fakeredis.FakeRedis(server=server, decode_responses=True)


redis_mod.Redis.from_url = staticmethod(fake_from_url)

apicache._reset()
apicache._state["url"] = "redis://integration-test:6379/0"
client = apicache._client()
check("apicache connects through redis.Redis.from_url", client is not None)
check("connected client is a real redis-py Redis", isinstance(client, redis_mod.Redis))

apicache.put(apicache.k_match("smoke-m-1"), '"etag-smoke"', '{"ok": 1}', 30)
hit = apicache.get(apicache.k_match("smoke-m-1"))
check("real SET/GET round trip", hit == ('"etag-smoke"', '{"ok": 1}'), str(hit))
ttl = client.ttl(apicache.k_match("smoke-m-1"))
check("SET applied the expiry (TTL ~30)", 25 <= ttl <= 30, f"ttl={ttl}")

# glob invalidation through a real SCAN
for gs in ("g1", "g2"):
    apicache.put(apicache.k_comp_matches("smoke-comp", gs), '"e"', "{}", 60)
apicache.put(apicache.k_competition("smoke-comp"), '"e"', "{}", 60)
apicache.put(apicache.k_competition("smoke-other"), '"e"', "{}", 60)
apicache.invalidate_competition("smoke-comp")
check("invalidate_competition works over real SCAN+DEL",
      apicache.get(apicache.k_comp_matches("smoke-comp", "g1")) is None
      and apicache.get(apicache.k_comp_matches("smoke-comp", "g2")) is None
      and apicache.get(apicache.k_competition("smoke-comp")) is None
      and apicache.get(apicache.k_competition("smoke-other")) is not None)

apicache.put(apicache.k_listing("2026-08-30", "2026-08-30", True, 0), '"e"', "{}", 15)
apicache.put(apicache.k_listing("2026-08-29", "2026-08-30", True, 0), '"e"', "{}", 15)
apicache.invalidate_day("2026-08-30")
check("invalidate_day works over real SCAN+DEL (day+neighbour)",
      apicache.get(apicache.k_listing("2026-08-30", "2026-08-30", True, 0)) is None
      and apicache.get(apicache.k_listing("2026-08-29", "2026-08-30", True, 0)) is None)

st = apicache.status()
check("status() against the live client",
      st.get("enabled") is True and st.get("connected") is True, str(st))


# ---------------------------------------------------------------------------
# the whole API on top of it (DB layer patched, cache real)
# ---------------------------------------------------------------------------
class FakeCursor:
    def __init__(self, rows):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=None):
        import re
        self.statements.append((sql, params))
        if re.search(r"FROM scrape_runs", sql):
            return FakeCursor([])
        if re.search(r"COUNT\(\*\) AS n", sql):
            return FakeCursor([{"n": 0}])
        if re.search(r"MAX\(last_seen_at2\)", sql):
            return FakeCursor([{"v": None}])
        return FakeCursor([])

    def commit(self):
        pass

    def close(self):
        pass


import scraper.db.backend as backend


@contextlib.contextmanager
def fake_connection(dsn=None, pooled=True):
    yield FakeConn()


backend.connection = fake_connection
backend.run_script = lambda conn, script: None
api.backend = backend

builds = {"n": 0}


def fake_build_listing(conn, date, today, major_only, tz_min):
    builds["n"] += 1
    return {"date": date, "today": today, "totalMatches": 1,
            "competitions": [{"id": "c", "matches": []}]}


api.build_listing = fake_build_listing
api._listing_cache.clear()

app = api.create_app("postgresql://fake")
c = app.test_client()

r1 = c.get("/api/matches?date=2026-08-30&today=2026-08-30&tz=0")
etag = r1.headers.get("ETag")
check("API miss builds + serves", r1.status_code == 200 and builds["n"] == 1)
builds["n"] = 0
r2 = c.get("/api/matches?date=2026-08-30&today=2026-08-30&tz=0")
check("API hit served by the real redis client (no rebuild)",
      r2.status_code == 200 and builds["n"] == 0)
r304 = c.get("/api/matches?date=2026-08-30&today=2026-08-30&tz=0",
             headers={"If-None-Match": etag})
check("API 304 fast path through the real client", r304.status_code == 304)

apicache.invalidate_day("2026-08-30")
builds["n"] = 0
r3 = c.get("/api/matches?date=2026-08-30&today=2026-08-30&tz=0")
check("worker invalidation forces a rebuild through the real client",
      r3.status_code == 200 and builds["n"] == 1)

r = c.get("/api/health")
check("health reports the real client as connected",
      (r.get_json() or {}).get("cache") == {"enabled": True, "connected": True})


# ---------------------------------------------------------------------------
# dead-server degradation with redis-py's real ConnectionError
# ---------------------------------------------------------------------------
redis_mod.Redis.from_url = real_from_url
apicache._reset()
apicache._state["url"] = "redis://127.0.0.1:1/0"      # nothing listens there
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
apicache._state["last_log"] = 0.0                      # let the warning through

builds["n"] = 0
r4 = c.get("/api/matches?date=2026-08-30&today=2026-08-30&tz=0")
check("dead Redis degrades to a plain DB build (200)",
      r4.status_code == 200 and builds["n"] == 1)
check("degradation opened the down window",
      apicache._state["down_until"] > 0.0)
st = apicache.status()
check("status() reports the outage",
      st.get("enabled") is True and st.get("connected") is False, str(st))

apicache._reset()
redis_mod.Redis.from_url = real_from_url

print(f"\nTOTAL: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
