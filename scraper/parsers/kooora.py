"""Parser for kooora.com (Arabic source).

Kooora shares the same data provider as goal.com (sportfeeds.io), so the
entity IDs match one-to-one. We only need its fixtures listing page, which
provides Arabic names for competitions, teams and venues.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from .. import config
from ..http_client import fetch_next_data

log = logging.getLogger("scraper.kooora")


def parse_fixtures_page(next_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return normalized Arabic-name rows keyed by the shared sportfeeds IDs."""
    data = next_data["props"]["pageProps"].get("data") or []
    rows: List[Dict[str, Any]] = []

    for block in data:
        comp = block.get("competition") or {}
        comp_id = comp.get("id")
        if not comp_id:
            continue
        area = comp.get("area") or {}

        for m in block.get("matches") or []:
            team_a = m.get("teamA") or {}
            team_b = m.get("teamB") or {}
            # skip TBD opponents ("Winner of Match X") - no stable team id yet
            if not m.get("id") or not team_a.get("id") or not team_b.get("id"):
                continue
            gameset = m.get("gameset") or {}

            rows.append(
                {
                    "match_id": m["id"],
                    "kickoff_utc": m.get("startDate"),
                    "competition": {
                        "id": comp_id,
                        "name_ar": comp.get("name"),
                        "area_name_ar": area.get("name"),
                    },
                    "home_team": {"id": team_a.get("id"), "name_ar": team_a.get("name")},
                    "away_team": {"id": team_b.get("id"), "name_ar": team_b.get("name")},
                    "venue_name_ar": (m.get("venue") or {}).get("name"),
                    "gameset_name_ar": gameset.get("name"),
                    "gameset_is_knockout": 1 if gameset.get("isKnockout") else 0,
                    "status": m.get("status"),
                }
            )
    return rows


def fetch_fixtures(date: str) -> List[Dict[str, Any]]:
    """Fetch + parse the kooora fixtures page for one date (YYYY-MM-DD)."""
    url = config.KOOORA_FIXTURES_URL.format(date=date)
    data = fetch_next_data(url)
    rows = parse_fixtures_page(data)
    log.info("kooora   %s: %d matches (Arabic names)", date, len(rows))
    return rows
