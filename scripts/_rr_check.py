#!/usr/bin/env python3
"""
Rich-results checker used by scripts/smoke_test.sh.

Reads the page HTML from stdin and validates the JSON-LD the way the Google
Rich Results Test would, per page kind and language:

    python3 scripts/_rr_check.py <match|competition|home> <ar|en> < html

Prints "OK <detail>" when every required property is present, else
"BAD <reason>".
"""
import json
import re
import sys

KIND, LANG = sys.argv[1], sys.argv[2]
html = sys.stdin.read()

# ---- collect every JSON-LD block and flatten @graph / ItemList structures --
blocks = re.findall(
    r'<script type="application/ld\+json">(.*?)</script>', html, re.S
)

nodes = []
parse_errors = []
for i, b in enumerate(blocks):
    try:
        d = json.loads(b)
    except json.JSONDecodeError as e:
        parse_errors.append(f"block {i}: {e}")
        continue
    if not isinstance(d, dict):
        continue
    # candidate node holders: the block itself and every @graph member
    holders = [d]
    if "@graph" in d:
        holders.extend(g for g in d["@graph"] if isinstance(g, dict))
    for h in holders:
        nodes.append(h)
        # an ItemList anywhere (top-level OR inside @graph) contributes its
        # inner items as nodes too
        if h.get("@type") == "ItemList":
            for item in h.get("itemListElement", []):
                inner = item.get("item") if isinstance(item, dict) else None
                if isinstance(inner, dict):
                    nodes.append(inner)

if parse_errors:
    print("BAD unparseable JSON-LD: " + "; ".join(parse_errors))
    sys.exit(0)

sports_events = [n for n in nodes if n.get("@type") == "SportsEvent"]
breadcrumb = next((n for n in nodes if n.get("@type") == "BreadcrumbList"), None)
item_list = next((n for n in nodes if n.get("@type") == "ItemList"), None)


def event_problem(ev: dict) -> str:
    """Everything Google's Event rich result requires, plus our bilingual rules."""
    for prop in ("name", "startDate", "location", "eventStatus", "url",
                 "image", "description"):
        if prop not in ev:
            return f"SportsEvent missing {prop}"
    if ev.get("inLanguage") != LANG:
        return f"inLanguage is {ev.get('inLanguage')!r}, want {LANG!r}"
    loc = ev.get("location") or {}
    if not (loc.get("name") or loc.get("url")):
        return "location has neither name nor url"
    return ""


if KIND == "match":
    if not sports_events:
        print("BAD no SportsEvent node")
    else:
        problem = event_problem(sports_events[0])
        if problem:
            print(f"BAD {problem}")
        elif breadcrumb is None:
            print("BAD no BreadcrumbList")
        else:
            ev = sports_events[0]
            print(f"OK SportsEvent({LANG}) + BreadcrumbList, "
                  f"url={ev['url'][:60]}...")

elif KIND == "competition":
    if item_list is None:
        print("BAD no ItemList")
    elif item_list.get("inLanguage") != LANG:
        print(f"BAD ItemList inLanguage is {item_list.get('inLanguage')!r}")
    elif breadcrumb is None:
        print("BAD no BreadcrumbList")
    elif not sports_events:
        print("BAD ItemList has no SportsEvent items")
    else:
        problems = [event_problem(ev) for ev in sports_events]
        problems = [p for p in problems if p]
        if problems:
            print(f"BAD {problems[0]} (and {len(problems)-1} more)"
                  if len(problems) > 1 else f"BAD {problems[0]}")
        else:
            print(f"OK ItemList({LANG}) with {len(sports_events)} complete "
                  f"SportsEvents + BreadcrumbList")

elif KIND == "home":
    if item_list is None:
        print("BAD no ItemList")
    elif not sports_events:
        print("BAD ItemList has no SportsEvent items")
    else:
        problems = [p for p in (event_problem(ev) for ev in sports_events) if p]
        if problems:
            print(f"BAD {problems[0]}")
        else:
            print(f"OK ItemList with {len(sports_events)} complete SportsEvents")

else:
    print(f"BAD unknown kind {KIND}")
