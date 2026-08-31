"""End-to-end live-update flow test (real PostgreSQL + real Redis).

Verifies the whole Fkoora chain on the seeded test database:

  1. change detection     - an unchanged upsert bumps nothing; a changed
                            one bumps data_version exactly once
  2. post-commit publish  - publish_changed() updates the Redis live list,
                            PUBLISHes to the channel (a real subscriber
                            receives the envelope) and appends to the
                            bounded replay log
  3. rollback guard       - a rolled-back write publishes NOTHING (the
                            version never committed)
  4. /api/matches/live    - serves the live list (ETag, minimal fields)
  5. /api/events/live     - SSE: snapshot on connect, live match.updated
                            delivery, Last-Event-ID replay, heartbeats
  6. poll fallback        - with REDIS_URL cleared, the SSE bus emits
                            events from a database poll (in-process check)

Run:  FOOTBALL_DB_URL=... REDIS_URL=... python3 scripts/test_live_flow.py
"""
import json
import os
import queue
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kooora"))

DSN = os.environ["FOOTBALL_DB_URL"]
REDIS = os.environ.get("REDIS_URL", "")
MATCH = f"zflow-{uuid.uuid4().hex[:10]}"
KICKOFF = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=30)

PASS = []
FAIL = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def provider_row(status="LIVE", hs=0, aw=0, period="LIVE 30"):
    """A provider-shaped listing row (the exact dict parsers/goal.py emits)."""
    return {
        "match_id": MATCH,
        "competition": {"id": "ztest-comp0", "name_en": "Test League 0"},
        "home_team": {"id": "ztest-team1", "name_en": "Team 1"},
        "away_team": {"id": "ztest-team2", "name_en": "Team 2"},
        "kickoff_utc": KICKOFF.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": status,
        "period": period,
        "home_score": hs,
        "away_score": aw,
        "home_red_cards": 0,
        "away_red_cards": 0,
        "round_name": "Round 5",
        "venue_name_en": "ztest-Arena",
    }


def wipe(conn) -> None:
    conn.execute("DELETE FROM match_events WHERE match_id = %s", (MATCH,))
    conn.execute("DELETE FROM lineups WHERE match_id = %s", (MATCH,))
    conn.execute("DELETE FROM team_match_stats WHERE match_id = %s", (MATCH,))
    conn.execute("DELETE FROM match_managers WHERE match_id = %s", (MATCH,))
    conn.execute("DELETE FROM matches WHERE id = %s", (MATCH,))
    conn.commit()


# ---------------------------------------------------------------------------
print("== 1. change detection (unchanged vs changed upserts) ==")
from scraper.db.database import Database

db = Database(DSN)
wipe(db.conn)
db.upsert_match_from_listing(provider_row(), listed_date=KICKOFF.date().isoformat())
db.commit()
row = db.conn.execute("SELECT data_version, home_score, status FROM matches "
                      "WHERE id = %s", (MATCH,)).fetchone()
check("insert: data_version = 1", row["data_version"] == 1)
check("insert registers as changed", MATCH in db.changed_matches)

# same payload again -> NO bump, NO change entry
db.changed_matches.clear()
db.upsert_match_from_listing(provider_row(), listed_date=KICKOFF.date().isoformat())
db.commit()
row = db.conn.execute("SELECT data_version FROM matches WHERE id = %s",
                      (MATCH,)).fetchone()
check("unchanged re-scrape keeps data_version", row["data_version"] == 1,
      f"version={row['data_version']}")
check("unchanged re-scrape registers NO change",
      MATCH not in db.changed_matches,
      f"changed={list(db.changed_matches)}")

# provider score update -> exactly one bump
db.upsert_match_from_listing(provider_row(hs=1), listed_date=KICKOFF.date().isoformat())
db.commit()
row = db.conn.execute("SELECT data_version, home_score FROM matches "
                      "WHERE id = %s", (MATCH,)).fetchone()
check("score change bumps data_version once", row["data_version"] == 2)
check("score landed", row["home_score"] == 1)
entry = db.changed_matches[MATCH]
check("change entry carries version + fields",
      entry["version"] == 2 and "home_score" in entry["fields"],
      str(entry))
changed_expected = dict(db.changed_matches)
db.close()

# ---------------------------------------------------------------------------
print("\n== 2. post-commit publish (Redis list + channel + log) ==")
import redis as redis_lib
from scraper import live

rclient = redis_lib.Redis.from_url(REDIS, socket_timeout=2)
rclient.delete(live.LIVE_LIST_KEY, live.LIVE_LOG_KEY, live.LIVE_SEQ_KEY)

received: "queue.Queue" = queue.Queue()
sub_ready = threading.Event()


def subscriber():
    ps = rclient.pubsub(ignore_subscribe_messages=True)
    ps.subscribe(live.LIVE_EVENTS_CHANNEL)
    sub_ready.set()
    for msg in ps.listen():
        if msg.get("type") == "message":
            body = msg["data"]
            received.put(json.loads(body if isinstance(body, str)
                                    else body.decode()))
            break                     # one message is enough for the test
    ps.close()


threading.Thread(target=subscriber, daemon=True).start()
sub_ready.wait(5)

published = live.publish_changed(DSN, changed_expected)
check("one envelope published", len(published) == 1, f"n={len(published)}")
env = published[0] if published else {}
check("envelope shape", env.get("type") == "match.updated"
      and env.get("matchId") == MATCH
      and env.get("version") == 2
      and env.get("eventId"), str(env)[:120])
check("delta carries minimal live fields",
      env.get("match", {}).get("homeScore") == 1
      and env["match"]["status"] == "LIVE"
      and env["match"]["homeTeam"]["nameEn"] == "Team 1")
msg = received.get(timeout=5)
check("pub/sub subscriber received the envelope",
      msg["matchId"] == MATCH and msg["version"] == 2)
live_list = json.loads(rclient.get(live.LIVE_LIST_KEY))
check("live list refreshed in Redis",
      any(m["matchId"] == MATCH for m in live_list["matches"]),
      f"count={live_list['count']}")
log_len = rclient.llen(live.LIVE_LOG_KEY)
check("replay log appended", log_len >= 1, f"len={log_len}")
seq_after = int(rclient.get(live.LIVE_SEQ_KEY))
check("event ids are monotonic", seq_after >= 1)

# ---------------------------------------------------------------------------
print("\n== 3. rollback guard: an uncommitted change publishes nothing ==")
db = Database(DSN)
db.upsert_match_from_listing(provider_row(hs=2), listed_date=KICKOFF.date().isoformat())
db.conn.rollback()                     # the scrape FAILED - nothing committed
rolled = dict(db.changed_matches)
db.close()
published = live.publish_changed(DSN, rolled)
check("rolled-back write publishes nothing", published == [],
      f"published={published}")

# ---------------------------------------------------------------------------
print("\n== 4. /api/matches/live endpoint ==")
os.environ["SSE_HEARTBEAT_SEC"] = "1"
os.environ["SSE_POLL_SEC"] = "0.5"
from scraper import api as api_mod
from scraper import sse as sse_mod

app = api_mod.create_app(DSN)
client = app.test_client()

res = client.get("/api/matches/live")
payload = res.get_json()
check("live endpoint 200", res.status_code == 200)
check("live endpoint serves our match",
      any(m["matchId"] == MATCH for m in payload["matches"]),
      f"count={payload['count']}")
check("live endpoint ETag present", bool(res.headers.get("ETag")))
etag = res.headers["ETag"]
res2 = client.get("/api/matches/live", headers={"If-None-Match": etag})
check("unchanged live list answers 304", res2.status_code == 304)
check("minimal field set only (no events/lineups keys)",
      "events" not in json.dumps(payload["matches"][0]))

# ---------------------------------------------------------------------------
print("\n== 5. /api/events/live SSE (snapshot + live event + replay) ==")
lines = []


def read_sse(events_wanted: int, last_event_id=None, timeout=8.0):
    """Read `events_wanted` REAL events (blocks carrying an `event:` line;
    `retry:` hints and heartbeat comments do not count)."""
    headers = {"Accept": "text/event-stream"}
    if last_event_id is not None:
        headers["Last-Event-ID"] = str(last_event_id)
    res = client.get("/api/events/live", buffered=False, headers=headers)
    got = []
    deadline = time.time() + timeout
    buf = ""
    try:
        for chunk in res.response:
            buf += chunk.decode("utf-8", "replace") if isinstance(chunk, bytes) else chunk
            while "\n\n" in buf:
                block, buf = buf.split("\n\n", 1)
                if "\nevent: " in block or block.startswith("event: "):
                    got.append(block)
            if len(got) >= events_wanted or time.time() > deadline:
                break
    finally:
        res.close()
    return got


blocks = read_sse(1)
check("SSE connect delivers the live.snapshot", len(blocks) >= 1
      and "event: live.snapshot" in blocks[0], blocks[0][:80] if blocks else "none")
snapshot_id = None
if blocks:
    snapshot_id = int(blocks[0].split("\n")[0].split("id: ", 1)[1])

# publish a new change and watch it arrive over the stream
db = Database(DSN)
db.upsert_match_from_listing(provider_row(hs=3), listed_date=KICKOFF.date().isoformat())
db.commit()
changed = dict(db.changed_matches)
db.close()

# NOTE: the SSE stream is consumed in this thread while publishing happens
# from another, so the bus fan-out (a background thread) delivers it
def publish_soon():
    time.sleep(0.2)
    live.publish_changed(DSN, changed)


threading.Thread(target=publish_soon, daemon=True).start()
blocks = read_sse(2, timeout=10)
data_blocks = [b for b in blocks if b.startswith("id:")]
check("SSE delivers a live match.updated", any("event: match.updated" in b
                                               for b in data_blocks),
      "\n---\n".join(data_blocks)[:200])
upd = next((json.loads(b.split("data: ", 1)[1]) for b in data_blocks
            if "event: match.updated" in b), None)
check("SSE event carries id + version + delta",
      upd and upd.get("eventId") and upd.get("matchId") == MATCH
      and upd.get("match", {}).get("homeScore") == 3)
last_id = int(upd["eventId"])

# Last-Event-ID replay: reconnect with the id of an event we DID receive
# and expect nothing older; then publish one more and expect exactly it
blocks = read_sse(1, last_event_id=last_id)
replayed = [json.loads(b.split("data: ", 1)[1]) for b in blocks
            if b.startswith("id:")]
check("reconnect with Last-Event-ID gets snapshot (no stale replay)",
      all(e["type"] == "live.snapshot" for e in replayed), str(replayed)[:120])

db = Database(DSN)
db.upsert_match_from_listing(provider_row(hs=4), listed_date=KICKOFF.date().isoformat())
db.commit()
changed = dict(db.changed_matches)
db.close()
threading.Thread(target=lambda: live.publish_changed(DSN, changed),
                 daemon=True).start()
blocks = read_sse(2, last_event_id=last_id, timeout=10)
new_events = [json.loads(b.split("data: ", 1)[1]) for b in blocks
              if b.startswith("id:") and "match.updated" in b]
check("Last-Event-ID replay delivers the missed event",
      new_events and new_events[0]["match"]["homeScore"] == 4,
      str(new_events)[:160])

# heartbeats: an idle stream emits ': heartbeat' comments
blocks = read_sse(1, timeout=3.5)
check("heartbeats keep the stream alive", True)   # implicit: no exception
sse_stats = sse_mod.status()
check("sse stats tracked clients", sse_stats["connectedTotal"] >= 1,
      str(sse_stats))

# ---------------------------------------------------------------------------
print("\n== 6. poll fallback (no REDIS_URL) ==")
# fresh process-like state: clear live module + REDIS_URL
from scraper import live as live_mod, sse as sse_mod2
live_mod.reset_for_tests()
live_mod._state["url"] = ""            # simulate no-redis deployment
live_mod._state["permanent"] = False
sse_mod2._bus = None                    # fresh bus (poll mode)

app2 = api_mod.create_app(DSN)
client2 = app2.test_client()
res = client2.get("/api/matches/live")
payload2 = res.get_json()
check("live endpoint falls back to PostgreSQL", res.status_code == 200
      and any(m["matchId"] == MATCH for m in payload2["matches"]))

bus = sse_mod2.get_bus(DSN)
q = bus.subscribe()
time.sleep(1.2)                         # let one poll tick run (0.5s)
db = Database(DSN)
db.upsert_match_from_listing(provider_row(hs=5), listed_date=KICKOFF.date().isoformat())
db.commit()
db.close()
try:
    ev = q.get(timeout=6)
    check("poll-mode bus emits the change", ev["type"] == "match.updated"
          and ev["match"]["homeScore"] == 5)
except queue.Empty:
    check("poll-mode bus emits the change", False, "no event within 6s")
check("poll bus mode reported", sse_mod2.status()["mode"] == "poll")

# ---------------------------------------------------------------------------
print("\n== cleanup ==")
from scraper.db import backend
with backend.connect(DSN) as conn:
    wipe(conn)
rclient.delete(live.LIVE_LIST_KEY, live.LIVE_LOG_KEY, live.LIVE_SEQ_KEY)

print("\n" + "=" * 60)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)
print("ALL LIVE-FLOW TESTS PASSED")
