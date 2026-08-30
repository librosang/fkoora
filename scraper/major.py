""""Major leagues & cups" classification, shared by the enrichment pipeline
and the JSON API (the frontend's "Major leagues & cups only" toggle).

Rules were grounded against the provider's REAL competition names (a week of
live listings, ~260 distinct competitions). Notable provider quirks handled:

  * Egypt's top flight is listed as plain "Premier League" + area Egypt
    (NOT "Egyptian Premier League") -> needs an area-scoped rule
  * several cups are listed as plain "Cup" + area (FA Cup, Coupe de France,
    Copa del Rey, Throne Cup ...)
  * Algeria/Tunisia top flights hide behind generic EN names ("Division",
    "Ligue I") -> matched via their distinctive Arabic names instead
  * the German *women's* Bundesliga is listed as plain "Bundesliga" -> only
    the Arabic name reveals "للسيدات", so Arabic names are screened too

Keep in sync with the rule set the frontend documents in its filter tooltip.
"""

from __future__ import annotations

import re
from typing import Dict, Optional

# (name_fragment, area_fragment_or_None). Prefix the name with "=" for an
# EXACT match (many local leagues share names: 20+ "Premier League"s).
COMPETITION_RULES = [
    # --- top-5 European leagues ---
    ("=premier league", "england"),
    ("=laliga", None),
    ("=la liga", None),
    ("=serie a", "italy"),
    ("=bundesliga", "germany"),
    ("=ligue 1", "france"),
    # --- other notable European top flights ---
    ("=eredivisie", None),            # Netherlands
    ("=liga portugal", None),
    ("=premiership", "scotland"),
    ("=super lig", "turkiye"),
    ("=super lig", "turkey"),
    ("=first division a", "belgium"),
    # --- European cups (incl. qualification rounds) ---
    ("champions league", None),       # UEFA + CAF + AFC champions leagues
    ("europa league", None),
    ("conference league", None),
    ("europa conference", None),
    ("=uefa super cup", None),
    # --- major international tournaments ---
    ("world cup", None),              # FIFA World Cup + Club World Cup
    ("intercontinental cup", None),
    ("=euro", None),                  # UEFA Euro (area: International)
    ("european championship", None),
    ("copa america", None),
    ("afcon", None),
    ("africa cup", None),
    ("nations league", None),
    ("asian cup", None),
    ("arab cup", None),
    ("gulf cup", None),
    # --- South America ---
    ("copa libertadores", None),
    ("copa sudamericana", None),
    ("=liga profesional", "argentina"),
    ("=serie a", "brazil"),
    ("=liga mx", None),
    ("=major league soccer", None),
    # --- Africa / Arab world top flights ---
    ("=premier league", "egypt"),
    ("=premier league", "iraq"),
    ("saudi pro league", None),
    ("=stars league", None),          # Qatar
    ("arabian gulf league", None),    # UAE
    ("botola", None),                 # Morocco
    ("confederation cup", None),      # CAF Confederation Cup
    # --- major domestic cups (provider lists most as plain "Cup" + area) ---
    ("=cup", "england"),              # FA Cup
    ("=carabao cup", None),
    ("=copa del rey", None),
    ("=cup", "spain"),
    ("=dfb pokal", None),
    ("=cup", "germany"),
    ("=coppa italia", None),
    ("=cup", "italy"),
    ("=coupe de france", None),
    ("=cup", "france"),
    ("=cup", "brazil"),
    ("=cup", "morocco"),              # Throne Cup
    ("=cup", "egypt"),
    ("=cup", "saudi arabia"),
]

# Leagues whose EN name is too generic ("Division") - matched via the
# distinctive Arabic name the provider also carries.
AR_INCLUDE_FRAGMENTS = [
    "البطولة المغربية",      # Morocco Botola (backup to the EN "botola" rule)
    "البطولة الاحترافية",    # Morocco Botola Pro (alternate AR naming)
    "الرابطة الجزائرية",     # Algeria Ligue 1
    "الرابطة التونسية",      # Tunisia Ligue 1
]

COMPETITION_EXCLUDE = [
    "u18", "u19", "u20", "u21", "u23",
    "youth", "reserve", "academy",
    "women", "feminine", "femenino", "femenil", "(w)",
    "division 2", "2. division", "2nd division",
]

# Some leagues hide behind a generic EN name - only the Arabic name reveals
# the youth/women/reserve edition, so Arabic names are screened as well.
AR_EXCLUDE_FRAGMENTS = [
    "سيدات", "نساء", "فتيات",       # women / ladies / girls
    "ناشئ", "شباب", "براعم",         # youth / juniors / cubs
    "رديف", "احتياط",               # reserve
]
AR_UNDER_AGE_RE = re.compile(r"تحت\s*[0-9٠-٩]")


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


def is_major_competition(comp: Dict[str, Optional[str]]) -> bool:
    """True when the competition is one of the big leagues/cups.

    Accepts a dict with any of: name_en, area_name_en, name_ar.
    """
    name_en = (comp.get("name_en") or "").lower()
    if any(frag in name_en for frag in COMPETITION_EXCLUDE):
        return False

    name_ar = comp.get("name_ar") or ""
    if any(frag in name_ar for frag in AR_EXCLUDE_FRAGMENTS):
        return False
    if AR_UNDER_AGE_RE.search(name_ar):
        return False

    if any(frag in name_ar for frag in AR_INCLUDE_FRAGMENTS):
        return True

    return any(
        _rule_matches(rule, area, comp.get("name_en") or "", comp.get("area_name_en"))
        for rule, area in COMPETITION_RULES
    )
