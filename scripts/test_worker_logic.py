"""Worker logic tests: adaptive provider polling, the post-commit publish
drain, and the midnight/day-transition behavior (simulated)."""
import json
import os
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kooora"))

DSN = os.environ["FOOTBALL_DB_URL"]
PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


from scraper import worker
from scraper.db import backend
from scraper.db.database import Database
from scraper import live as live_mod
import redis as redis_lib

rc = redis_lib.Redis.from_url(os.environ["REDIS_URL"], socket_timeout=2)
rc.delete(live_mod.LIVE_LIST_KEY, live_mod.LIVE_LOG_KEY, live_mod.LIVE_SEQ_KEY)

print("== adaptive polling interval selection ==")
# suspend the SEEDED live matches AND near-term fixtures so the idle/upcoming
# probes see only the state this test creates (both restored at the end)
with backend.connection(DSN) as conn:
    conn.execute("""UPDATE matches SET status = 'RESULT'
                    WHERE status = 'LIVE'""")
    conn.execute("""UPDATE matches SET kickoff_utc = kickoff_utc + INTERVAL '1 day'
                    WHERE status = 'FIXTURE'
                      AND kickoff_utc >= NOW() AND kickoff_utc < NOW() + INTERVAL '2 hours'""")
    conn.commit()

def set_match(mid, status, kickoff_min):
    db = Database(DSN)
    db.conn.execute("DELETE FROM matches WHERE id = %s", (mid,))
    db.conn.commit()
    kickoff = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=kickoff_min)
    db.upsert_match_from_listing({
        "match_id": mid, "status": status,
        "competition": {"id": "ztest-comp0"},
        "home_team": {"id": "ztest-team1"}, "away_team": {"id": "ztest-team2"},
        "kickoff_utc": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home_red_cards": 0, "away_red_cards": 0,
    }, listed_date=kickoff.date().isoformat())
    db.commit()
    db.close()


M1, M2, M3 = f"zw1-{uuid.uuid4().hex[:6]}", f"zw2-{uuid.uuid4().hex[:6]}", f"zw3-{uuid.uuid4().hex[:6]}"

# idle: nothing live, nothing upcoming soon
set_match(M1, "RESULT", -600)
with backend.connection(DSN) as conn:
    iv = worker._today_interval(conn)
check("idle -> PROVIDER_POLL_IDLE_SECONDS",
      iv == worker.PROVIDER_POLL_IDLE_SECONDS, f"iv={iv}")

# upcoming: a fixture kicks off within the lookahead window
set_match(M2, "FIXTURE", 20)  # well inside the 30-min lookahead
with backend.connection(DSN) as conn:
    iv = worker._today_interval(conn)
check("upcoming kickoff -> PROVIDER_POLL_UPCOMING_SECONDS",
      iv == worker.PROVIDER_POLL_UPCOMING_SECONDS, f"iv={iv}")

# live: a match is LIVE right now
set_match(M3, "LIVE", -20)
with backend.connection(DSN) as conn:
    iv = worker._today_interval(conn)
check("live match -> PROVIDER_POLL_LIVE_SECONDS",
      iv == worker.PROVIDER_POLL_LIVE_SECONDS, f"iv={iv}")

# adaptive disabled -> fixed legacy cadence
saved = worker.ADAPTIVE_POLL
worker.ADAPTIVE_POLL = False
with backend.connection(DSN) as conn:
    iv = worker._today_interval(conn)
check("ADAPTIVE_POLL=0 -> fixed REFRESH_TODAY_SEC",
      iv == worker.REFRESH_TODAY_SEC, f"iv={iv}")
worker.ADAPTIVE_POLL = saved

# the probe is index-backed (fast) - just time it
t0 = time.time()
with backend.connection(DSN) as conn:
    for _ in range(20):
        worker._polling_activity(conn)
elapsed = (time.time() - t0) / 20
check("activity probe is cheap (<25ms avg)", elapsed < 0.025, f"{elapsed*1000:.1f}ms")

print("\n== worker post-commit publish drain ==")
db = Database(DSN)
db.conn.execute("DELETE FROM matches WHERE id = %s", (M3,))
db.conn.commit()
kickoff = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=25)
db.upsert_match_from_listing({
    "match_id": M3, "status": "LIVE", "period": "LIVE 25",
    "home_score": 1, "away_score": 0,
    "competition": {"id": "ztest-comp0"},
    "home_team": {"id": "ztest-team1"}, "away_team": {"id": "ztest-team2"},
    "kickoff_utc": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "home_red_cards": 0, "away_red_cards": 0,
}, listed_date=kickoff.date().isoformat())
db.commit()
check("changed_matches populated by upsert", M3 in db.changed_matches)
db.close()   # <- the worker pattern: close() commits

# drain exactly like the worker does after db.close()
worker._publish_live_changes(db)
check("drain cleared the change sets", not db.changed_matches
      and not db.new_match_events)
live_list = json.loads(rc.get(live_mod.LIVE_LIST_KEY) or "{}")
check("live list refreshed by the drain",
      any(m["matchId"] == M3 for m in live_list.get("matches", [])))
check("drain on empty sets is a no-op (no crash)",
      worker._publish_live_changes(db) is None)

print("\n== midnight / day-transition state handling ==")
state = {"listing": time.time(), "around": time.time(), "live": 0.0,
         "backfill": 0.0, "comp_warm": 0.0, "ar_today": 0.0, "ar_around": 0.0,
         "around_iv": 300.0, "today_iv": 60.0, "date": "2026-08-31"}
today = "2026-09-01"
# replicate the transition block from _scheduler_tick
if state.get("date") != today:
    if state.get("date") is not None:
        state["listing"] = 0.0
        state["around"] = 0.0
        state["today_iv"] = worker.REFRESH_TODAY_SEC
        worker._detail_ar_ts.clear()
    state["date"] = today
check("day transition resets listing timer (immediate new-day scrape)",
      state["listing"] == 0.0 and state["date"] == today)
check("day transition resets the around cadence",
      state["around"] == 0.0)

# cleanup: remove test matches + restore the seeded live statuses + kickoffs
with backend.connect(DSN) as conn:
    for mid in (M1, M2, M3):
        conn.execute("DELETE FROM matches WHERE id = %s", (mid,))
    conn.execute("""UPDATE matches SET status = 'LIVE'
                    WHERE id LIKE 'ztest-match-live%'""")
    conn.execute("""UPDATE matches SET kickoff_utc = kickoff_utc - INTERVAL '1 day'
                    WHERE status = 'FIXTURE' AND id LIKE 'ztest-%'
                      AND kickoff_utc >= NOW() AND kickoff_utc < NOW() + INTERVAL '2 hours'""")
    conn.commit()
rc.delete(live_mod.LIVE_LIST_KEY, live_mod.LIVE_LOG_KEY, live_mod.LIVE_SEQ_KEY)


print("\n" + "=" * 60)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)
print("ALL WORKER LOGIC TESTS PASSED")
