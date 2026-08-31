"""Full-stack test: Flask API (:9000) + real Next.js server (:3000) -
verifies the SSE stream passes through the Next.js proxy unbuffered, the
live endpoint is proxied, and a published change reaches the browser-side
stream within seconds.
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
API_PORT, WEB_PORT = 9200, 3200
MATCH = f"znext-{uuid.uuid4().hex[:8]}"
KICKOFF = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=50)
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "kooora")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def wait_port(port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.3)
    return False


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
    "status": "LIVE", "period": "LIVE 50",
    "home_score": 0, "away_score": 0,
    "home_red_cards": 0, "away_red_cards": 0,
}, listed_date=KICKOFF.date().isoformat())
db.commit()
db.close()
rc = redis_lib.Redis.from_url(os.environ["REDIS_URL"], socket_timeout=2)
rc.delete(live_mod.LIVE_LIST_KEY, live_mod.LIVE_LOG_KEY, live_mod.LIVE_SEQ_KEY)

api_proc = web_proc = None
try:
    # ---- boot the real API server ----------------------------------------
    env = dict(os.environ)
    env["SSE_HEARTBEAT_SEC"] = "2"
    api_proc = subprocess.Popen(
        [sys.executable, "-m", "scraper.cli", "api", "--port", str(API_PORT)],
        env=env, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    check("API server booted", wait_port(API_PORT))

    # ---- boot the real Next.js standalone server --------------------------
    web_env = dict(os.environ)
    web_env["FOOTBALL_API_BASE"] = f"http://127.0.0.1:{API_PORT}"
    web_env["PORT"] = str(WEB_PORT)
    web_env["HOSTNAME"] = "127.0.0.1"
    web_proc = subprocess.Popen(
        ["node", ".next/standalone/server.js"],
        env=web_env, cwd=ROOT,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    check("Next.js server booted", wait_port(WEB_PORT))
    time.sleep(1.5)

    # ---- proxied HTTP endpoints -------------------------------------------
    st, _, payload = None, None, None
    with urllib.request.urlopen(
            f"http://127.0.0.1:{WEB_PORT}/api/matches?major=0", timeout=15) as res:
        st, payload = res.status, json.loads(res.read())
    check("Next.js proxies /api/matches", st == 200
          and payload.get("totalMatches", 0) > 0,
          f"total={payload.get('totalMatches')}")
    with urllib.request.urlopen(
            f"http://127.0.0.1:{WEB_PORT}/api/matches/live", timeout=15) as res:
        st, livep = res.status, json.loads(res.read())
    check("Next.js proxies /api/matches/live", st == 200
          and any(m["matchId"] == MATCH for m in livep["matches"]))

    # ---- SSE through the Next.js proxy -------------------------------------
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", WEB_PORT, timeout=20)
    conn.request("GET", "/api/events/live",
                 headers={"Accept": "text/event-stream"})
    sse_res = conn.getresponse()
    ctype = sse_res.headers.get("Content-Type") or ""
    check("SSE content-type preserved through proxy",
          "text/event-stream" in ctype, ctype)
    check("SSE no-buffering header preserved",
          sse_res.headers.get("x-accel-buffering") == "no")

    events = []

    def reader():
        buf = b""
        while True:
            chunk = sse_res.read1(2048)
            if not chunk:
                return
            buf += chunk
            while b"\n\n" in buf:
                block, buf = buf.split(b"\n\n", 1)
                if b"event: " in block:
                    events.append(block.decode())
                    if len(events) >= 2:
                        return

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(8)
    check("snapshot streamed through Next.js",
          any("live.snapshot" in e for e in events), str(events[:1])[:90])

    # commit a change (worker path) -> must arrive through the proxy
    db = Database(DSN)
    db.upsert_match_from_listing({
        "match_id": MATCH, "status": "LIVE", "period": "LIVE 51",
        "home_score": 3, "away_score": 1,
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
    t2.join(8)
    upd = next((e for e in events if "match.updated" in e), None)
    check("live match.updated streamed through Next.js", upd is not None,
          str(events)[-120:] if upd is None else upd[:80])
    if upd:
        data = json.loads(upd.split("data: ", 1)[1])
        check("delta intact through the proxy",
              data["match"]["homeScore"] == 3 and data["matchId"] == MATCH)
    conn.close()

    # ---- the home page renders (SSR through the full stack) ---------------
    with urllib.request.urlopen(
            f"http://127.0.0.1:{WEB_PORT}/", timeout=20) as res:
        body = res.read().decode("utf-8", "replace")
    check("home page SSR renders", res.status == 200
          and ("match" in body.lower() or "Team" in body))

finally:
    for proc_ in (web_proc, api_proc):
        if proc_:
            proc_.send_signal(signal.SIGINT)
            try:
                proc_.wait(6)
            except subprocess.TimeoutExpired:
                proc_.kill()
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
print("ALL FULL-STACK TESTS PASSED")
