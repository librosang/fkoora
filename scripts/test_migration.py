"""Test the legacy TEXT schema -> native types migration path.

1. creates a scratch database with the ORIGINAL (pre-migration) schema
2. seeds it with TEXT timestamps - including some edge cases:
   values without a Z suffix, an empty string, an invalid match_date
3. runs migrate.migrate_types() and asserts every conversion + repair
4. verifies the data survived with identical semantics (ISO -> instant)
5. runs migrate_types() AGAIN (idempotency) and revert_types() (rollback)
"""
import sys, os
sys.path.insert(0, "/home/z/my-project/kooora")

import psycopg
from psycopg.rows import dict_row
import pgserver

PG_DIR = "/home/z/my-project/testenv/pgdata"
pg = pgserver.get_server(PG_DIR, cleanup_mode=None)
pg.psql("DROP DATABASE IF EXISTS migrate_test")
pg.psql("CREATE DATABASE migrate_test")
base = pg.get_uri()
# postgresql://postgres:@/postgres?host=/path  -> swap the dbname
head, _, tail = base.partition("?")
dsn = head.rsplit("/", 1)[0] + "/migrate_test" + ("?" + tail if tail else "")

legacy = open("/tmp/legacy_schema.sql", encoding="utf-8").read()

with psycopg.connect(dsn, row_factory=dict_row, autocommit=True) as conn:
    # apply the LEGACY schema statement-by-statement
    stripped = [l.split("--")[0] if "--" in l else l for l in legacy.splitlines()]
    for stmt in [s.strip() for s in "\n".join(stripped).split(";")]:
        if stmt:
            conn.execute(stmt)
    # seed legacy TEXT data
    conn.execute("""INSERT INTO competitions (id, name_en, first_seen_at, last_seen_at)
        VALUES ('comp1', 'Premier League', '2026-08-01T00:00:00Z', '2026-08-31T12:00:00Z')""")
    conn.execute("""INSERT INTO teams (id, name_en, first_seen_at, last_seen_at)
        VALUES ('t1', 'Arsenal', '2026-08-01T00:00:00Z', '2026-08-31T12:00:00Z'),
                ('t2', 'Chelsea', '2026-08-01T00:00:00Z', '2026-08-31T12:00:00Z')""")
    # kickoff WITH Z, kickoff WITHOUT Z (UTC assumed), invalid match_date
    # (repaired from kickoff prefix), empty listed_date (-> NULL)
    conn.execute("""INSERT INTO matches (id, competition_id, kickoff_utc, match_date,
        listed_date, status, home_team_id, away_team_id, first_seen_at, last_seen_at2)
        VALUES
        ('m1', 'comp1', '2026-08-31T17:20:00Z', '2026-08-31', '2026-08-31', 'LIVE',
         't1', 't2', '2026-08-01T00:00:00Z', '2026-08-31T12:00:00Z'),
        ('m2', 'comp1', '2026-08-31 19:00:00', '31/08/2026', '', 'FIXTURE',
         't2', 't1', '2026-08-01T00:00:00Z', '2026-08-31T12:00:00Z')""")
    conn.execute("""INSERT INTO match_events (match_id, team_side, event_type, minute, sort_order)
        VALUES ('m1', 'home', 'GOAL', 67, 0)""")
    conn.execute("""INSERT INTO refresh_jobs (kind, ref, requested_at)
        VALUES ('day_listing', '2026-08-31', '2026-08-31T00:00:00Z')""")
    print("legacy data seeded (TEXT columns)")

from scraper.db import migrate
from scraper.db import backend
from scraper.db.database import SCHEMA_PATH

with psycopg.connect(dsn, row_factory=dict_row) as conn:
    # production flow: schema.sql first (idempotent - adds data_version +
    # indexes to the legacy DB), THEN the type migration
    backend.run_script(conn, open(SCHEMA_PATH, encoding="utf-8").read())
    assert migrate.needs_migration(conn), "should need migration"
    report = migrate.migrate_types(conn)
    print("\n-- migration report --")
    for k in ("converted", "repaired", "failed"):
        for line in report.get(k, []):
            print(f"  [{k}] {line}")
    assert not report["failed"], f"migration failures: {report['failed']}"
    assert len(report["converted"]) >= 24, "should have converted 24+ columns"

    # types now native
    types = {r["column_name"]: r["data_type"] for r in conn.execute(
        """SELECT column_name, data_type FROM information_schema.columns
           WHERE table_name = 'matches'""").fetchall()}
    assert types["kickoff_utc"] == "timestamp with time zone", types
    assert types["match_date"] == "date", types
    assert types["listed_date"] == "date", types
    print("\nnative types confirmed:", types["kickoff_utc"], types["match_date"])

    # data survived with correct semantics
    rows = conn.execute("""SELECT id, kickoff_utc, match_date, listed_date, data_version
                           FROM matches ORDER BY id""").fetchall()
    r1, r2 = rows
    assert r1["kickoff_utc"].isoformat() == "2026-08-31T17:20:00+00:00", r1["kickoff_utc"]
    assert str(r1["match_date"]) == "2026-08-31"
    assert r1["data_version"] == 1
    # m2: '2026-08-31 19:00:00' (no offset) interpreted as UTC; bad match_date
    # repaired from the kickoff prefix; empty listed_date became NULL
    assert r2["kickoff_utc"].isoformat() == "2026-08-31T19:00:00+00:00", r2["kickoff_utc"]
    assert str(r2["match_date"]) == "2026-08-31", r2["match_date"]
    assert r2["listed_date"] is None, r2["listed_date"]
    print("data survived: m1", r1["kickoff_utc"], "| m2", r2["kickoff_utc"], r2["match_date"])

    # idempotency: second run is a no-op
    report2 = migrate.migrate_types(conn)
    assert not report2["converted"] and not report2["failed"], report2
    assert len(report2["already_native"]) >= 24
    print("idempotent re-run: no conversions,", len(report2["already_native"]), "already native")

    # revert (rollback path) restores the exact TEXT wire format
    rev = migrate.revert_types(conn)
    assert not rev["failed"], rev
    assert len(rev["reverted"]) >= 24
    back = conn.execute("""SELECT kickoff_utc, match_date FROM matches WHERE id='m1'""").fetchone()
    assert back["kickoff_utc"] == "2026-08-31T17:20:00Z", back
    assert back["match_date"] == "2026-08-31"
    print("revert restored wire format:", back["kickoff_utc"])

    # and migrating again still works (round trip)
    report3 = migrate.migrate_types(conn)
    assert len(report3["converted"]) >= 24 and not report3["failed"]
    print("re-migration after revert OK")

print("\nALL MIGRATION TESTS PASSED")
