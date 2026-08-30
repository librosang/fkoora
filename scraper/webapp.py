"""
Lightweight web view for the football database - classic kooora-style theme.

Mobile-first layout with team emblems, bilingual (EN/AR, RTL) support.

Run:
    python -m scraper serve            # http://127.0.0.1:8765
    python -m scraper serve --port 9000 --db postgresql://localhost/football
"""

from __future__ import annotations

import os
import re
import sys
import threading
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg
import requests as rq
from flask import Flask, abort, g, redirect, render_template, request, send_file, url_for

from .db import backend

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Images (team crests, competition logos) are cached on disk and served
# locally. Browsers that block the source CDN (e.g. Safari tracking
# prevention / iCloud Private Relay) still get every logo.
CACHE_DIR = Path(os.environ.get("CREST_CACHE_DIR", str(BASE_DIR.parent / "crest_cache")))
_DL_LOCK = threading.Lock()
IMG_MIME = {".svg": "image/svg+xml", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".webp": "image/webp", ".gif": "image/gif", ".png": "image/png"}

# Popular competitions get listed first on a day page (name keywords).
COMPETITION_PRIORITY = [
    "uefa champions league",
    "uefa europa league",
    "uefa europa conference league",
    "premier league",
    "la liga",
    "laliga",
    "serie a",
    "bundesliga",
    "ligue 1",
    "saudi pro league",
    "roc nation saudi pro league",
    "caf champions league",
    "caf confederation cup",
    "world cup",
    "euro",
    "copa america",
    "africa cup of nations",
    "africa cup",
    "afcon",
    "kings cup",
    "qatar stars league",
    "egyptian premier league",
    "botola",
    "uefa nations league",
    "fa cup",
    "efl cup",
    "copa del rey",
    "coppa italia",
    "dfb pokal",
]

MONTHS_EN = ["January", "February", "March", "April", "May", "June", "July",
             "August", "September", "October", "November", "December"]
WEEKDAYS_EN = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MONTHS_AR = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو", "يوليو",
             "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
WEEKDAYS_AR = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]

STATUS_AR = {
    "RESULT": "انتهت",
    "LIVE": "مباشر",
    "FIXTURE": "لم تبدأ",
    "POSTPONED": "مؤجلة",
    "CANCELLED": "ملغاة",
    "SUSPENDED": "متوقفة",
    "AWARDED": "بقرار",
    "DELAYED": "متأخرة",
    "ABANDONED": "متروكة",
}

PERIOD_AR = {
    "FIRST_HALF": "الشوط الأول",
    "SECOND_HALF": "الشوط الثاني",
    "HALF_TIME": "بين الشوطين",
    "EXTRA_TIME_FIRST_HALF": "الوقت الإضافي - الشوط الأول",
    "EXTRA_TIME_SECOND_HALF": "الوقت الإضافي - الشوط الثاني",
    "EXTRA_TIME": "الوقت الإضافي",
    "PENALTY_SHOOTOUT": "ركلات الترجيح",
    "AWAITING_KICKOFF": "بانتظار الانطلاق",
}


# --------------------------------------------------------------------------
# Flask app
# --------------------------------------------------------------------------
app = Flask(__name__, template_folder=str(TEMPLATE_DIR), static_folder=str(STATIC_DIR))
app.config["DB_URL"] = backend.resolve_dsn()
app.config["TEMPLATES_AUTO_RELOAD"] = True  # pick up template edits without restart


def get_db() -> psycopg.Connection:
    if "db" not in g:
        # the context manager commits on success / rolls back on error
        # when it is closed in the teardown handler below
        g.db_ctx = backend.connection(app.config["DB_URL"])
        g.db = g.db_ctx.__enter__()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    ctx = g.pop("db_ctx", None)
    g.pop("db", None)
    if ctx is not None:
        ctx.__exit__(*sys.exc_info())


# --------------------------------------------------------------------------
# Local image proxy + disk cache (team crests, competition logos)
# --------------------------------------------------------------------------
def _cache_path(kind: str, entity_id: str, url: str) -> Path:
    ext = ".png"
    low = url.lower().split("?")[0]
    for e in (".svg", ".jpg", ".jpeg", ".webp", ".gif"):
        if low.endswith(e):
            ext = e
            break
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", entity_id)
    return CACHE_DIR / f"{kind}_{safe}{ext}"


def cached_image(kind: str, entity_id: str, url: str | None):
    """Return (path, mime) of a locally cached image, downloading once."""
    if not url:
        return None, None
    path = _cache_path(kind, entity_id, url)
    if path.exists():
        return path, IMG_MIME.get(path.suffix.lower(), "image/png")
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with _DL_LOCK:
            if not path.exists():  # re-check inside the lock
                resp = rq.get(url, timeout=12,
                              headers={"User-Agent": "Mozilla/5.0 (compatible; football-scraper)"})
                resp.raise_for_status()
                if len(resp.content) < 32:  # empty / error placeholder guard
                    return None, None
                path.write_bytes(resp.content)
    except Exception:
        return None, None
    return path, IMG_MIME.get(path.suffix.lower(), "image/png")


@app.route("/crest/<team_id>")
def crest_route(team_id: str):
    row = get_db().execute("SELECT crest_url FROM teams WHERE id = %s", (team_id,)).fetchone()
    path, mime = cached_image("team", team_id, row["crest_url"] if row else None)
    if path is None:
        return redirect(url_for("static", filename="crest.svg"))
    return send_file(path, mimetype=mime, max_age=2592000)


@app.route("/compimg/<comp_id>")
def compimg_route(comp_id: str):
    row = get_db().execute("SELECT image_url FROM competitions WHERE id = %s", (comp_id,)).fetchone()
    path, mime = cached_image("comp", comp_id, row["image_url"] if row else None)
    if path is None:
        abort(404)
    return send_file(path, mimetype=mime, max_age=2592000)


def cache_all_crests(db_url: Optional[str] = None):
    """Pre-download every team crest and competition logo (CLI helper)."""
    with backend.connection(db_url or app.config["DB_URL"], pooled=False) as con:
        teams = con.execute(
            "SELECT id, crest_url FROM teams WHERE crest_url IS NOT NULL AND crest_url != ''"
        ).fetchall()
        comps = con.execute(
            "SELECT id, image_url FROM competitions WHERE image_url IS NOT NULL AND image_url != ''"
        ).fetchall()
    ok = fail = 0
    total = len(teams) + len(comps)
    jobs = [("team", r["id"], r["crest_url"]) for r in teams] \
        + [("comp", r["id"], r["image_url"]) for r in comps]
    for i, (kind, eid, url) in enumerate(jobs, 1):
        path, _ = cached_image(kind, eid, url)
        if path is not None:
            ok += 1
        else:
            fail += 1
        if i % 200 == 0:
            print(f"  {i}/{total} images processed ...", flush=True)
    return ok, fail


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def parse_date(value: str) -> date_cls:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        abort(404)


def day_mode(d: date_cls) -> str:
    """results / today / fixtures depending on the date vs today."""
    today = date_cls.today()
    if d < today:
        return "results"
    if d == today:
        return "today"
    return "fixtures"


def comp_priority(comp: Dict[str, Any]) -> tuple:
    # comp here is a joined match row: competition columns are aliased c_*
    name = (comp["c_name_en"] or comp["c_name_ar"] or "").lower()
    for i, key in enumerate(COMPETITION_PRIORITY):
        if key in name:
            return (0, i)
    return (1, name)


def human_date(d: date_cls, lang: str) -> str:
    months = MONTHS_AR if lang == "ar" else MONTHS_EN
    weekdays = WEEKDAYS_AR if lang == "ar" else WEEKDAYS_EN
    return f"{weekdays[d.weekday()]} {d.day} {months[d.month - 1]} {d.year}"


def kickoff_time(kickoff_utc: str) -> str:
    """Kickoff HH:MM in UTC (client-side JS converts to local time)."""
    try:
        dt = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
        return dt.strftime("%H:%M")
    except (ValueError, AttributeError):
        return ""


def live_minute(match: Dict[str, Any]) -> str:
    """Short live status like \"90+2'\" derived from the period string."""
    period = (match["period"] or "").strip()
    if not period:
        return "LIVE"
    # e.g. "SECOND_HALF 90+2" -> "90+2'"
    m = re.search(r"(\d+(?:\s*\+\s*\d+)?)\s*$", period)
    if m:
        return m.group(1).replace(" ", "") + "'"
    p = period.lower().replace("_", " ")
    if "half time" in p or "halftime" in p:
        return "HT"
    if "first half" in p:
        return "1H"
    if "second half" in p:
        return "2H"
    if "extra" in p:
        return "ET"
    if "penalt" in p or "shootout" in p:
        return "PEN"
    if "awaiting" in p or "pre" in p or "warmup" in p:
        return "SOON"
    return "LIVE"


def pretty_period(period: str | None, lang: str) -> str:
    """Human-friendly live period, e.g. 'SECOND_HALF 90+2' ->
    '2nd half · 90+2' (EN) or 'الشوط الثاني · 90+2'' (AR)."""
    if not period:
        return ""
    minute = ""
    m = re.search(r"(\d+(?:\s*\+\s*\d+)?)\s*$", period)
    if m:
        minute = m.group(1).replace(" ", "") + "'"
        period = period[: m.start()].strip()
    key = period.upper().replace(" ", "_")
    if lang == "ar":
        label = PERIOD_AR.get(key, period.replace("_", " "))
    else:
        label = key.replace("_", " ").lower()
    return f"{label} · {minute}" if minute else label


def status_label(match: Dict[str, Any], lang: str) -> tuple[str, str]:
    """(short label, css class) for the left status column."""
    status = (match["status"] or "").upper()
    if status == "LIVE":
        return (live_minute(match), "live")
    if status == "RESULT":
        label = "FT" if lang != "ar" else "انتهت"
        if match["home_score_et"] is not None:
            label = "AET" if lang != "ar" else "بعد وقت إضافي"
        return (label, "ft")
    if status in ("POSTPONED", "CANCELLED", "SUSPENDED", "ABANDONED", "DELAYED", "AWARDED"):
        if lang == "ar":
            label = STATUS_AR.get(status, status.title())
        else:
            label = {"POSTPONED": "Post.", "CANCELLED": "Canc.", "SUSPENDED": "Susp.",
                     "ABANDONED": "Aban.", "DELAYED": "Delay", "AWARDED": "Award"}.get(status, status.title())
        return (label, "off")
    return (kickoff_time(match["kickoff_utc"]), "pre")


def has_pens(match: Dict[str, Any]) -> bool:
    return (match["home_pen_score"] is not None and match["away_pen_score"] is not None
            and (match["home_pen_score"] or match["away_pen_score"]))


def score_parts(match: Dict[str, Any]) -> dict:
    """Score display info shared by templates."""
    if match["home_score"] is None or match["away_score"] is None:
        return {"show": False}
    return {
        "show": True,
        "home": match["home_score"],
        "away": match["away_score"],
        "aet": match["home_score_et"] is not None,
        "pens": (match["home_pen_score"], match["away_pen_score"]) if has_pens(match) else None,
    }


def team_name(team: Optional[Dict[str, Any]], lang: str) -> str:
    if team is None:
        return "-"
    if lang == "ar":
        return team["name_ar"] or team["name_en"] or "-"
    return team["name_en"] or team["name_ar"] or "-"


def comp_name(comp: Optional[Dict[str, Any]], lang: str) -> str:
    if comp is None:
        return "-"
    if lang == "ar":
        return comp["name_ar"] or comp["name_en"] or "-"
    return comp["name_en"] or comp["name_ar"] or "-"


def area_name(comp: Optional[Dict[str, Any]], lang: str) -> str:
    if comp is None:
        return ""
    if lang == "ar":
        return comp["area_name_ar"] or comp["area_name_en"] or ""
    return comp["area_name_en"] or comp["area_name_ar"] or ""


def lang_dir(lang: str) -> str:
    return "rtl" if lang == "ar" else "ltr"


def current_lang() -> str:
    return request.args.get("lang", "en")


def toggle_url(**overrides) -> str:
    """Same path with a swapped lang= parameter."""
    args = request.args.to_dict()
    args.update(overrides)
    qs = "&".join(f"{k}={v}" for k, v in args.items())
    return request.path + ("?" + qs if qs else "")


@app.context_processor
def inject_helpers():
    return {
        "lang": current_lang(),
        "dir": lang_dir(current_lang()),
        "toggle_url": toggle_url,
        "team_name": team_name,
        "comp_name": comp_name,
        "area_name": area_name,
        "status_label": status_label,
        "score_parts": score_parts,
        "kickoff_time": kickoff_time,
        "human_date": human_date,
        "pretty_period": pretty_period,
    }


# --------------------------------------------------------------------------
# Queries
# --------------------------------------------------------------------------
MATCH_SELECT = """
SELECT m.*, 
       h.id AS h_id, h.name_en AS h_name_en, h.name_ar AS h_name_ar,
       h.short_name_en AS h_short, h.code AS h_code, h.crest_url AS h_crest,
       a.id AS a_id, a.name_en AS a_name_en, a.name_ar AS a_name_ar,
       a.short_name_en AS a_short, a.code AS a_code, a.crest_url AS a_crest,
       c.id AS c_id, c.name_en AS c_name_en, c.name_ar AS c_name_ar,
       c.image_url AS c_image, c.area_name_en AS c_area_en, c.area_name_ar AS c_area_ar
FROM matches m
JOIN teams h ON h.id = m.home_team_id
JOIN teams a ON a.id = m.away_team_id
JOIN competitions c ON c.id = m.competition_id
"""


def fetch_day(db: psycopg.Connection, day: str) -> List[Dict[str, Any]]:
    rows = db.execute(
        MATCH_SELECT + " WHERE m.match_date = %s ORDER BY m.kickoff_utc, c.name_en",
        (day,),
    ).fetchall()

    groups: dict[str, dict] = {}
    for r in rows:
        key = r["c_id"]
        if key not in groups:
            groups[key] = {"comp": r, "matches": [], "live": 0}
        g_ = groups[key]
        if (r["status"] or "").upper() == "LIVE":
            g_["live"] += 1
        g_["matches"].append(r)

    def sort_key(item):
        # goal.com order only. LIVE MATCHES NEVER REORDER THE LEAGUE LIST -
        # a league keeps its position whether it has live games or not.
        return comp_priority(item["comp"])

    ordered = sorted(groups.values(), key=sort_key)
    for i, grp in enumerate(ordered):
        grp["open"] = bool(grp["live"]) or i < 6
    return rows, ordered


def fetch_match(db: psycopg.Connection, match_id: str):
    m = db.execute(MATCH_SELECT + " WHERE m.id = %s", (match_id,)).fetchone()
    if not m:
        return None

    events = db.execute(
        """SELECT * FROM match_events
           WHERE match_id = %s AND event_type NOT LIKE 'PERIOD_%%'
           ORDER BY sort_order, minute, extra_minute""",
        (match_id,),
    ).fetchall()

    lineups = db.execute(
        """SELECT l.*, p.name_en AS p_name_en, p.name_ar AS p_name_ar, p.image_url AS p_image
           FROM lineups l LEFT JOIN players p ON p.id = l.player_id
           WHERE l.match_id = %s ORDER BY l.is_starter DESC, l.shirt_number""",
        (match_id,),
    ).fetchall()

    managers = db.execute(
        "SELECT * FROM match_managers WHERE match_id = %s", (match_id,)
    ).fetchall()

    stats = db.execute(
        """SELECT * FROM team_match_stats WHERE match_id = %s
           ORDER BY stat_type""",
        (match_id,),
    ).fetchall()

    venue = None
    if m["venue_id"]:
        venue = db.execute("SELECT * FROM venues WHERE id = %s", (m["venue_id"],)).fetchone()

    return {"match": m, "events": events, "lineups": lineups,
            "managers": managers, "stats": stats, "venue": venue}


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------
@app.route("/")
def index():
    return day_page(date_cls.today().isoformat())


@app.route("/day/<day>")
def day_page(day: str):
    d = parse_date(day)
    lang = current_lang()
    mode = day_mode(d)
    db = get_db()
    rows, groups = fetch_day(db, day)

    counts = {"live": 0, "result": 0, "fixture": 0}
    for r in rows:
        s = (r["status"] or "").upper()
        if s == "LIVE":
            counts["live"] += 1
        elif s == "RESULT":
            counts["result"] += 1
        else:
            counts["fixture"] += 1

    return render_template(
        "day.html",
        day=d, mode=mode, groups=groups, counts=counts,
        total=len(rows),
        prev_day=d - timedelta(days=1),
        next_day=d + timedelta(days=1),
        today=date_cls.today(),
        live_refresh=(mode == "today"),
    )


@app.route("/match/<match_id>")
def match_page(match_id: str):
    lang = current_lang()
    data = fetch_match(get_db(), match_id)
    if not data:
        abort(404)
    m = data["match"]
    d = datetime.fromisoformat(m["kickoff_utc"].replace("Z", "+00:00")).date()

    lineup_teams = []
    for side, tid, tname_en, tname_ar, crest in (
        ("home", m["h_id"], m["h_name_en"], m["h_name_ar"], m["h_crest"]),
        ("away", m["a_id"], m["a_name_en"], m["a_name_ar"], m["a_crest"]),
    ):
        players = [row for row in data["lineups"] if row["team_id"] == tid]
        starters = [row for row in players if row["is_starter"]]
        bench = [row for row in players if not row["is_starter"]]
        mgr = next((row for row in data["managers"] if row["team_id"] == tid), None)
        lineup_teams.append({
            "side": side, "id": tid, "name_en": tname_en, "name_ar": tname_ar,
            "crest": crest, "starters": starters, "bench": bench, "manager": mgr,
        })

    # stats: home value / label / away value
    stats_rows = []
    by_type = {}
    for row in data["stats"]:
        by_type.setdefault(row["stat_type"], {})[row["team_id"]] = row["value"]
    label_map = {
        "POSSESSION": ("Possession", "الاستحواذ"),
        "EXPECTED_GOAL": ("Expected goals (xG)", "الأهداف المتوقعة"),
        "SHOT_TOTAL": ("Total shots", "إجمالي التسديدات"),
        "SHOT_ON_TARGET": ("Shots on target", "تسديدات على المرمى"),
        "SHOT_OFF_TARGET": ("Shots off target", "تسديدات خارج المرمى"),
        "CORNER_TOTAL": ("Corners", "الركنيات"),
        "FOUL_TOTAL": ("Fouls", "الأخطاء"),
        "OFFSIDE_TOTAL": ("Offsides", "التسلل"),
        "YELLOW_CARD": ("Yellow cards", "بطاقات صفراء"),
        "RED_CARD": ("Red cards", "بطاقات حمراء"),
        "SAVES": ("Saves", "التصديات"),
        "ATTACK_TOTAL": ("Attacks", "الهجمات"),
        "DANGEROUS_ATTACK_TOTAL": ("Dangerous attacks", "الهجمات الخطيرة"),
        "CROSS_TOTAL": ("Crosses", "العرضيات"),
        "TACKLE_TOTAL": ("Tackles", "الالتحامات"),
    }
    def fmt(v, pct, xg):
        if v is None:
            return "-"
        if pct:
            return f"{v:.0f}%"
        if xg:
            return f"{v:.2f}"
        if abs(v - round(v)) < 0.05:
            return str(int(round(v)))
        return f"{v:.1f}"

    for stat_type, values in by_type.items():
        hv = values.get(m["h_id"])
        av = values.get(m["a_id"])
        if hv is None and av is None:
            continue
        pct = stat_type == "POSSESSION"
        xg = "EXPECTED" in stat_type
        en_label, ar_label = label_map.get(stat_type, (stat_type.replace("_", " ").title(), stat_type))
        total = (hv or 0) + (av or 0)
        if total > 0:
            hr = (hv or 0) / total
        else:
            hr = 0.5
        stats_rows.append({
            "label": ar_label if lang == "ar" else en_label,
            "home": hv, "away": av,
            "home_str": fmt(hv, pct, xg),
            "away_str": fmt(av, pct, xg),
            "home_ratio": hr, "away_ratio": 1 - hr,
        })

    return render_template(
        "match.html",
        m=m, match_day=d, data=data, lineup_teams=lineup_teams,
        stats_rows=stats_rows, venue=data["venue"],
    )


@app.errorhandler(404)
def not_found(_e):
    lang = current_lang()
    return render_template("404.html"), 404


def run(host="0.0.0.0", port=8765, db_url=None, debug=False):
    if db_url:
        app.config["DB_URL"] = backend.resolve_dsn(db_url)
    print(f"* Serving football web view on http://{host}:{port} "
          f"(db: {backend.display_dsn(app.config['DB_URL'])})")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run(port=int(os.environ.get("PORT", 8765)))
