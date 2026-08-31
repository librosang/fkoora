"""Seed realistic test data into a scratch database: 10,000+ matches
spread over ~2 years, 100 currently LIVE, events/lineups/stats on live
matches, teams + competitions + venues.

Target DB: FOOTBALL_DB_URL (the sandbox test instance). All IDs carry a
"ztest-" prefix so the data is unmistakably synthetic and deletable:

    python3 scripts/seed_test_data.py            # seed (idempotent-ish)
    python3 scripts/seed_test_data.py --wipe     # remove ztest-* rows
"""
import argparse
import os
import random
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "kooora"))

random.seed(20260831)

N_TEAMS = 120
N_COMPS = 12
N_DAYS = 730
N_LIVE = 100
PREFIX = "ztest-"

STATUS_VOCAB = ["FIXTURE", "RESULT", "LIVE", "RESULT", "RESULT", "AET", "PEN",
                "CANCELLED", "FIXTURE"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wipe", action="store_true", help="delete ztest-* data")
    args = ap.parse_args()

    from scraper.db import backend
    from scraper.db.database import SCHEMA_PATH
    dsn = os.environ.get("FOOTBALL_DB_URL")
    assert dsn, "set FOOTBALL_DB_URL first"

    now = datetime.now(timezone.utc).replace(microsecond=0)

    # apply the schema first (idempotent CREATE IF NOT EXISTS)
    with backend.connect(dsn) as conn:
        backend.run_script(conn, open(SCHEMA_PATH, encoding="utf-8").read())
    print("schema applied")

    with backend.connection(dsn, pooled=False) as conn:
        if args.wipe:
            for table, col in (("match_events", "match_id"),
                               ("lineups", "match_id"),
                               ("team_match_stats", "match_id"),
                               ("match_managers", "match_id"),
                               ("matches", "id")):
                conn.execute(f"DELETE FROM {table} WHERE {col} LIKE %s", (PREFIX + "%",))
            conn.execute("DELETE FROM teams WHERE id LIKE %s", (PREFIX + "%",))
            conn.execute("DELETE FROM competitions WHERE id LIKE %s", (PREFIX + "%",))
            conn.execute("DELETE FROM venues WHERE name_en LIKE %s", (PREFIX + "%",))
            print("wiped ztest-* data")
            return

        # ---- competitions ------------------------------------------------------
        comps = []
        for i in range(N_COMPS):
            cid = f"{PREFIX}comp{i}"
            comps.append(cid)
            conn.execute(
                """INSERT INTO competitions (id, name_en, name_ar, area_name_en,
                       area_code, first_seen_at, last_seen_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO UPDATE SET last_seen_at = EXCLUDED.last_seen_at""",
                (cid, f"Test League {i}", f"دوري التجربة {i}",
                 "Testland", "TST", now, now))

        # ---- teams ---------------------------------------------------------------
        teams = []
        for i in range(N_TEAMS):
            tid = f"{PREFIX}team{i}"
            teams.append(tid)
            conn.execute(
                """INSERT INTO teams (id, name_en, name_ar, short_name_en, code,
                       first_seen_at, last_seen_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO NOTHING""",
                (tid, f"Team {i}", f"فريق {i}", f"T{i:03d}", f"T{i%26:02d}", now, now))

        # ---- players (lineup identities reference them) ---------------------------
        for p in range(40):
            conn.execute(
                """INSERT INTO players (id, name_en, name_ar, first_seen_at, last_seen_at)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO NOTHING""",
                (f"{PREFIX}player{p}", f"Test Player {p}", f"لاعب {p}", now, now))

        # ---- venue ---------------------------------------------------------------
        conn.execute(
            """INSERT INTO venues (name_en, name_ar) VALUES (%s, %s)
               ON CONFLICT (name_en) DO NOTHING""",
            (PREFIX + "Arena", "ملعب التجربة"))
        venue = conn.execute(
            "SELECT id FROM venues WHERE name_en = %s", (PREFIX + "Arena",)).fetchone()["id"]

        # ---- matches: ~2 years of days, several per day ---------------------------
        # today's live block first: 100 matches LIVE right now
        today = now.date()
        total = 0
        live_ids = []
        for i in range(N_LIVE):
            mid = f"{PREFIX}match-live{i:04d}"
            live_ids.append(mid)
            kickoff = now - timedelta(minutes=random.randint(5, 80))
            conn.execute(
                """INSERT INTO matches (id, competition_id, kickoff_utc, match_date,
                       listed_date, status, period, round_name, home_team_id,
                       away_team_id, venue_id, home_score, away_score,
                       home_red_cards, away_red_cards, data_version,
                       first_seen_at, last_seen_at2)
                   VALUES (%s, %s, %s, %s, %s, 'LIVE', %s, %s, %s, %s, %s,
                           %s, %s, %s, %s, 1, %s, %s)
                   ON CONFLICT (id) DO NOTHING""",
                (mid, comps[i % N_COMPS], kickoff, kickoff.date(),
                 kickoff.date(), f"LIVE {random.randint(10, 85)}",
                 f"Round {1 + i % 34}",
                 teams[(i * 7) % N_TEAMS], teams[(i * 13 + 60) % N_TEAMS], venue,
                 random.randint(0, 4), random.randint(0, 3),
                 random.randint(0, 1), random.randint(0, 1),
                 now, now))
            total += 1
            # a goal event + lineup entries for each live match
            conn.execute(
                """INSERT INTO match_events (match_id, team_side, event_type, minute,
                       player_name_en, player_name_ar, home_score_after,
                       away_score_after, sort_order)
                   VALUES (%s, 'home', 'GOAL', %s, %s, %s, 1, 0, 0)""",
                (mid, random.randint(1, 60), "Test Scorer", "هداف التجربة"))
            for side_i, tid in enumerate((teams[(i * 7) % N_TEAMS],
                                          teams[(i * 13 + 60) % N_TEAMS])):
                for p in range(11):
                    conn.execute(
                        """INSERT INTO lineups (match_id, team_id, player_id,
                               is_starter, shirt_number)
                           VALUES (%s, %s, %s, 1, %s)
                           ON CONFLICT DO NOTHING""",
                        (mid, tid, f"{PREFIX}player{p}", p + 1))

        # history + future across N_DAYS
        for day_off in range(-N_DAYS, 30):
            day = today + timedelta(days=day_off)
            per_day = random.randint(8, 20)
            for j in range(per_day):
                mid = f"{PREFIX}match{day.strftime('%Y%m%d')}-{j:03d}"
                hour = random.randint(0, 23)
                kickoff = datetime(day.year, day.month, day.day, hour,
                                   random.choice([0, 15, 30, 45]), tzinfo=timezone.utc)
                if day_off < 0:
                    status = random.choice(STATUS_VOCAB)
                    if status == "LIVE":
                        status = "RESULT"       # history is never LIVE
                elif day_off == 0:
                    status = "FIXTURE" if j >= N_LIVE else "LIVE"
                else:
                    status = "FIXTURE"
                hs, as_ = (random.randint(0, 5), random.randint(0, 5)) \
                    if status in ("RESULT", "AET", "PEN") else (None, None)
                conn.execute(
                    """INSERT INTO matches (id, competition_id, kickoff_utc, match_date,
                           listed_date, status, round_name, home_team_id, away_team_id,
                           venue_id, home_score, away_score, data_version,
                           first_seen_at, last_seen_at2)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s)
                       ON CONFLICT (id) DO NOTHING""",
                    (mid, comps[j % N_COMPS], kickoff, kickoff.date(),
                     kickoff.date(), status, f"Round {1 + j % 34}",
                     teams[(j * 3 + day_off) % N_TEAMS],
                     teams[(j * 11 + 5) % N_TEAMS], venue, hs, as_, now, now))
                total += 1

        counts = conn.execute(
            "SELECT COUNT(*) AS n FROM matches WHERE id LIKE %s", (PREFIX + "%",)
        ).fetchone()["n"]
        print(f"seeded: {counts} matches ({N_LIVE} live), {N_TEAMS} teams, "
              f"{N_COMPS} competitions (prefix {PREFIX!r})")
        assert counts >= 10_000, f"expected 10k+ matches, got {counts}"


if __name__ == "__main__":
    main()
