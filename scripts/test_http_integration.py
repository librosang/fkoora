"""Real-HTTP integration test: boots the actual Flask API server
(`python -m scraper.cli api`) against the seeded DB + Redis and verifies:

  1. /api/matches + /api/matches/live + /api/health respond over HTTP
  2. SSE /api/events/live streams a live.snapshot, then delivers a
     match.updated event the moment a committed change is published
  3. ETag revalidation (304) on the live endpoint
  4. cache stampede control: 40 concurrent cold requests produce exactly
     ONE cache miss (single-flight) in the API process
"""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kooora"))

DSN = os.environ["FOOTBALL_DB_URL"]
PORT = 9123
BASE = f"http://127.0.0.1:{PORT}"
MATCH = f"zhttp-{uuid.uuid4().hex[:8]}"
KICKOFF = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=40)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def get_json(path, headers=None):
    req = urllib.request.Request(BASE + path, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, dict(res.headers), json.loads(res.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 304:   # revalidation success: no body by design
            return 304, dict(exc.headers), {}
        raise


def wait_port(port, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.25)
    return False


# ---------------------------------------------------------------------------
# seed one controllable live match through the upsert layer
from scraper.db.database import Database
from scraper import live as live_mod
import redis as redis_lib

db = Database(DSN)
db.conn.execute("DELETE FROM matches WHERE id = %s", (MATCH,))
db.conn.commit()
db.upsert_match_from_listing({
    "match_id": MATCH,
    "competition": {"id": "ztest-comp0", "name_en": "Test League 0"},
    "home_team": {"id": "ztest-team1", "name_en": "Team 1"},
    "away_team": {"id": "ztest-team2", "name_en": "Team 2"},
    "kickoff_utc": KICKOFF.strftime("%Y-%m-%dT%H:%M:%SZ"),
    "status": "LIVE", "period": "LIVE 40",
    "home_score": 0, "away_score": 0,
    "home_red_cards": 0, "away_red_cards": 0,
}, listed_date=KICKOFF.date().isoformat())
db.commit()
db.close()
rc = redis_lib.Redis.from_url(os.environ["REDIS_URL"], socket_timeout=2)
rc.delete(live_mod.LIVE_LIST_KEY, live_mod.LIVE_LOG_KEY, live_mod.LIVE_SEQ_KEY)

# ---------------------------------------------------------------------------
# boot the real server
env = dict(os.environ)
env["SSE_HEARTBEAT_SEC"] = "1"
env["PORT"] = str(PORT)
env.pop("PYTHONPATH", None)
proc = subprocess.Popen(
    [sys.executable, "-m", "scraper.cli", "api", "--port", str(PORT)],
    env=env, cwd=os.path.join(os.path.dirname(__file__), "..", "kooora"),
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)
try:
    ok = wait_port(PORT)
    check("API server booted", ok)
    if not ok:
        out = proc.stdout.read().decode() if proc.stdout else ""
        print(out[:2000])
        sys.exit(1)

    # ---- 1. basic endpoints ----------------------------------------------
    st, hdr, payload = get_json("/api/health")
    check("GET /api/health", st == 200 and payload.get("ok")
          and "sse" in payload and "live" in payload, str(payload.keys()))
    st, hdr, payload = get_json("/api/matches?major=0")
    check("GET /api/matches", st == 200 and payload.get("totalMatches", 0) > 0,
          f"total={payload.get('totalMatches')}")
    st, hdr, payload = get_json("/api/matches/live")
    check("GET /api/matches/live", st == 200
          and any(m["matchId"] == MATCH for m in payload["matches"]))
    etag = hdr.get("ETag")
    st, hdr, _ = get_json("/api/matches/live", headers={"If-None-Match": etag})
    check("live endpoint 304 revalidation", st == 304, f"status={st}")

    # ---- 2. SSE over real HTTP --------------------------------------------
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", PORT, timeout=15)
    conn.request("GET", "/api/events/live", headers={"Accept": "text/event-stream"})
    sse_res = conn.getresponse()
    check("SSE endpoint content-type",
          "text/event-stream" in (sse_res.headers.get("Content-Type") or ""),
          sse_res.headers.get("Content-Type"))
    check("SSE nginx header", sse_res.headers.get("X-Accel-Buffering") == "no")

    got_events = []
    stop_reader = threading.Event()

    def reader():
        buf = b""
        while not stop_reader.is_set():
            chunk = sse_res.read1(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n\n" in buf:
                block, buf = buf.split(b"\n\n", 1)
                if b"event: " in block:
                    got_events.append(block.decode())
                    if len(got_events) >= 2:
                        return

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(6)
    check("SSE snapshot delivered over HTTP",
          any("live.snapshot" in b for b in got_events),
          str(got_events[:1])[:100])

    # publish a committed change (worker-style) and watch it arrive
    db = Database(DSN)
    db.conn.execute(
        """UPDATE matches SET home_score = 1, data_version = data_version + 1
           WHERE id = %s""", (MATCH,))
    db.commit()
    db.close()
    db = Database(DSN)
    db.upsert_match_from_listing({
        "match_id": MATCH, "status": "LIVE", "period": "LIVE 41",
        "home_score": 2, "away_score": 0,
        "competition": {"id": "ztest-comp0"},
        "home_team": {"id": "ztest-team1"}, "away_team": {"id": "ztest-team2"},
        "kickoff_utc": KICKOFF.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "home_red_cards": 0, "away_red_cards": 0,
    }, listed_date=KICKOFF.date().isoformat())
    db.commit()
    changed = dict(db.changed_matches)
    db.close()
    live_mod.publish_changed(DSN, changed)

    t2 = threading.Thread(target=reader, daemon=True)
    t2.start()
    t2.join(6)
    upd = next((b for b in got_events if "match.updated" in b), None)
    check("SSE live match.updated delivered over HTTP", upd is not None,
          str(got_events)[::1][:200] if upd is None else upd[:80])
    if upd:
        data = json.loads(upd.split("data: ", 1)[1])
        check("SSE delta fresh (score 2)",
              data["match"]["homeScore"] == 2 and data["matchId"] == MATCH)
        check("SSE event carries id: line", upd.startswith("id: "))
    stop_reader.set()
    conn.close()

    # ---- 3. cache stampede single-flight -----------------------------------
    rc.delete("fk:api:v1:listing:*")  # not a glob - clear via scan
    for key in rc.scan_iter(match="fk:api:v1:listing:*"):
        rc.delete(key)
    before = get_json("/api/health")[2]
    builds_before = before.get("builds", {}).get("listingBuilds", 0)

    import urllib.error as _ue
    import concurrent.futures

    def hit():
        try:
            get_json("/api/matches?major=0")
            return True
        except _ue.HTTPError as exc:
            return exc.code == 304
        except Exception:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=40) as pool:
        results = list(pool.map(lambda _: hit(), range(40)))
    check("40 concurrent requests all served", all(results),
          f"ok={sum(results)}/40")
    after = get_json("/api/health")[2]
    builds_after = after.get("builds", {}).get("listingBuilds", 0)
    rebuilds = builds_after - builds_before
    check("single-flight: exactly ONE SQL rebuild under a 40-request burst",
          rebuilds == 1, f"rebuilds={rebuilds}")

finally:
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
    # cleanup
    try:
        db2 = Database(DSN)
        for table in ("match_events", "lineups", "team_match_stats",
                      "match_managers"):
            db2.conn.execute(f"DELETE FROM {table} WHERE match_id = %s", (MATCH,))
        db2.conn.execute("DELETE FROM matches WHERE id = %s", (MATCH,))
        db2.commit()
        db2.close()
    except Exception:
        pass
    rc.delete(live_mod.LIVE_LIST_KEY, live_mod.LIVE_LOG_KEY, live_mod.LIVE_SEQ_KEY)

print("\n" + "=" * 60)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)
print("ALL HTTP INTEGRATION TESTS PASSED")
