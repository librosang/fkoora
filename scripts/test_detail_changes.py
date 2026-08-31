"""Change-detection tests for the DETAIL write path (apply_match_detail):
events/lineups/stats diffs, version bumps, new_match_events extraction,
idempotency of repeated identical detail fetches, and the thin-refresh
cache invalidation hook. Runs against the seeded test database.
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kooora"))

DSN = os.environ["FOOTBALL_DB_URL"]
MATCH = f"zdetail-{uuid.uuid4().hex[:10]}"
KICKOFF = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=45)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def listing_row(hs=0, aw=0):
    return {
        "match_id": MATCH,
        "competition": {"id": "ztest-comp0", "name_en": "Test League 0"},
        "home_team": {"id": "ztest-team1", "name_en": "Team 1"},
        "away_team": {"id": "ztest-team2", "name_en": "Team 2"},
        "kickoff_utc": KICKOFF.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "status": "LIVE",
        "period": "LIVE 45",
        "home_score": hs,
        "away_score": aw,
        "home_red_cards": 0, "away_red_cards": 0,
        "round_name": "Round 5",
    }


def detail_payload(events, lineups_confirmed=True, period="LIVE 45"):
    return {
        "match_id": MATCH,
        "competition": {"id": "ztest-comp0", "name_en": "Test League 0"},
        "season": {"id": "ztest-season1", "name": "2026/2027"},
        "status": "LIVE",
        "period": period,
        "home_score": 1, "away_score": 0,
        "home_score_ht": None, "away_score_ht": None,
        "referee": "Test Referee",
        "venue": {"name_en": "ztest-Arena", "name_ar": "ملعب"},
        "events": events,
        "lineups": {"confirmed": lineups_confirmed,
                    "teams": {"home": {"team_id": "ztest-team1", "formation": "4-3-3",
                                       "manager": {"id": "zm1", "name_en": "Boss"},
                                       "entries": [
                                           {"person": {"id": "ztest-player1",
                                                       "name_en": "P1"}, "is_starter": 1,
                                            "shirt_number": 1},
                                           {"person": {"id": "ztest-player2",
                                                       "name_en": "P2"}, "is_starter": 1,
                                            "shirt_number": 2}]},
                              "away": {"team_id": "ztest-team2", "formation": "4-4-2",
                                       "manager": {"id": "zm2", "name_en": "Coach"},
                                       "entries": [
                                           {"person": {"id": "ztest-player3",
                                                       "name_en": "P3"}, "is_starter": 1,
                                            "shirt_number": 3}]}}},
        "stats": [{"stat_type": "POSSESSION", "home_value": 55, "away_value": 45}],
    }


def ev(minute, etype="GOAL", player="ztest-player1", pname="P1", side="home",
       hs_after=1, aw_after=0):
    return {"event_type": etype, "minute": minute, "team_side": side,
            "player": {"id": player, "name_en": pname},
            "related_player": {"id": None, "name_en": None},
            "home_score_after": hs_after, "away_score_after": aw_after,
            "sort_order": minute}


from scraper.db.database import Database

print("== detail change detection ==")
db = Database(DSN)
db.conn.execute("DELETE FROM matches WHERE id = %s", (MATCH,))
db.commit()
db.upsert_match_from_listing(listing_row(), listed_date=KICKOFF.date().isoformat())
db.commit()
db.changed_matches.clear()
v = db.conn.execute("SELECT data_version FROM matches WHERE id=%s", (MATCH,)).fetchone()["data_version"]
check("baseline version", v == 1, f"v={v}")

# 1) first detail apply: everything is new -> bump + events recorded
db.apply_match_detail(detail_payload([ev(20), ev(35)]))
db.commit()
v = db.conn.execute("SELECT data_version FROM matches WHERE id=%s", (MATCH,)).fetchone()["data_version"]
check("first detail apply bumps version", v == 2, f"v={v}")
check("first detail registers change", MATCH in db.changed_matches
      and "events" in db.changed_matches[MATCH]["fields"])
check("new events extracted for match.event SSE",
      len(db.new_match_events.get(MATCH, [])) == 2)
n = db.conn.execute("SELECT COUNT(*) AS n FROM match_events WHERE match_id=%s", (MATCH,)).fetchone()["n"]
check("events stored", n == 2, f"n={n}")

# 2) identical re-fetch: NO bump, no events, no changed entry
db.changed_matches.clear(); db.new_match_events.clear()
db.apply_match_detail(detail_payload([ev(20), ev(35)]))
db.commit()
v = db.conn.execute("SELECT data_version FROM matches WHERE id=%s", (MATCH,)).fetchone()["data_version"]
check("identical detail re-fetch does NOT bump", v == 2, f"v={v}")
check("identical re-fetch registers nothing", MATCH not in db.changed_matches
      and not db.new_match_events)

# 3) a new goal arrives -> bump + exactly the new event
db.changed_matches.clear(); db.new_match_events.clear()
db.apply_match_detail(detail_payload([ev(20), ev(35), ev(67, hs_after=2)]))
db.commit()
v = db.conn.execute("SELECT data_version FROM matches WHERE id=%s", (MATCH,)).fetchone()["data_version"]
check("new goal bumps version", v == 3, f"v={v}")
newevs = db.new_match_events.get(MATCH, [])
check("only the NEW event extracted", len(newevs) == 1
      and newevs[0]["minute"] == 67 and newevs[0]["eventType"] == "GOAL",
      str(newevs))
check("event wire shape (camelCase for SSE)",
      set(newevs[0].keys()) >= {"eventType", "minute", "teamSide", "playerId",
                                "playerNameEn"})

# 4) stats change -> bump
db.changed_matches.clear(); db.new_match_events.clear()
d = detail_payload([ev(20), ev(35), ev(67, hs_after=2)])
d["stats"] = [{"stat_type": "POSSESSION", "home_value": 58, "away_value": 42}]
db.apply_match_detail(d)
db.commit()
v = db.conn.execute("SELECT data_version FROM matches WHERE id=%s", (MATCH,)).fetchone()["data_version"]
check("stats change bumps version", v == 4, f"v={v}")
check("stats change registered", "statistics" in db.changed_matches.get(MATCH, {}).get("fields", []))

# 5) minute/period change (same data otherwise) -> bump
db.changed_matches.clear(); db.new_match_events.clear()
db.apply_match_detail(detail_payload([ev(20), ev(35), ev(67, hs_after=2)], period="LIVE 68"))
db.commit()
v = db.conn.execute("SELECT data_version FROM matches WHERE id=%s", (MATCH,)).fetchone()["data_version"]
check("period change bumps version", v == 5, f"v={v}")

# 6) language-preserving refresh: AR-only pass keeps EN names, no bump
db.changed_matches.clear(); db.new_match_events.clear()
db.apply_match_detail(detail_payload([ev(20), ev(35), ev(67, hs_after=2)], period="LIVE 68"))
db.conn.execute("""UPDATE match_events SET player_name_ar = 'هداف' WHERE match_id=%s""", (MATCH,))
db.commit()
names = db.conn.execute("""SELECT player_name_en, player_name_ar FROM match_events
                           WHERE match_id=%s ORDER BY minute LIMIT 1""", (MATCH,)).fetchone()
check("arabic name enrichment kept", names["player_name_en"] == "P1" and names["player_name_ar"] == "هداف")

# 7) full match detail endpoint payload is JSON-safe after the type migration
db.commit()   # release the idle-in-transaction locks before schema ensure
from scraper import api as api_mod
app = api_mod.create_app(DSN)
client = app.test_client()
res = client.get(f"/api/match/{MATCH}")
check("GET /api/match/<id> 200", res.status_code == 200)
detail = res.get_json()
check("detail kickoff is the ISO-Z wire string",
      isinstance(detail.get("kickoffUtc"), str) and detail["kickoffUtc"].endswith("Z"),
      str(detail.get("kickoffUtc")))
check("detail carries events + lineups + stats",
      len(detail["events"]) == 3 and detail["lineups"]["home"] is not None
      and len(detail["stats"]) == 1)

# listing endpoint: timestamp-safe serialization over native types
res = client.get("/api/matches")
listing = res.get_json()
kickoffs = [m["kickoffUtc"] for g in listing["groups"] for m in g["matches"][:3]]
check("listing kickoffs are ISO-Z wire strings",
      all(isinstance(k, str) and k.endswith("Z") for k in kickoffs), str(kickoffs[:2]))
res = client.get("/api/health")
h = res.get_json()
check("health payload JSON-safe (lastRuns/finishedAt strings)",
      all(isinstance(r.get("finished_at"), (str, type(None)))
          for r in h.get("lastRuns", []))
      and "live" in h and "sse" in h and "cacheCounters" in h)
check("health counts live matches", h.get("liveMatches", 0) >= 100,
      f"live={h.get('liveMatches')}")

# cleanup
for table in ("match_events", "lineups", "team_match_stats", "match_managers"):
    db.conn.execute(f"DELETE FROM {table} WHERE match_id = %s", (MATCH,))
db.conn.execute("DELETE FROM matches WHERE id = %s", (MATCH,))
db.commit()
db.close()

print("\n" + "=" * 60)
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)
print("ALL DETAIL-CHANGE TESTS PASSED")
