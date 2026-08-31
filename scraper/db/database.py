"""Database layer: connection management + upsert helpers (PostgreSQL).

Every write is idempotent (INSERT ... ON CONFLICT ... DO UPDATE), so re-scraping
a date or a match never creates duplicates - it just refreshes the data and
fills in previously-missing columns (e.g. Arabic names from kooora).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg

from .. import config
from . import backend
from . import migrate as _migrate

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _values_differ(old: Any, new: Any) -> bool:
    """Compare a stored column value with an incoming scraper value.

    Robust across the TEXT -> TIMESTAMPTZ/DATE migration: the stored side may
    be a ``datetime``/``date`` object while the incoming side is the ISO
    string the parsers produce (both normalize to the same wire string via
    ``timeutil``). None == None; NULL-into-value and value-into-NULL count as
    changes; numbers compare by value (int 2 == float 2.0).
    """
    if old is None and new is None:
        return False
    if old is None or new is None:
        return True
    if isinstance(old, (datetime, date)) or isinstance(new, (datetime, date)):
        from ..timeutil import iso_z, iso_date
        if isinstance(old, (datetime, date)) and isinstance(new, str):
            return (iso_z(old) if isinstance(old, datetime) else iso_date(old)) != new.strip()
        if isinstance(new, (datetime, date)) and isinstance(old, str):
            return (iso_z(new) if isinstance(new, datetime) else iso_date(new)) != old.strip()
        # both datetime/date
        return iso_z(old) != iso_z(new)
    if isinstance(old, (int, float)) and isinstance(new, (int, float)):
        return old != new
    return str(old) != str(new)


class Database:
    def __init__(self, db_url: Optional[str] = None):
        self.db_url = backend.resolve_dsn(db_url)
        self.conn: psycopg.Connection = backend.connect(self.db_url)
        self._init_schema()
        # competitions that had a match transition into an ended status
        # (LIVE/UPCOMING -> RESULT/AET/PEN) during writes on THIS connection.
        # The API drains this set after each scrape to refresh the affected
        # league's standings right away - a table only ever changes when one
        # of its matches ends (see config.MATCH_ENDED_STATUSES).
        self.newly_finished_comps: set = set()
        # matches whose client-visible data changed during writes on THIS
        # connection, with the data_version the change produces. The worker
        # drains this AFTER commit and publishes SSE / Redis live updates
        # for exactly these matches (unchanged matches never broadcast).
        #   {match_id: {"version": int, "fields": [..], "inserted": bool}}
        self.changed_matches: Dict[str, Dict[str, Any]] = {}
        # new match events (goals/cards/...) observed during a detail write:
        #   {match_id: [{"eventType":.., "minute":.., "teamSide":..,
        #                "playerId":.., "playerNameEn":.., ...}, ...]}
        # drained by the worker into `match.event` SSE messages.
        self.new_match_events: Dict[str, List[Dict[str, Any]]] = {}

    # ------------------------------------------------------------------
    def _init_schema(self) -> None:
        with open(SCHEMA_PATH, encoding="utf-8") as fh:
            backend.run_script(self.conn, fh.read())
        # TEXT -> TIMESTAMPTZ/DATE conversion for databases created before
        # the typed schema (idempotent: no-op when already converted).
        try:
            _migrate.migrate_types(self.conn)
        except Exception as exc:  # noqa: BLE001 - serving must not break
            log.warning("type migration deferred: %s", exc)

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def commit(self) -> None:
        self.conn.commit()

    # ==================================================================
    # competitions
    # ==================================================================
    def upsert_competition(self, comp: Dict[str, Any]) -> None:
        now = utcnow()
        self.conn.execute(
            """
            INSERT INTO competitions (id, name_en, name_ar, area_name_en, area_name_ar,
                                      area_code, image_url, first_seen_at, last_seen_at)
            VALUES (%(id)s, %(name_en)s, %(name_ar)s, %(area_name_en)s, %(area_name_ar)s,
                    %(area_code)s, %(image_url)s, %(now)s, %(now)s)
            ON CONFLICT(id) DO UPDATE SET
                name_en      = COALESCE(excluded.name_en,      competitions.name_en),
                name_ar      = COALESCE(excluded.name_ar,      competitions.name_ar),
                area_name_en = COALESCE(excluded.area_name_en, competitions.area_name_en),
                area_name_ar = COALESCE(excluded.area_name_ar, competitions.area_name_ar),
                area_code    = COALESCE(excluded.area_code,    competitions.area_code),
                image_url    = COALESCE(excluded.image_url,    competitions.image_url),
                last_seen_at = excluded.last_seen_at
            """,
            {
                "id": comp.get("id"),
                "name_en": comp.get("name_en"),
                "name_ar": comp.get("name_ar"),
                "area_name_en": comp.get("area_name_en"),
                "area_name_ar": comp.get("area_name_ar"),
                "area_code": comp.get("area_code"),
                "image_url": comp.get("image_url"),
                "now": now,
            },
        )

    # ==================================================================
    # seasons
    # ==================================================================
    def upsert_season(self, season: Dict[str, Any]) -> None:
        if not season.get("id"):
            return
        now = utcnow()
        self.conn.execute(
            """
            INSERT INTO seasons (id, competition_id, name, is_active, first_seen_at, last_seen_at)
            VALUES (%(id)s, %(competition_id)s, %(name)s, %(is_active)s, %(now)s, %(now)s)
            ON CONFLICT(id) DO UPDATE SET
                competition_id = excluded.competition_id,
                name           = COALESCE(excluded.name, seasons.name),
                is_active      = GREATEST(excluded.is_active, seasons.is_active),
                last_seen_at   = excluded.last_seen_at
            """,
            {
                "id": season["id"],
                "competition_id": season.get("competition_id"),
                "name": season.get("name"),
                "is_active": season.get("is_active", 0),
                "now": now,
            },
        )

    # ==================================================================
    # teams
    # ==================================================================
    def upsert_team(self, team: Dict[str, Any]) -> None:
        if not team.get("id"):
            return
        now = utcnow()
        self.conn.execute(
            """
            INSERT INTO teams (id, name_en, short_name_en, name_ar, code, crest_url,
                               first_seen_at, last_seen_at)
            VALUES (%(id)s, %(name_en)s, %(short_name_en)s, %(name_ar)s, %(code)s,
                    %(crest_url)s, %(now)s, %(now)s)
            ON CONFLICT(id) DO UPDATE SET
                name_en       = COALESCE(excluded.name_en,       teams.name_en),
                short_name_en = COALESCE(excluded.short_name_en, teams.short_name_en),
                name_ar       = COALESCE(excluded.name_ar,       teams.name_ar),
                code          = COALESCE(excluded.code,          teams.code),
                crest_url     = COALESCE(excluded.crest_url,     teams.crest_url),
                last_seen_at  = excluded.last_seen_at
            """,
            {
                "id": team["id"],
                "name_en": team.get("name_en"),
                "short_name_en": team.get("short_name_en"),
                "name_ar": team.get("name_ar"),
                "code": team.get("code"),
                "crest_url": team.get("crest_url"),
                "now": now,
            },
        )

    # ==================================================================
    # players
    # ==================================================================
    def upsert_player(self, player: Dict[str, Any]) -> None:
        if not player.get("id"):
            return
        now = utcnow()
        self.conn.execute(
            """
            INSERT INTO players (id, name_en, name_ar, image_url, is_verified,
                                 full_name_en, full_name_ar, slug_en, slug_ar,
                                 position, shirt_number, height_cm, weight_kg,
                                 birth_date, age, nationality_en, nationality_ar,
                                 country_of_birth_en, country_of_birth_ar,
                                 place_of_birth_en, place_of_birth_ar,
                                 current_club_id, current_club_name_en,
                                 current_club_name_ar, profile_fetched_at,
                                 first_seen_at, last_seen_at)
            VALUES (%(id)s, %(name_en)s, %(name_ar)s, %(image_url)s, %(verified)s,
                    %(full_name_en)s, %(full_name_ar)s, %(slug_en)s, %(slug_ar)s,
                    %(position)s, %(shirt_number)s, %(height_cm)s, %(weight_kg)s,
                    %(birth_date)s, %(age)s, %(nationality_en)s, %(nationality_ar)s,
                    %(country_of_birth_en)s, %(country_of_birth_ar)s,
                    %(place_of_birth_en)s, %(place_of_birth_ar)s,
                    %(current_club_id)s, %(current_club_name_en)s,
                    %(current_club_name_ar)s, %(profile_fetched_at)s,
                    %(now)s, %(now)s)
            ON CONFLICT(id) DO UPDATE SET
                name_en      = COALESCE(excluded.name_en,      players.name_en),
                name_ar      = COALESCE(excluded.name_ar,      players.name_ar),
                image_url    = COALESCE(excluded.image_url,    players.image_url),
                is_verified  = GREATEST(excluded.is_verified,  players.is_verified),
                full_name_en = COALESCE(excluded.full_name_en, players.full_name_en),
                full_name_ar = COALESCE(excluded.full_name_ar, players.full_name_ar),
                slug_en      = COALESCE(excluded.slug_en,     players.slug_en),
                slug_ar      = COALESCE(excluded.slug_ar,     players.slug_ar),
                position     = COALESCE(excluded.position,    players.position),
                shirt_number = COALESCE(excluded.shirt_number, players.shirt_number),
                height_cm    = COALESCE(excluded.height_cm,   players.height_cm),
                weight_kg    = COALESCE(excluded.weight_kg,   players.weight_kg),
                birth_date   = COALESCE(excluded.birth_date,  players.birth_date),
                age          = COALESCE(excluded.age,         players.age),
                nationality_en = COALESCE(excluded.nationality_en, players.nationality_en),
                nationality_ar = COALESCE(excluded.nationality_ar, players.nationality_ar),
                country_of_birth_en = COALESCE(excluded.country_of_birth_en, players.country_of_birth_en),
                country_of_birth_ar = COALESCE(excluded.country_of_birth_ar, players.country_of_birth_ar),
                place_of_birth_en = COALESCE(excluded.place_of_birth_en, players.place_of_birth_en),
                place_of_birth_ar = COALESCE(excluded.place_of_birth_ar, players.place_of_birth_ar),
                current_club_id      = COALESCE(excluded.current_club_id,      players.current_club_id),
                current_club_name_en = COALESCE(excluded.current_club_name_en, players.current_club_name_en),
                current_club_name_ar = COALESCE(excluded.current_club_name_ar, players.current_club_name_ar),
                profile_fetched_at = COALESCE(excluded.profile_fetched_at, players.profile_fetched_at),
                last_seen_at = excluded.last_seen_at
            """,
            {
                "id": player["id"],
                "name_en": player.get("name_en"),
                "name_ar": player.get("name_ar"),
                "image_url": player.get("image_url"),
                "verified": 1 if player.get("verified") else 0,
                "full_name_en": player.get("full_name_en"),
                "full_name_ar": player.get("full_name_ar"),
                "slug_en": player.get("slug_en"),
                "slug_ar": player.get("slug_ar"),
                "position": player.get("position"),
                "shirt_number": player.get("shirt_number"),
                "height_cm": player.get("height_cm"),
                "weight_kg": player.get("weight_kg"),
                "birth_date": player.get("birth_date"),
                "age": player.get("age"),
                "nationality_en": player.get("nationality_en"),
                "nationality_ar": player.get("nationality_ar"),
                "country_of_birth_en": player.get("country_of_birth_en"),
                "country_of_birth_ar": player.get("country_of_birth_ar"),
                "place_of_birth_en": player.get("place_of_birth_en"),
                "place_of_birth_ar": player.get("place_of_birth_ar"),
                "current_club_id": player.get("current_club_id"),
                "current_club_name_en": player.get("current_club_name_en"),
                "current_club_name_ar": player.get("current_club_name_ar"),
                "profile_fetched_at": player.get("profile_fetched_at"),
                "now": now,
            },
        )

    def replace_player_career(self, player_id: str,
                              career_rows: List[Dict[str, Any]]) -> int:
        """Replace the player's career history wholesale (idempotent re-scrape).

        Goal.com returns the full career list on every page load, so the
        cleanest approach is delete-then-insert. The UNIQUE constraint on
        (player_id, team_id, season_name, competition_id) protects against
        accidental duplicates within one payload.
        """
        if not player_id:
            return 0
        with self.conn.cursor() as cur:
            cur.execute(
                "DELETE FROM player_career_entries WHERE player_id = %s",
                (player_id,),
            )
            for r in career_rows:
                cur.execute(
                    """
                    INSERT INTO player_career_entries (
                        player_id, team_id, team_name_en, team_name_ar,
                        season_name, competition_id, competition_name_en,
                        competition_name_ar, appearances, goals, assists,
                        yellow_cards, red_cards, minutes_played, is_loan, sort_order
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (player_id, team_id, season_name, competition_id)
                    DO UPDATE SET
                        team_name_en = COALESCE(excluded.team_name_en, player_career_entries.team_name_en),
                        team_name_ar = COALESCE(excluded.team_name_ar, player_career_entries.team_name_ar),
                        competition_name_en = COALESCE(excluded.competition_name_en, player_career_entries.competition_name_en),
                        competition_name_ar = COALESCE(excluded.competition_name_ar, player_career_entries.competition_name_ar),
                        appearances   = COALESCE(excluded.appearances, player_career_entries.appearances),
                        goals         = COALESCE(excluded.goals, player_career_entries.goals),
                        assists       = COALESCE(excluded.assists, player_career_entries.assists),
                        yellow_cards  = COALESCE(excluded.yellow_cards, player_career_entries.yellow_cards),
                        red_cards     = COALESCE(excluded.red_cards, player_career_entries.red_cards),
                        minutes_played = COALESCE(excluded.minutes_played, player_career_entries.minutes_played),
                        is_loan       = excluded.is_loan,
                        sort_order    = excluded.sort_order
                    """,
                    (player_id,
                     r.get("team_id"), r.get("team_name_en"), r.get("team_name_ar"),
                     r.get("season_name"), r.get("competition_id"),
                     r.get("competition_name_en"), r.get("competition_name_ar"),
                     r.get("appearances"), r.get("goals"), r.get("assists"),
                     r.get("yellow_cards"), r.get("red_cards"),
                     r.get("minutes_played"),
                     1 if r.get("is_loan") else 0,
                     r.get("sort_order", 0)),
                )
        return len(career_rows)

    def apply_player_profile(self, profile: Dict[str, Any]) -> None:
        """Persist a player profile payload (bio + career) in one call."""
        profile = {**profile,
                   "profile_fetched_at": utcnow()}
        self.upsert_player(profile)
        self.replace_player_career(profile["id"], profile.get("career") or [])

    # ==================================================================
    # venues  (deduplicated by English name)
    # ==================================================================
    def get_or_create_venue(
        self, name_en: Optional[str], name_ar: Optional[str] = None,
        latitude: Optional[float] = None, longitude: Optional[float] = None,
    ) -> Optional[int]:
        venue_id: Optional[int] = None

        if name_en:
            row = self.conn.execute(
                "SELECT id FROM venues WHERE name_en = %s", (name_en,)
            ).fetchone()
            if row:
                venue_id = row["id"]
        if venue_id is None and name_ar:
            row = self.conn.execute(
                "SELECT id FROM venues WHERE name_ar = %s AND name_en IS NULL", (name_ar,)
            ).fetchone()
            if row:
                venue_id = row["id"]

        if venue_id is not None:
            self.conn.execute(
                """UPDATE venues SET
                       name_en   = COALESCE(%s, name_en),
                       name_ar   = COALESCE(%s, name_ar),
                       latitude  = COALESCE(%s, latitude),
                       longitude = COALESCE(%s, longitude)
                   WHERE id = %s""",
                (name_en, name_ar, latitude, longitude, venue_id),
            )
            return venue_id

        if not name_en and not name_ar:
            return None

        row = self.conn.execute(
            """INSERT INTO venues (name_en, name_ar, latitude, longitude)
               VALUES (%s, %s, %s, %s) RETURNING id""",
            (name_en, name_ar, latitude, longitude),
        ).fetchone()
        return row["id"] if row else None

    # ==================================================================
    # matches
    # ==================================================================
    # fields that make a listing update MEANINGFUL for a client: a change in
    # any of these is what the live layer broadcasts (see live.py). Everything
    # else (slugs, bookkeeping timestamps, venue links) is not client-visible
    # live state and never triggers a broadcast.
    _LIVE_COMPARE_FIELDS = (
        "status", "period", "kickoff_utc", "match_date",
        "home_score", "away_score",
        "home_agg_score", "away_agg_score",
        "home_red_cards", "away_red_cards",
    )

    def upsert_match_from_listing(self, row: Dict[str, Any], listed_date: str) -> None:
        """Upsert a match row coming from a fixtures listing (either site)."""
        kickoff = row.get("kickoff_utc") or ""
        match_date = kickoff[:10] if len(kickoff) >= 10 else listed_date
        now = utcnow()

        # current row (if any) - powers BOTH the match-end event below AND
        # the change detection that decides whether this upsert bumps
        # data_version / gets published to the live channel. One SELECT,
        # both jobs.
        prev = self.conn.execute(
            """SELECT status, period, kickoff_utc, match_date,
                      home_score, away_score, home_agg_score, away_agg_score,
                      home_red_cards, away_red_cards, data_version
               FROM matches WHERE id = %s""",
            (row["match_id"],),
        ).fetchone()

        # match-end event: a transition into an ended status is the moment a
        # league table changes - remember the competition so the caller can
        # refresh its standings immediately instead of on the next warm tick.
        if (row.get("status") or "").upper() in config.MATCH_ENDED_STATUSES:
            if prev is None or (prev["status"] or "").upper() not in config.MATCH_ENDED_STATUSES:
                self.newly_finished_comps.add(row["competition"]["id"])

        # ---- change detection (only meaningful fields, COALESCE semantics) --
        # The upsert overwrites the row with the NEW values where the new
        # value is non-NULL; a NULL new value keeps the old one. Compute the
        # EFFECTIVE new state and compare - an unchanged provider response
        # ("Real Madrid 2-1 Barcelona, 67'" twice) must NOT bump the version
        # and must NOT broadcast anything.
        new_values = {
            "status": row.get("status") or "UNKNOWN",
            "period": row.get("period"),
            "kickoff_utc": kickoff,
            "match_date": match_date,
            "home_score": row.get("home_score"),
            "away_score": row.get("away_score"),
            "home_agg_score": row.get("home_agg_score"),
            "away_agg_score": row.get("away_agg_score"),
            "home_red_cards": row.get("home_red_cards", 0),
            "away_red_cards": row.get("away_red_cards", 0),
        }
        version_bump = 0
        changed_fields: List[str] = []
        if prev is None:
            version_bump = 1
            changed_fields = ["__insert__"]
        else:
            for field in self._LIVE_COMPARE_FIELDS:
                old_v = prev[field]
                new_v = new_values[field]
                # COALESCE: a NULL new value keeps the stored one
                eff_v = new_v if new_v is not None else old_v
                if _values_differ(old_v, eff_v):
                    changed_fields.append(field)
            if changed_fields:
                version_bump = 1

        # venue handling: resolve by ENGLISH name only here. Arabic-only venue
        # names must NOT create a row (that could duplicate an existing
        # EN-named venue) - update_match_venue_ar attaches them instead.
        name_en = row.get("venue_name_en")
        venue_id = self.get_or_create_venue(name_en, row.get("venue_name_ar")) if name_en else None

        self.conn.execute(
            """
            INSERT INTO matches (
                id, competition_id, kickoff_utc, match_date, listed_date, status,
                period, round_name, gameset_name, gameset_name_ar, gameset_id,
                gameset_is_knockout,
                home_team_id, away_team_id, venue_id,
                home_score, away_score, home_agg_score, away_agg_score,
                home_red_cards, away_red_cards, slug_en, slug_ar, last_updated_at,
                data_version,
                first_seen_at, last_seen_at2
            ) VALUES (
                %(match_id)s, %(competition_id)s, %(kickoff_utc)s, %(match_date)s,
                %(listed_date)s, %(status)s,
                %(period)s, %(round_name)s, %(gameset_name)s, %(gameset_name_ar)s,
                %(gameset_id)s,
                %(gameset_is_knockout)s,
                %(home_team_id)s, %(away_team_id)s, %(venue_id)s,
                %(home_score)s, %(away_score)s, %(home_agg_score)s, %(away_agg_score)s,
                %(home_red_cards)s, %(away_red_cards)s, %(slug_en)s, %(slug_ar)s,
                %(last_updated_at)s,
                %(version_bump)s,
                %(now)s, %(now)s
            )
            ON CONFLICT(id) DO UPDATE SET
                competition_id   = excluded.competition_id,
                kickoff_utc      = excluded.kickoff_utc,
                match_date       = excluded.match_date,
                status           = excluded.status,
                period           = COALESCE(excluded.period, matches.period),
                round_name       = COALESCE(excluded.round_name, matches.round_name),
                gameset_name     = COALESCE(excluded.gameset_name, matches.gameset_name),
                gameset_name_ar  = COALESCE(excluded.gameset_name_ar, matches.gameset_name_ar),
                gameset_id       = COALESCE(excluded.gameset_id, matches.gameset_id),
                gameset_is_knockout = GREATEST(excluded.gameset_is_knockout,
                                               matches.gameset_is_knockout),
                venue_id         = COALESCE(excluded.venue_id, matches.venue_id),
                home_score       = COALESCE(excluded.home_score, matches.home_score),
                away_score       = COALESCE(excluded.away_score, matches.away_score),
                home_agg_score   = COALESCE(excluded.home_agg_score, matches.home_agg_score),
                away_agg_score   = COALESCE(excluded.away_agg_score, matches.away_agg_score),
                home_red_cards   = COALESCE(excluded.home_red_cards, matches.home_red_cards),
                away_red_cards   = COALESCE(excluded.away_red_cards, matches.away_red_cards),
                slug_en          = COALESCE(excluded.slug_en, matches.slug_en),
                slug_ar          = COALESCE(excluded.slug_ar, matches.slug_ar),
                last_updated_at  = COALESCE(excluded.last_updated_at, matches.last_updated_at),
                data_version     = matches.data_version + excluded.data_version,
                last_seen_at2    = excluded.last_seen_at2
            """,
            {
                "match_id": row["match_id"],
                "competition_id": row["competition"]["id"],
                "kickoff_utc": kickoff,
                "match_date": match_date,
                "listed_date": listed_date,
                "status": row.get("status") or "UNKNOWN",
                "period": row.get("period"),
                "round_name": row.get("round_name"),
                "gameset_name": row.get("gameset_name"),
                "gameset_name_ar": row.get("gameset_name_ar"),
                "gameset_id": row.get("gameset_id"),
                "gameset_is_knockout": row.get("gameset_is_knockout", 0),
                "home_team_id": row["home_team"]["id"],
                "away_team_id": row["away_team"]["id"],
                "venue_id": venue_id,
                "home_score": row.get("home_score"),
                "away_score": row.get("away_score"),
                "home_agg_score": row.get("home_agg_score"),
                "away_agg_score": row.get("away_agg_score"),
                "home_red_cards": row.get("home_red_cards", 0),
                "away_red_cards": row.get("away_red_cards", 0),
                "slug_en": row.get("slug_en"),
                "slug_ar": row.get("slug_ar"),
                "last_updated_at": row.get("last_updated_at"),
                "version_bump": version_bump,
                "now": now,
            },
        )

        if version_bump:
            new_version = (prev["data_version"] if prev else 0) + 1
            entry = self.changed_matches.get(row["match_id"]) or {
                "version": new_version, "fields": [], "inserted": prev is None,
            }
            entry["version"] = new_version
            entry["fields"] = sorted(set(entry["fields"] + changed_fields))
            entry["inserted"] = entry["inserted"] or prev is None
            self.changed_matches[row["match_id"]] = entry

    def update_match_venue_ar(self, match_id: str, venue_name_ar: Optional[str]) -> None:
        """Attach an Arabic venue name to the match's venue row."""
        if not venue_name_ar:
            return
        row = self.conn.execute(
            "SELECT venue_id FROM matches WHERE id = %s", (match_id,)
        ).fetchone()
        if row and row["venue_id"]:
            self.conn.execute(
                "UPDATE venues SET name_ar = COALESCE(%s, name_ar) WHERE id = %s",
                (venue_name_ar, row["venue_id"]),
            )
        else:
            vid = self.get_or_create_venue(None, venue_name_ar)
            if vid:
                self.conn.execute(
                    "UPDATE matches SET venue_id = %s WHERE id = %s", (vid, match_id)
                )

    # ==================================================================
    # match detail (scores breakdown, events, lineups, stats, managers)
    # ==================================================================
    def apply_match_detail(self, detail: Dict[str, Any]) -> None:
        match_id = detail["match_id"]

        # competition & season
        if detail.get("competition", {}).get("id"):
            self.upsert_competition(detail["competition"])
        season = dict(detail.get("season") or {})
        season.setdefault("id", None)
        if season.get("id"):
            season["competition_id"] = detail["competition"]["id"]
            self.upsert_season(season)

        # ---- venue (bilingual + coordinates) ----------------------------------
        venue = detail.get("venue") or {}
        existing = self.conn.execute(
            """SELECT venue_id, competition_id, status, period, referee,
                      home_score, away_score, home_agg_score, away_agg_score,
                      home_pen_score, away_pen_score,
                      home_score_ht, away_score_ht,
                      home_score_ft, away_score_ft,
                      home_score_et, away_score_et,
                      lineups_confirmed, home_formation, away_formation,
                      detail_fetched_at, data_version
               FROM matches WHERE id = %s""",
            (match_id,),
        ).fetchone()
        # match-end event (same logic as upsert_match_from_listing): detail
        # enrichment is often what first observes a live match has finished,
        # because live matches are re-fetched every couple of minutes
        if existing is not None \
                and (detail.get("status") or "").upper() in config.MATCH_ENDED_STATUSES \
                and (existing["status"] or "").upper() not in config.MATCH_ENDED_STATUSES:
            self.newly_finished_comps.add(existing["competition_id"])
        if venue.get("name_en"):
            # resolve by the English name; fills Arabic name + coordinates
            vid = self.get_or_create_venue(
                venue.get("name_en"), venue.get("name_ar"),
                venue.get("latitude"), venue.get("longitude"),
            )
            if vid:
                self.conn.execute(
                    "UPDATE matches SET venue_id = %s WHERE id = %s", (vid, match_id)
                )
        elif existing and existing["venue_id"] and (
            venue.get("name_ar") or venue.get("latitude") or venue.get("longitude")
        ):
            # no EN name - enrich the venue already linked to this match
            self.conn.execute(
                "UPDATE venues SET name_ar = COALESCE(%s, name_ar), "
                "latitude = COALESCE(%s, latitude), longitude = COALESCE(%s, longitude) "
                "WHERE id = %s",
                (venue.get("name_ar"), venue.get("latitude"),
                 venue.get("longitude"), existing["venue_id"]),
            )
        elif venue.get("name_ar"):
            # AR-only venue with no match link yet
            vid = self.get_or_create_venue(
                None, venue.get("name_ar"), venue.get("latitude"), venue.get("longitude")
            )
            if vid:
                self.conn.execute(
                    "UPDATE matches SET venue_id = %s WHERE id = %s", (vid, match_id)
                )

        # ---- change detection (detail side) ------------------------------------
        # Which client-visible parts of the detail payload actually differ
        # from what is stored? Detail refreshes re-arrive every couple of
        # minutes for live matches, and an unchanged page must NOT bump
        # data_version or broadcast anything. The same computation also
        # yields the NEW events (goals/cards/subs) for `match.event` SSE
        # messages. COALESCE semantics apply: a NULL in the payload keeps
        # the stored value.
        detail_compare: List[str] = []
        if existing is None:
            detail_compare = ["__insert__"]
        else:
            _eff = lambda new, old: new if new is not None else old  # noqa: E731
            pairs = (
                ("status", _eff(detail.get("status"), existing["status"])),
                ("period", _eff(detail.get("period"), existing["period"])),
                ("home_score", _eff(detail.get("home_score"), existing["home_score"])),
                ("away_score", _eff(detail.get("away_score"), existing["away_score"])),
                ("home_agg_score", _eff(detail.get("home_agg_score"), existing["home_agg_score"])),
                ("away_agg_score", _eff(detail.get("away_agg_score"), existing["away_agg_score"])),
                ("home_pen_score", _eff(detail.get("home_pen_score"), existing["home_pen_score"])),
                ("away_pen_score", _eff(detail.get("away_pen_score"), existing["away_pen_score"])),
                ("home_score_ht", _eff(detail.get("home_score_ht"), existing["home_score_ht"])),
                ("away_score_ht", _eff(detail.get("away_score_ht"), existing["away_score_ht"])),
                ("home_score_ft", _eff(detail.get("home_score_ft"), existing["home_score_ft"])),
                ("away_score_ft", _eff(detail.get("away_score_ft"), existing["away_score_ft"])),
                ("home_score_et", _eff(detail.get("home_score_et"), existing["home_score_et"])),
                ("away_score_et", _eff(detail.get("away_score_et"), existing["away_score_et"])),
                ("referee", _eff(detail.get("referee"), existing["referee"])),
                ("home_formation", _eff(((detail.get("lineups") or {}).get("teams") or {}).get("home", {}).get("formation"), existing["home_formation"])),
                ("away_formation", _eff(((detail.get("lineups") or {}).get("teams") or {}).get("away", {}).get("formation"), existing["away_formation"])),
            )
            for field, eff_v in pairs:
                if _values_differ(existing[field], eff_v):
                    detail_compare.append(field)
            if (1 if (detail.get("lineups") or {}).get("confirmed") else 0) \
                    > (existing["lineups_confirmed"] or 0):
                detail_compare.append("lineups_confirmed")

        # ---- previous events / lineups / stats (BEFORE the writes) -------------
        # One read serves THREE masters: the language-preserving COALESCE
        # below, the event-diff change detection, and the new-event list that
        # becomes `match.event` SSE messages.
        prev_event_names: Dict[Any, Dict[str, Optional[str]]] = {}
        prev_event_state: Dict[Any, Dict[str, Any]] = {}
        for old in self.conn.execute(
            """SELECT player_id, related_player_id, event_type, minute, extra_minute,
                      player_name_en, player_name_ar,
                      related_player_name_en, related_player_name_ar,
                      team_side, home_score_after, away_score_after,
                      outcome, decision, sort_order
               FROM match_events WHERE match_id = %s""",
            (match_id,),
        ).fetchall():
            key = (old["player_id"], old["related_player_id"], old["event_type"],
                   old["minute"], old["extra_minute"])
            prev_event_names[key] = {
                "player_name_en": old["player_name_en"],
                "player_name_ar": old["player_name_ar"],
                "related_player_name_en": old["related_player_name_en"],
                "related_player_name_ar": old["related_player_name_ar"],
            }
            prev_event_state[key] = {
                "team_side": old["team_side"],
                "home_score_after": old["home_score_after"],
                "away_score_after": old["away_score_after"],
                "outcome": old["outcome"],
                "decision": old["decision"],
                "sort_order": old["sort_order"],
            }

        # event identity of the incoming payload
        def _event_key(ev: Dict[str, Any]) -> Any:
            player = ev.get("player") or {}
            related = ev.get("related_player") or {}
            return (player.get("id"), related.get("id"), ev["event_type"],
                    ev.get("minute"), ev.get("extra_minute"))

        new_event_keys = {_event_key(ev) for ev in detail.get("events", [])}
        prev_event_keys = set(prev_event_names.keys())
        added_events: List[Dict[str, Any]] = []
        if new_event_keys != prev_event_keys:
            detail_compare.append("events")
        for ev in detail.get("events", []):
            key = _event_key(ev)
            if key not in prev_event_state:
                player = ev.get("player") or {}
                related = ev.get("related_player") or {}
                added_events.append({
                    "eventType": ev["event_type"],
                    "minute": ev.get("minute"),
                    "extraMinute": ev.get("extra_minute"),
                    "teamSide": ev.get("team_side"),
                    "playerId": player.get("id"),
                    "playerNameEn": player.get("name_en"),
                    "playerNameAr": player.get("name_ar"),
                    "relatedPlayerId": related.get("id"),
                    "relatedPlayerNameEn": related.get("name_en"),
                    "relatedPlayerNameAr": related.get("name_ar"),
                    "homeScoreAfter": ev.get("home_score_after"),
                    "awayScoreAfter": ev.get("away_score_after"),
                    "outcome": ev.get("outcome"),
                    "decision": ev.get("decision"),
                })
            else:
                # same identity but different outcome (VAR overturn,
                # score-after correction) still counts as a change
                st = prev_event_state[key]
                if (ev.get("outcome") != st["outcome"]
                        or ev.get("decision") != st["decision"]
                        or ev.get("home_score_after") != st["home_score_after"]
                        or ev.get("away_score_after") != st["away_score_after"]):
                    detail_compare.append("events")

        # lineup identity diff (per team: the set of players)
        _lineups = detail.get("lineups") or {}
        new_lineup_ids: set = set()
        for side in ("home", "away"):
            team = (_lineups.get("teams") or {}).get(side) or {}
            for entry in team.get("entries", []):
                person = entry.get("person") or {}
                if person.get("id"):
                    new_lineup_ids.add((team["team_id"], person["id"]))
        if existing is not None:
            prev_lineup_ids = {
                (r["team_id"], r["player_id"]) for r in self.conn.execute(
                    "SELECT team_id, player_id FROM lineups WHERE match_id = %s",
                    (match_id,),
                ).fetchall()
            }
            if new_lineup_ids != prev_lineup_ids:
                detail_compare.append("lineups")

        # stats diff ((team, stat_type, value) set)
        _home_tid = ((_lineups.get("teams") or {}).get("home") or {}).get("team_id")
        _away_tid = ((_lineups.get("teams") or {}).get("away") or {}).get("team_id")
        new_stat_rows: set = set()
        for stat in detail.get("stats", []):
            for tid, value in ((_home_tid, stat.get("home_value")),
                               (_away_tid, stat.get("away_value"))):
                if tid and value is not None:
                    new_stat_rows.add((tid, stat["stat_type"], float(value)))
        if existing is not None:
            prev_stat_rows = {
                (r["team_id"], r["stat_type"], float(r["value"])) for r in self.conn.execute(
                    """SELECT team_id, stat_type, value FROM team_match_stats
                       WHERE match_id = %s AND value IS NOT NULL""",
                    (match_id,),
                ).fetchall()
            }
            if new_stat_rows != prev_stat_rows:
                detail_compare.append("statistics")

        version_bump = 1 if detail_compare else 0

        # refresh scores + meta (gameset_name_ar may arrive from AR pages)
        self.conn.execute(
            """
            UPDATE matches SET
                status = COALESCE(%(status)s, status),
                period = COALESCE(%(period)s, period),
                round_name = COALESCE(%(round_name)s, round_name),
                gameset_name = COALESCE(%(gameset_name)s, gameset_name),
                gameset_name_ar = COALESCE(%(gameset_name_ar)s, gameset_name_ar),
                home_score = COALESCE(%(home_score)s, home_score),
                away_score = COALESCE(%(away_score)s, away_score),
                home_agg_score = COALESCE(%(home_agg_score)s, home_agg_score),
                away_agg_score = COALESCE(%(away_agg_score)s, away_agg_score),
                home_pen_score = COALESCE(%(home_pen_score)s, home_pen_score),
                away_pen_score = COALESCE(%(away_pen_score)s, away_pen_score),
                home_score_ht = COALESCE(%(home_score_ht)s, home_score_ht),
                away_score_ht = COALESCE(%(away_score_ht)s, away_score_ht),
                home_score_ft = COALESCE(%(home_score_ft)s, home_score_ft),
                away_score_ft = COALESCE(%(away_score_ft)s, away_score_ft),
                home_score_et = COALESCE(%(home_score_et)s, home_score_et),
                away_score_et = COALESCE(%(away_score_et)s, away_score_et),
                referee = COALESCE(%(referee)s, referee),
                lineups_confirmed = GREATEST(%(lineups_confirmed)s, lineups_confirmed),
                home_formation = COALESCE(%(home_formation)s, home_formation),
                away_formation = COALESCE(%(away_formation)s, away_formation),
                season_id = COALESCE(%(season_id)s, season_id),
                detail_fetched_at = %(now)s,
                data_version = data_version + %(version_bump)s
            WHERE id = %(match_id)s
            """,
            {
                "status": detail.get("status"),
                "period": detail.get("period"),
                "round_name": detail.get("round_name"),
                "gameset_name": detail.get("gameset_name"),
                "gameset_name_ar": detail.get("gameset_name_ar"),
                "home_score": detail.get("home_score"),
                "away_score": detail.get("away_score"),
                "home_agg_score": detail.get("home_agg_score"),
                "away_agg_score": detail.get("away_agg_score"),
                "home_pen_score": detail.get("home_pen_score"),
                "away_pen_score": detail.get("away_pen_score"),
                "home_score_ht": detail.get("home_score_ht"),
                "away_score_ht": detail.get("away_score_ht"),
                "home_score_ft": detail.get("home_score_ft"),
                "away_score_ft": detail.get("away_score_ft"),
                "home_score_et": detail.get("home_score_et"),
                "away_score_et": detail.get("away_score_et"),
                "referee": detail.get("referee"),
                "lineups_confirmed": 1 if (detail.get("lineups") or {}).get("confirmed") else 0,
                "home_formation": ((detail.get("lineups") or {}).get("teams") or {}).get("home", {}).get("formation"),
                "away_formation": ((detail.get("lineups") or {}).get("teams") or {}).get("away", {}).get("formation"),
                "season_id": season.get("id"),
                "now": utcnow(),
                "version_bump": version_bump,
                "match_id": match_id,
            },
        )

        # replace events + lineups + stats + managers (idempotent full refresh)
        #
        # Language-preserving refresh: on the live-refresh hot path detail
        # pages are fetched one language at a time (EN carries the
        # fast-changing scores/events/minutes, AR only the slow-changing
        # names). The wholesale event replace below must therefore not lose
        # the language the new payload does not carry - remember each stored
        # event's names first, then COALESCE them back in by event identity
        # (person ids + type + minute), so an EN-only or AR-only refresh
        # keeps the other language's names intact. (prev_event_names was
        # read above, before any writes.)

        self.conn.execute("DELETE FROM match_events WHERE match_id = %s", (match_id,))
        self.conn.execute("DELETE FROM lineups WHERE match_id = %s", (match_id,))
        self.conn.execute("DELETE FROM team_match_stats WHERE match_id = %s", (match_id,))
        self.conn.execute("DELETE FROM match_managers WHERE match_id = %s", (match_id,))

        for ev in detail.get("events", []):
            player = ev.get("player") or {}
            related = ev.get("related_player") or {}
            prev = prev_event_names.get(
                (player.get("id"), related.get("id"), ev["event_type"],
                 ev.get("minute"), ev.get("extra_minute"))) or {}
            player = {**player,
                      "name_en": player.get("name_en") or prev.get("player_name_en"),
                      "name_ar": player.get("name_ar") or prev.get("player_name_ar")}
            related = {**related,
                       "name_en": related.get("name_en") or prev.get("related_player_name_en"),
                       "name_ar": related.get("name_ar") or prev.get("related_player_name_ar")}
            self.upsert_player(player)
            if related.get("id"):
                self.upsert_player(related)
            self.conn.execute(
                """INSERT INTO match_events
                   (match_id, team_side, event_type, minute, extra_minute,
                    player_id, player_name_en, player_name_ar,
                    related_player_id, related_player_name_en, related_player_name_ar,
                    home_score_after, away_score_after, outcome, decision, sort_order)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (match_id, ev.get("team_side"), ev["event_type"], ev.get("minute"),
                 ev.get("extra_minute"), player.get("id"), player.get("name_en"),
                 player.get("name_ar"), related.get("id"), related.get("name_en"),
                 related.get("name_ar"), ev.get("home_score_after"),
                 ev.get("away_score_after"), ev.get("outcome"), ev.get("decision"),
                 ev.get("sort_order")),
            )

        lineups = detail.get("lineups") or {}
        for side in ("home", "away"):
            team = (lineups.get("teams") or {}).get(side) or {}
            if not team.get("team_id"):
                continue
            manager = team.get("manager") or {}
            if manager.get("id") or manager.get("name_en") or manager.get("name_ar"):
                self.conn.execute(
                    """INSERT INTO match_managers
                       (match_id, team_id, manager_id, manager_name_en, manager_name_ar)
                       VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT (match_id, team_id) DO UPDATE SET
                           manager_id      = COALESCE(excluded.manager_id,
                                                      match_managers.manager_id),
                           manager_name_en = COALESCE(excluded.manager_name_en,
                                                      match_managers.manager_name_en),
                           manager_name_ar = COALESCE(excluded.manager_name_ar,
                                                      match_managers.manager_name_ar)""",
                    (match_id, team["team_id"], manager.get("id"),
                     manager.get("name_en"), manager.get("name_ar")),
                )
            for entry in team.get("entries", []):
                person = entry.get("person") or {}
                self.upsert_player(person)
                if not person.get("id"):
                    continue
                self.conn.execute(
                    """INSERT INTO lineups
                       (match_id, team_id, player_id, is_starter, shirt_number,
                        position_x, position_y, is_captain, rating)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (match_id, team_id, player_id) DO UPDATE SET
                           is_starter   = excluded.is_starter,
                           shirt_number = excluded.shirt_number,
                           position_x   = excluded.position_x,
                           position_y   = excluded.position_y,
                           is_captain   = excluded.is_captain,
                           rating       = excluded.rating""",
                    (match_id, team["team_id"], person["id"], entry.get("is_starter", 0),
                     entry.get("shirt_number"), entry.get("position_x"),
                     entry.get("position_y"), entry.get("is_captain", 0),
                     entry.get("rating")),
                )

        home_team_id = ((lineups.get("teams") or {}).get("home") or {}).get("team_id")
        away_team_id = ((lineups.get("teams") or {}).get("away") or {}).get("team_id")
        for stat in detail.get("stats", []):
            for team_id, value in ((home_team_id, stat.get("home_value")),
                                   (away_team_id, stat.get("away_value"))):
                if team_id and value is not None:
                    self.conn.execute(
                        """INSERT INTO team_match_stats
                           (match_id, team_id, stat_type, value) VALUES (%s,%s,%s,%s)
                           ON CONFLICT (match_id, team_id, stat_type) DO UPDATE SET
                               value = excluded.value""",
                        (match_id, team_id, stat["stat_type"], value),
                    )

        # ---- record the change for the live layer --------------------------------
        # The worker drains these AFTER COMMIT and publishes SSE / Redis
        # updates for exactly this match - never for an unchanged one.
        if version_bump:
            new_version = (existing["data_version"] if existing else 0) + 1
            entry = self.changed_matches.get(match_id) or {
                "version": new_version, "fields": [], "inserted": existing is None,
            }
            entry["version"] = new_version
            entry["fields"] = sorted(set(entry["fields"] + detail_compare))
            entry["inserted"] = entry["inserted"] or existing is None
            self.changed_matches[match_id] = entry
            if added_events:
                known = self.new_match_events.setdefault(match_id, [])
                self.new_match_events[match_id] = known + added_events

    # ==================================================================
    # standings + gamesets (competition feature)
    # ==================================================================
    def replace_standings(self, comp_id: str, season_id: Optional[str],
                          stage: str, tables: List[Dict[str, Any]]) -> None:
        """Wholesale-replace one competition's standings for a stage.

        Positions shift between matchdays, so a delete+insert keeps the
        table exact without stale-row bookkeeping.
        """
        now = utcnow()
        self.conn.execute(
            "DELETE FROM standings WHERE competition_id = %s "
            "AND season_id IS NOT DISTINCT FROM %s AND stage = %s",
            (comp_id, season_id, stage),
        )
        for tbl in tables:
            table_name = tbl.get("name")
            for row in tbl.get("rows", []):
                team = row.get("team") or {}
                if not team.get("id"):
                    continue
                self.upsert_team(team)
                self.conn.execute(
                    """INSERT INTO standings (
                           competition_id, season_id, stage, table_name, position,
                           team_id, played, win, draw, lose, goals_for, goals_against,
                           goal_diff, points, form_json, markers_json, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        comp_id, season_id, stage, table_name, row.get("position"),
                        team["id"], row.get("played"), row.get("win"), row.get("draw"),
                        row.get("lose"), row.get("goals_for"), row.get("goals_against"),
                        row.get("goal_diff"), row.get("points"),
                        json.dumps(row.get("form") or [], ensure_ascii=False),
                        json.dumps(row.get("markers") or [], ensure_ascii=False),
                        now,
                    ),
                )

    def get_standings(self, comp_id: str, season_id: Optional[str] = None,
                      stage: str = "total") -> List[Dict[str, Any]]:
        """Standings rows (joined with team names/crests), table order kept."""
        sql = """
            SELECT s.*, t.name_en AS team_name_en, t.name_ar AS team_name_ar,
                   t.short_name_en AS team_short_en, t.code AS team_code,
                   t.crest_url AS team_crest
            FROM standings s JOIN teams t ON t.id = s.team_id
            WHERE s.competition_id = %s AND s.stage = %s
        """
        params: list = [comp_id, stage]
        if season_id is not None:
            sql += " AND s.season_id = %s"
            params.append(season_id)
        sql += " ORDER BY s.table_name IS NULL DESC, s.table_name, s.position"
        return self.conn.execute(sql, params).fetchall()

    def replace_standings_markers(self, comp_id: str, season_id: Optional[str],
                                  markers: List[Dict[str, Any]]) -> None:
        """Replace the marker legend (id -> name/type) for one competition."""
        self.conn.execute(
            "DELETE FROM standings_markers WHERE competition_id = %s "
            "AND season_id IS NOT DISTINCT FROM %s",
            (comp_id, season_id),
        )
        for m in markers:
            if m.get("id"):
                self.conn.execute(
                    """INSERT INTO standings_markers
                       (competition_id, season_id, marker_id, name, type)
                       VALUES (%s,%s,%s,%s,%s)
                       ON CONFLICT (competition_id, season_id, marker_id) DO UPDATE SET
                           name = excluded.name,
                           type = excluded.type""",
                    (comp_id, season_id, m["id"], m.get("name"), m.get("type")),
                )

    def get_standings_markers(self, comp_id: str,
                              season_id: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM standings_markers WHERE competition_id = %s"
        params: list = [comp_id]
        if season_id is not None:
            sql += " AND season_id = %s"
            params.append(season_id)
        return self.conn.execute(sql, params).fetchall()

    def upsert_gamesets(self, comp_id: str, season_id: Optional[str],
                        gamesets: List[Dict[str, Any]]) -> None:
        for order, gs in enumerate(gamesets):
            gst_id = gs.get("game_set_type_id")
            if not gst_id:
                continue
            self.conn.execute(
                """INSERT INTO gamesets (
                       competition_id, season_id, game_set_type_id,
                       name_en, name_ar, is_active, sort_order)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(competition_id, game_set_type_id) DO UPDATE SET
                       season_id  = COALESCE(excluded.season_id, gamesets.season_id),
                       name_en    = COALESCE(excluded.name_en, gamesets.name_en),
                       name_ar    = COALESCE(excluded.name_ar, gamesets.name_ar),
                       is_active  = GREATEST(excluded.is_active, gamesets.is_active),
                       sort_order = excluded.sort_order""",
                (comp_id, season_id, gst_id,
                 gs.get("name_en"), gs.get("name_ar"),
                 gs.get("is_active", 0), order),
            )

    def get_gamesets(self, comp_id: str) -> List[Dict[str, Any]]:
        return self.conn.execute(
            """SELECT g.*, (
                   SELECT COUNT(*) FROM matches m
                   WHERE m.competition_id = g.competition_id AND m.gameset_id = g.game_set_type_id
               ) AS match_count
               FROM gamesets g WHERE g.competition_id = %s
               ORDER BY g.sort_order, g.id""",
            (comp_id,),
        ).fetchall()

    def mark_competition_scrape(self, comp_id: str, season_id: Optional[str],
                                has_standings: Optional[bool] = None,
                                standings: bool = False, matches: bool = False) -> None:
        """Update the competition scrape bookkeeping row (TTL source)."""
        now = utcnow()
        sets, params = [], []
        if season_id is not None:
            sets.append("season_id = %s")
            params.append(season_id)
        if has_standings is not None:
            sets.append("has_standings = %s")
            params.append(1 if has_standings else 0)
        if standings:
            sets.append("standings_at = %s")
            params.append(now)
        if matches:
            sets.append("matches_at = %s")
            params.append(now)
        assignments = ", ".join(sets)
        self.conn.execute(
            f"""INSERT INTO competition_scrapes (competition_id, season_id, has_standings,
                                                standings_at, matches_at)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT(competition_id) DO UPDATE SET {assignments}""",
            (comp_id, season_id, 1 if has_standings is not False else 0,
             now if standings else None, now if matches else None, *params),
        )

    def get_competition_scrape(self, comp_id: str) -> Optional[Dict[str, Any]]:
        return self.conn.execute(
            "SELECT * FROM competition_scrapes WHERE competition_id = %s", (comp_id,)
        ).fetchone()

    # ==================================================================
    # scrape run log
    # ==================================================================
    def start_run(self, run_mode: str, target: str, source: str) -> int:
        row = self.conn.execute(
            """INSERT INTO scrape_runs (run_mode, target, source, status, started_at)
               VALUES (%s,%s,%s,'running',%s) RETURNING id""",
            (run_mode, target, source, utcnow()),
        ).fetchone()
        self.conn.commit()
        return row["id"] if row else 0

    def finish_run(self, run_id: int, status: str = "ok", **counts: int) -> None:
        error = counts.pop("error", None)
        self.conn.execute(
            f"""UPDATE scrape_runs SET status = %s, finished_at = %s,
                {', '.join(f'{k} = %s' for k in counts)} WHERE id = %s""",
            (status, utcnow(), *counts.values(), run_id),
        )
        self.conn.commit()

    # ==================================================================
    # small query helpers used by the CLI
    # ==================================================================
    def matches_missing_details(self, listed_date: str) -> List[Dict[str, Any]]:
        return self.conn.execute(
            """SELECT m.id, m.slug_en, m.status, c.name_en AS competition
               FROM matches m JOIN competitions c ON c.id = m.competition_id
               WHERE m.listed_date = %s AND m.detail_fetched_at IS NULL
                 AND m.status IN ('RESULT','LIVE','AET','PEN')
               ORDER BY m.kickoff_utc""",
            (listed_date,),
        ).fetchall()

    # ---- bootstrap resumability --------------------------------------------
    # The one-time `bootstrap` walk covers ~4000 days. To make it safe to
    # interrupt and restart, we skip any date that already has a *successful*
    # scrape_runs row of the right mode. The check is per-mode so that a past
    # listing-only run is still re-scraped when the user later adds
    # `--details`, and vice-versa.
    def listing_done_for(self, date_iso: str) -> bool:
        """True iff a successful listing run already exists for `date_iso`."""
        row = self.conn.execute(
            """SELECT 1 FROM scrape_runs
               WHERE run_mode = 'date' AND target = %s AND status = 'ok'
               LIMIT 1""",
            (date_iso,),
        ).fetchone()
        return row is not None

    def details_done_for(self, date_iso: str) -> bool:
        """True iff a successful details run already exists for `date_iso`."""
        row = self.conn.execute(
            """SELECT 1 FROM scrape_runs
               WHERE run_mode = 'details' AND target = %s AND status = 'ok'
               LIMIT 1""",
            (date_iso,),
        ).fetchone()
        return row is not None

    def bootstrap_progress(self) -> Dict[str, int]:
        """How many listing + details runs have already succeeded (any date)."""
        rows = self.conn.execute(
            """SELECT run_mode, COUNT(*) AS n
               FROM scrape_runs
               WHERE status = 'ok' AND run_mode IN ('date', 'details')
               GROUP BY run_mode"""
        ).fetchall()
        return {r["run_mode"]: r["n"] for r in rows}

    def stats(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for table in ("competitions", "seasons", "teams", "players", "venues",
                      "matches", "match_events", "lineups", "match_managers",
                      "team_match_stats", "player_career_entries"):
            out[table] = self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        out["matches_with_details"] = self.conn.execute(
            "SELECT COUNT(*) AS n FROM matches WHERE detail_fetched_at IS NOT NULL"
        ).fetchone()["n"]
        out["players_with_profile"] = self.conn.execute(
            "SELECT COUNT(*) AS n FROM players WHERE profile_fetched_at IS NOT NULL"
        ).fetchone()["n"]
        return out
