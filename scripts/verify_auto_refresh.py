#!/usr/bin/env python3
"""Deterministic verification of the adaptive auto-refresh cadence.

Uses Playwright's fake clock (page.clock) + route interception, so 30-minute
idle windows and kickoff-triggered refreshes are tested in seconds instead of
hours. The fetch wrapper (init script) stamps every /api/matches request with
the fake time (_t=...), which the route handler uses to serve time-dependent
payloads (e.g. a match that flips FIXTURE -> LIVE at its kickoff).

Cases:
  1. LIVE match running              -> poll every 60s
  2. nothing live, nothing upcoming  -> poll every 30 min
  3. no live, next kickoff in 5 min  -> NO poll before kickoff; poll right
     after kickoff (+90s buffer), then flip to the 60s live cadence
  4. kickoff passed, still FIXTURE   -> 60s cadence (startPending watch window)
  5. hidden tab -> no polls at all; returning to the tab -> immediate refresh
  6. failed load (502s) -> NO manual refresh button anywhere; the app
     auto-retries every 30s and recovers on its own
  + indicator label: "كل دقيقة" when live, "كل 30 دقيقة" when idle
"""
import json
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:3000"
T0_DT = datetime(2026, 8, 28, 14, 0, 0, tzinfo=timezone.utc)
T0 = int(T0_DT.timestamp() * 1000)
DAY = "2026-08-28"

MIN, SEC = 60_000, 1_000

INIT_JS = """
window.__fetchTimes = [];
const origFetch = window.fetch.bind(window);
window.fetch = function (input, init) {
  let url = String(typeof input === 'string' ? input : (input && input.url) || '');
  if (url.includes('/api/matches')) {
    window.__fetchTimes.push(Date.now());
    const sep = url.includes('?') ? '&' : '?';
    url = url + sep + '_t=' + Date.now();
    return origFetch(url, init);
  }
  return origFetch(input, init);
};
"""


def kickoff_iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z"
    )


def match(mid, kickoff_ms, status, hs=None, as_=None):
    return {
        "matchId": mid,
        "kickoffUtc": kickoff_iso(kickoff_ms) if kickoff_ms else None,
        "status": status,
        "period": None,
        "homeTeam": {"id": "h1", "nameEn": "Home FC", "nameAr": "هوم", "crestUrl": None},
        "awayTeam": {"id": "a1", "nameEn": "Away FC", "nameAr": "أوي", "crestUrl": None},
        "competition": {
            "id": "c1", "nameEn": "Test League", "nameAr": "دوري الاختبار",
            "areaNameEn": "Testland", "areaNameAr": "تستلاند", "areaCode": "TL",
        },
        "homeScore": hs, "awayScore": as_,
        "homeRedCards": 0, "awayRedCards": 0,
    }


def make_handler(payloads, fail_until_ms=None):
    """payloads: [(from_ms, matches)]; fail_until_ms: 502 for _t below it."""
    def handler(route):
        q = parse_qs(urlparse(route.request.url).query)
        t = int(q.get("_t", [str(T0)])[0])
        if fail_until_ms is not None and t < fail_until_ms:
            route.fulfill(status=502, content_type="application/json",
                          body='{"error": "boom"}')
            return
        date = q.get("date", [DAY])[0]
        chosen = payloads[0][1]
        for frm, ms in payloads:
            if t >= frm:
                chosen = ms
        body = {
            "date": date, "dayType": "today",
            "generatedAt": kickoff_iso(t),
            "totalMatches": len(chosen),
            "groups": [{"competition": {
                "id": "c1", "nameEn": "Test League", "nameAr": "دوري الاختبار",
                "areaNameEn": "Testland", "areaNameAr": "تستلاند",
            }, "matches": chosen, "isMajor": True}],
        }
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(body))
    return handler


def advance(page, total_ms, step_ms=30_000, settle_ms=60):
    done = 0
    while done < total_ms:
        chunk = min(step_ms, total_ms - done)
        page.clock.fast_forward(chunk)
        page.wait_for_timeout(settle_ms)
        done += chunk


def times(page):
    return page.evaluate("() => window.__fetchTimes.slice()")


def approx(expected_ms, actual_ms, tol_ms=5_000):
    return abs(actual_ms - expected_ms) <= tol_ms


def main():
    results = []

    def check(case, name, ok, detail=""):
        results.append((case, name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  {detail}")

    with sync_playwright() as pw:
        # ------------------------------------------------------------------
        print("Case 1: live match running -> 60s cadence")
        browser = pw.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(INIT_JS)
        page.route("**/api/matches*", make_handler(
            [(0, [match("m1", T0 - 30 * MIN, "LIVE", 1, 0)])]))
        page.clock.install(time=T0_DT)
        page.goto(BASE)
        page.wait_for_timeout(700)
        advance(page, 5 * MIN)
        ts = times(page)
        check(1, "fetch count (initial + 5 polls)", len(ts) == 6, f"n={len(ts)}")
        check(1, "60s intervals", len(ts) == 6 and all(
            approx(60_000, ts[i + 1] - ts[i]) for i in range(5)),
            f"deltas={[ts[i+1]-ts[i] for i in range(len(ts)-1)]}")
        label = page.locator("main").inner_text()
        check(1, "indicator shows 'كل دقيقة'", "تحديث تلقائي كل دقيقة" in label)
        check(1, "no page errors", not errors, str(errors[:2]))
        browser.close()

        # ------------------------------------------------------------------
        print("Case 2: nothing live, nothing upcoming -> 30 min cadence")
        browser = pw.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(INIT_JS)
        page.route("**/api/matches*", make_handler(
            [(0, [match("m1", T0 - 3 * 60 * MIN, "RESULT", 2, 1)])]))
        page.clock.install(time=T0_DT)
        page.goto(BASE)
        page.wait_for_timeout(700)
        advance(page, 65 * MIN, step_ms=60_000)
        ts = times(page)
        check(2, "fetch count (initial + 2 idle polls in 65 min)", len(ts) == 3,
              f"n={len(ts)}")
        check(2, "30min intervals", len(ts) == 3 and approx(
            30 * MIN, ts[1] - ts[0]) and approx(30 * MIN, ts[2] - ts[1]),
            f"deltas={[ts[i+1]-ts[i] for i in range(len(ts)-1)]}")
        label = page.locator("main").inner_text()
        check(2, "indicator shows 'كل 30 دقيقة'", "تحديث تلقائي كل 30 دقيقة" in label)
        check(2, "no page errors", not errors, str(errors[:2]))
        browser.close()

        # ------------------------------------------------------------------
        print("Case 3: next kickoff in 5 min -> poll at kickoff+90s, then live 60s")
        browser = pw.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(INIT_JS)
        page.route("**/api/matches*", make_handler([
            (0, [match("m1", T0 + 5 * MIN, "FIXTURE")]),
            (T0 + 5 * MIN, [match("m1", T0 + 5 * MIN, "LIVE", 0, 0)]),
        ]))
        page.clock.install(time=T0_DT)
        page.goto(BASE)
        page.wait_for_timeout(700)
        advance(page, 8 * MIN)
        ts = times(page)
        check(3, "no intermediate poll while waiting",
              not any(60_000 < t - T0 < 380_000 for t in ts),
              f"ts={[t-T0 for t in ts]}")
        check(3, "poll right after kickoff (kickoff+90s)",
              any(approx(390_000, t - T0, 3_000) for t in ts),
              f"ts={[t-T0 for t in ts]}")
        check(3, "flips to 60s live cadence afterwards",
              any(approx(450_000, t - T0, 3_000) for t in ts),
              f"ts={[t-T0 for t in ts]}")
        check(3, "exactly 3 fetches in 8 min", len(ts) == 3, f"n={len(ts)}")
        check(3, "no page errors", not errors, str(errors[:2]))
        browser.close()

        # ------------------------------------------------------------------
        print("Case 4: kickoff passed but still FIXTURE -> 60s watch window")
        browser = pw.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(INIT_JS)
        page.route("**/api/matches*", make_handler(
            [(0, [match("m1", T0 - 2 * MIN, "FIXTURE")])]))
        page.clock.install(time=T0_DT)
        page.goto(BASE)
        page.wait_for_timeout(700)
        advance(page, 4 * MIN)
        ts = times(page)
        check(4, "fetch count (initial + 4 polls)", len(ts) == 5, f"n={len(ts)}")
        check(4, "60s intervals", len(ts) == 5 and all(
            approx(60_000, ts[i + 1] - ts[i]) for i in range(4)),
            f"deltas={[ts[i+1]-ts[i] for i in range(len(ts)-1)]}")
        check(4, "no page errors", not errors, str(errors[:2]))
        browser.close()

        # ------------------------------------------------------------------
        print("Case 5: hidden tab skips polls; return -> immediate refresh")
        browser = pw.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(INIT_JS)
        page.route("**/api/matches*", make_handler(
            [(0, [match("m1", T0 - 30 * MIN, "LIVE", 1, 0)])]))
        page.clock.install(time=T0_DT)
        page.goto(BASE)
        page.wait_for_timeout(700)
        page.evaluate("""() => {
            Object.defineProperty(document, 'visibilityState',
                { get: () => 'hidden', configurable: true });
        }""")
        advance(page, 3 * MIN)
        ts = times(page)
        check(5, "no polls while hidden", len(ts) == 1, f"n={len(ts)}")
        page.evaluate("""() => {
            Object.defineProperty(document, 'visibilityState',
                { get: () => 'visible', configurable: true });
            document.dispatchEvent(new Event('visibilitychange'));
        }""")
        page.wait_for_timeout(300)
        ts = times(page)
        check(5, "immediate refresh on return", len(ts) == 2, f"n={len(ts)}")
        advance(page, 1 * MIN)
        ts = times(page)
        check(5, "60s cadence resumes after return",
              len(ts) == 3 and approx(60_000, ts[2] - ts[1]),
              f"deltas={[ts[i+1]-ts[i] for i in range(len(ts)-1)]}")
        check(5, "no page errors", not errors, str(errors[:2]))
        browser.close()

        # ------------------------------------------------------------------
        print("Case 6: failed load auto-retries every 30s (no refresh button)")
        browser = pw.chromium.launch()
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(INIT_JS)
        # 502 for the first ~45s of fake time (initial load + first retry)
        page.route("**/api/matches*", make_handler(
            [(0, [match("m1", T0 - 3 * 60 * MIN, "RESULT", 2, 1)])],
            fail_until_ms=T0 + 45_000))
        page.clock.install(time=T0_DT)
        page.goto(BASE)
        page.wait_for_timeout(700)
        check(6, "no refresh button in the DOM",
              page.locator('button[aria-label="إعادة المحاولة"]').count() == 0)
        err_txt = page.locator("main").inner_text()
        check(6, "error box + auto-retry hint",
              "تعذّر جلب البيانات" in err_txt
              and "سيُعاد المحاولة تلقائياً" in err_txt)
        advance(page, 30 * SEC, settle_ms=200)  # retry #1 at ~30s: still 502
        ts = times(page)
        check(6, "auto-retry fired after 30s",
              len(ts) == 2 and approx(30_000, ts[1] - ts[0], 3_000),
              f"deltas={[ts[i+1]-ts[i] for i in range(len(ts)-1)]}")
        check(6, "still in error state (502 window)",
              "تعذّر جلب البيانات" in page.locator("main").inner_text())
        advance(page, 30 * SEC, settle_ms=200)  # retry #2 at ~60s: recovers
        page.wait_for_timeout(400)
        txt = page.locator("main").inner_text()
        check(6, "recovered by itself (data renders)",
              "دوري الاختبار" in txt and "تعذّر جلب البيانات" not in txt)
        check(6, "idle cadence indicator after recovery",
              "تحديث تلقائي كل 30 دقيقة" in txt)
        ts = times(page)
        check(6, "second retry 30s after the first",
              len(ts) == 3 and approx(30_000, ts[2] - ts[1], 3_000),
              f"deltas={[ts[i+1]-ts[i] for i in range(len(ts)-1)]}")
        check(6, "no page errors", not errors, str(errors[:2]))
        browser.close()

    failed = [r for r in results if not r[2]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks PASS")
    if failed:
        for case, name, _, detail in failed:
            print(f"  FAILED case {case}: {name} {detail}")
        sys.exit(1)


if __name__ == "__main__":
    main()
