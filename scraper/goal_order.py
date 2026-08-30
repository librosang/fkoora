"""goal.com-style competition ordering for the day listing.

Grounded in goal.com's OWN ordering, measured from their live-scores /
results / fixtures pages (EN and AR pages use the IDENTICAL order):

  1. a small FEATURED set first, in goal.com's own popularity order
     (their navigation "Leagues" menu: Premier League, La Liga, Serie A,
     Ligue 1, Bundesliga, UCL, UEL, UECL, MLS, Saudi Pro League; the
     Arabic edition additionally features the Egyptian league, AFC
     Champions League and the Turkish league, with the Saudi league
     promoted to its own menu) - on scores pages the continental cups
     (Champions/Europa/Conference qualifiers, Copa Libertadores/
     Sudamericana, Carabao Cup) occupy exactly this top band;
  2. everything else ALPHABETICALLY BY AREA (Argentina, Australia,
     Bolivia, ... , Uzbekistan - International comps sit at "I" between
     Iceland and Iraq), ties broken by competition name.

The league list uses this order EXCLUSIVELY, on every scores / results
/ fixtures page: live matches NEVER reorder it. goal.com keeps the
same league order whether or not a league currently has live matches
(a famous league with no games that day still sits at its usual top
position) - liveness is shown per-match (minute, pulsing score), not
by floating leagues around.

Name matching reuses the same conventions as scraper/major.py:
  * "..."  -> substring match (case-insensitive) on the EN name
  * "=..." -> EXACT EN name match (many local leagues share names:
              20+ competitions are called plain "Premier League")
  * second element scopes the rule to an area name fragment (or None)
Women's / youth / reserve editions never get a featured rank (they fall
through to the alphabetical band), mirroring the major-league screens.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# (name rule, area fragment or None) in goal.com priority order.
FEATURED_ORDER: List[Tuple[str, Optional[str]]] = [
    # --- the biggest international tournaments ---
    ("world cup", None),                       # FIFA WC + Club WC
    ("champions league", "international"),     # UEFA CL + qualification
    ("=euro", None),
    ("european championship", None),
    ("copa america", None),
    ("africa cup", None),                      # AFCON
    ("afcon", None),
    ("nations league", None),
    # --- goal.com nav "Leagues" top-5, in their order ---
    ("=premier league", "england"),
    ("=laliga", None),
    ("=la liga", None),
    ("=serie a", "italy"),
    ("=ligue 1", "france"),
    ("=bundesliga", "germany"),
    # --- European cups (incl. qualification rounds) ---
    ("europa league", "international"),
    ("conference league", "international"),
    ("europa conference", "international"),
    ("=uefa super cup", None),
    # --- South American cups (featured on goal.com scores pages) ---
    ("copa libertadores", None),
    ("copa sudamericana", None),
    # --- England cups ---
    ("=carabao cup", None),
    ("=efl cup", None),
    ("=cup", "england"),                       # FA Cup
    # --- the Arab world's flagship leagues (goal.com AR edition) ---
    ("saudi pro league", None),
    ("=premier league", "egypt"),
    ("champions league", "asia"),              # AFC Champions League (Elite)
    ("champions league", "africa"),            # CAF Champions League
    ("confederation cup", "africa"),           # CAF Confederation Cup
    ("=super lig", "turkiye"),
    ("=super lig", "turkey"),
    ("=stars league", None),                   # Qatar
    ("arabian gulf league", None),             # UAE
    ("botola", None),                          # Morocco
    ("=premier league", "iraq"),
    # --- other notable European top flights (goal.com table pages) ---
    ("=eredivisie", None),
    ("=liga portugal", None),
    ("=premiership", "scotland"),
    ("=first division a", "belgium"),
    # --- major domestic cups ---
    ("=copa del rey", None),
    ("=cup", "spain"),
    ("=coppa italia", None),
    ("=cup", "italy"),
    ("=dfb pokal", None),
    ("=cup", "germany"),
    ("=coupe de france", None),
    ("=cup", "france"),
    ("=cup", "brazil"),
    ("=cup", "morocco"),                       # Throne Cup
    ("=cup", "egypt"),
    ("=cup", "saudi arabia"),
    # --- the Americas (goal.com nav + table pages) ---
    ("=major league soccer", None),
    ("=liga mx", None),
    ("=serie a", "brazil"),
    ("=liga profesional", "argentina"),
    # --- other international featured on AR nav ---
    ("asian cup", None),
    ("arab cup", None),
    ("gulf cup", None),
    ("intercontinental cup", None),
]

# Generic EN names that only the Arabic name disambiguates (women's /
# youth editions of featured competitions).
AR_EXCLUDE_FRAGMENTS = [
    "سيدات", "نساء", "فتيات",      # women / ladies / girls
    "ناشئ", "شباب", "براعم",        # youth / juniors
    "رديف", "احتياط",              # reserve
]
EN_EXCLUDE_FRAGMENTS = [
    "women", "feminine", "femenino", "femenil", "(w)",
    "u18", "u19", "u20", "u21", "u23", "youth", "reserve", "academy",
]
AR_UNDER_AGE_RE = re.compile(r"تحت\s*[0-9٠-٩]")

_NOT_FEATURED = 999


def _rule_matches(rule: str, area: Optional[str], name_en: str,
                  area_name_en: Optional[str]) -> bool:
    exact = rule.startswith("=")
    frag = rule[1:] if exact else rule
    name = (name_en or "").lower()
    name_ok = (name == frag) if exact else (frag in name)
    if not name_ok:
        return False
    if area:
        return area in (area_name_en or "").lower()
    return True


def featured_rank(comp: Dict[str, Optional[str]]) -> int:
    """goal.com priority rank for a competition; 999 = not featured.

    Accepts a dict with any of: name_en, name_ar, area_name_en.
    """
    name_en = (comp.get("name_en") or "").lower()
    if any(frag in name_en for frag in EN_EXCLUDE_FRAGMENTS):
        return _NOT_FEATURED
    name_ar = comp.get("name_ar") or ""
    if any(frag in name_ar for frag in AR_EXCLUDE_FRAGMENTS):
        return _NOT_FEATURED
    if AR_UNDER_AGE_RE.search(name_ar):
        return _NOT_FEATURED

    for i, (rule, area) in enumerate(FEATURED_ORDER):
        if _rule_matches(rule, area, comp.get("name_en") or "",
                         comp.get("area_name_en")):
            return i
    return _NOT_FEATURED


def goal_sort_key(comp: Dict[str, Optional[str]]) -> tuple:
    """Full sort key (rank, area, name) - the COMPLETE day-listing order.

    Purely static competition properties: no live/match state is (or ever
    should be) part of this key.
    """
    return (
        featured_rank(comp),
        (comp.get("area_name_en") or "").lower(),
        (comp.get("name_en") or "").lower(),
    )
