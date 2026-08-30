"""
Task 11 verification: "loading in place of old data" (updated for the
buttonless auto-refresh UI).

Approach: intercept every /api/matches, /api/match/*, /api/competition/*
request. The upstream response is fetched immediately but NOT delivered to the
page until we explicitly release it - so the app stays in its loading state
deterministically until we let the data land. fail_mode injects 502s instead.

Checks:
1. initial load -> list skeleton (role=status) shows, then data
2. date switch (tomorrow quick btn) -> skeleton REPLACES old day's data
3. NO manual refresh button; a failed load shows the error box with the
   auto-retry hint (old data hidden) and heals itself ~30s later, no clicks
4. match dialog -> in-dialog loader, tab content absent while loading;
   second match -> loader again, first match's tabs never shown (no stale)
5. competition dialog -> loader while info loads; round switch -> loader
   replaces previous round's rows
6. no console/page errors
"""

import re
import sys
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:3000"
results = []
pending = []  # [(route, response)] held until released
fail_mode = {"on": False}  # when on: /api/matches gets a 502 (no upstream call)


def check(name: str, ok: bool, extra: str = "") -> None:
    results.append((name, ok, extra))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{extra}]" if extra else ""))


def stash(route):
    """Fetch upstream now, deliver to the page only when released."""
    if fail_mode["on"] and "/api/matches" in route.request.url:
        route.fulfill(status=502, content_type="application/json",
                      body='{"error": "boom"}')
        return
    try:
        resp = route.fetch()
        pending.append((route, resp))
    except Exception:
        route.continue_()


def release(page, settle_ms: int = 700, rounds: int = 8):
    """Deliver all held responses; keep draining follow-up requests."""
    for _ in range(rounds):
        if not pending:
            break
        batch = pending[:]
        pending.clear()
        for route, resp in batch:
            try:
                route.fulfill(response=resp)
            except Exception:
                pass
        page.wait_for_timeout(settle_ms)


def wait_loading_gone(page, sel, timeout=30000):
    page.wait_for_selector(sel, state="detached", timeout=timeout)


with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 390, "height": 844})
    page = ctx.new_page()

    console_errors = []
    page.on(
        "console",
        lambda m: console_errors.append(m.text)
        if m.type == "error" and "Failed to load resource" not in m.text
        else None,
    )
    page.on("pageerror", lambda e: console_errors.append(str(e)))

    page.route(re.compile(r".*/api/matches.*"), stash)
    page.route(re.compile(r".*/api/match/.*"), stash)
    page.route(re.compile(r".*/api/competition/.*"), stash)

    SKELETON = 'main [role="status"][aria-busy="true"]'
    DIALOG = '[role="dialog"]'

    # ---- 1. initial load: skeleton first, then data ----------------------
    page.goto(BASE, wait_until="domcontentloaded")
    page.wait_for_selector(SKELETON, timeout=30000)
    check("initial load: skeleton shows", True)
    release(page)
    wait_loading_gone(page, SKELETON)
    page.wait_for_selector('main button[role="listitem"]', timeout=10000)
    check("initial load: data renders", True)
    page.wait_for_timeout(500)

    # marker from the old day to detect stale data later
    old_txt = page.locator("main").inner_text()
    marker_row = page.locator('main button[role="listitem"]').first.inner_text()

    # ---- 2. date switch: skeleton replaces old data -----------------------
    page.get_by_role("button", name="غداً", exact=True).filter(has_text="غداً").click()
    page.wait_for_timeout(900)  # request is stashed -> app must be loading
    check("date switch: skeleton shows", page.locator(SKELETON).count() == 1)
    now_txt = page.locator("main").inner_text()
    check(
        "date switch: OLD data hidden",
        marker_row.split("\n")[0] not in now_txt,
        marker_row.split("\n")[0][:30],
    )
    release(page)
    wait_loading_gone(page, SKELETON)
    ok_new = page.locator('main button[role="listitem"]').count() > 0 or "لا توجد مباريات" in page.locator("main").inner_text()
    check("date switch: new data renders (or empty state)", ok_new)

    # if tomorrow had no matches, go back to today for the dialog tests
    if page.locator('main button[role="listitem"]').count() == 0:
        page.get_by_role("button", name="اليوم", exact=True).filter(has_text="اليوم").click()
        page.wait_for_selector(SKELETON, timeout=10000)
        release(page)
        wait_loading_gone(page, SKELETON)
        page.wait_for_selector('main button[role="listitem"]', timeout=15000)

    # ---- 3. no refresh button; failed load heals itself -------------------
    # the toolbar refresh button is gone - nothing for the user to spam
    check("refresh button removed",
          page.locator('main button[aria-label="إعادة المحاولة"]').count() == 0)

    # go to yesterday first (guaranteed != tomorrow, so the failing switch
    # below really fires even if case 2 already left us on tomorrow)
    page.get_by_role("button", name="أمس", exact=True).filter(has_text="أمس").click()
    page.wait_for_selector(SKELETON, timeout=10000)
    page.wait_for_timeout(900)  # let the request reach the route interceptor
    release(page)
    wait_loading_gone(page, SKELETON)
    page.wait_for_timeout(400)
    rows_now = page.locator('main button[role="listitem"]')
    marker = (rows_now.first.inner_text().split("\n")[0]
              if rows_now.count() > 0 else "")

    # a failed date switch -> error box + auto-retry hint, old data hidden
    fail_mode["on"] = True
    page.get_by_role("button", name="غداً", exact=True).filter(has_text="غداً").click()
    page.wait_for_timeout(900)
    err_txt = page.locator("main").inner_text()
    check("failed load: error box + auto-retry hint",
          "تعذّر جلب البيانات" in err_txt and "سيُعاد المحاولة تلقائياً" in err_txt)
    if marker:
        check("failed load: OLD data hidden", marker not in err_txt, marker[:30])
    check("failed load: no retry button",
          page.locator('main button:has-text("إعادة المحاولة")').count() == 0)
    fail_mode["on"] = False
    # the app retries by itself after ~30s - the retry is stashed, then released
    page.wait_for_selector(SKELETON, timeout=60000)
    page.wait_for_timeout(900)  # let the retry request reach the interceptor
    release(page)
    wait_loading_gone(page, SKELETON)
    ok_heal = (page.locator('main button[role="listitem"]').count() > 0
               or "لا توجد مباريات" in page.locator("main").inner_text())
    check("auto-retry recovered without any click", ok_heal)

    # dialog tests below need rows - if tomorrow is empty, go back to today
    if page.locator('main button[role="listitem"]').count() == 0:
        page.get_by_role("button", name="اليوم", exact=True).filter(has_text="اليوم").click()
        page.wait_for_selector(SKELETON, timeout=10000)
        page.wait_for_timeout(900)  # let the request reach the interceptor
        release(page)
        wait_loading_gone(page, SKELETON)
        page.wait_for_selector('main button[role="listitem"]', timeout=15000)

    # ---- 4. match dialog: loader in place, no stale content ---------------
    rows = page.locator('main button[role="listitem"]')
    rows.first.click()
    page.wait_for_selector(DIALOG, timeout=10000)
    page.wait_for_timeout(900)  # detail request stashed
    body = page.locator(f"{DIALOG} [aria-busy='true']")
    check(
        "match dialog: loader shows while detail loads",
        body.count() > 0 and "تحميل" in body.first.inner_text(),
    )
    check(
        "match dialog: tabs NOT rendered while loading",
        page.locator(f"{DIALOG} [role='tablist']").count() == 0,
    )
    release(page)
    wait_loading_gone(page, f"{DIALOG} [aria-busy='true']")
    check(
        "match dialog: detail renders",
        page.locator(f"{DIALOG} [role='tablist']").count() > 0,
    )
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)

    # second match -> loader again, first match's content never flashes
    if rows.count() > 2:
        rows.nth(2).click()
        page.wait_for_selector(DIALOG, timeout=10000)
        page.wait_for_timeout(900)
        body = page.locator(f"{DIALOG} [aria-busy='true']")
        check(
            "second match: loader shows again",
            body.count() > 0 and "تحميل" in body.first.inner_text(),
        )
        check(
            "second match: first match's tabs never shown (no stale)",
            page.locator(f"{DIALOG} [role='tablist']").count() == 0,
        )
        release(page)
        wait_loading_gone(page, f"{DIALOG} [aria-busy='true']")
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

    # ---- 5. competition dialog --------------------------------------------
    comp_btn = page.locator('button[aria-label^="صفحة البطولة"]').first
    comp_btn.click()
    page.wait_for_selector(DIALOG, timeout=10000)
    page.wait_for_timeout(900)  # info request stashed
    body = page.locator(f"{DIALOG} [aria-busy='true']")
    check(
        "competition dialog: loader shows while info loads",
        body.count() > 0 and "تحميل" in body.first.inner_text(),
    )
    release(page)  # info + active round
    wait_loading_gone(page, f"{DIALOG} [aria-busy='true']", timeout=30000)
    page.wait_for_timeout(800)
    check(
        "competition dialog: content renders",
        page.locator(f"{DIALOG} [role='tablist']").count() > 0,
    )

    # round switch inside the dialog -> loader replaces previous round rows
    page.get_by_role("tab", name="الجولات والنتائج").click()
    page.wait_for_timeout(1200)  # active round may still be arriving
    release(page)
    page.wait_for_timeout(800)
    chips = page.locator('[role="tablist"][aria-label="اختر الجولة"] button[role="tab"]')
    inactive = [i for i in range(chips.count()) if chips.nth(i).get_attribute("aria-selected") != "true"]
    if chips.count() > 1 and inactive:
        before_rows = page.locator(f"{DIALOG} button[role='listitem']").count()
        chips.nth(inactive[-1]).click()  # a far-away round
        page.wait_for_timeout(900)  # round request stashed
        dlg_txt = page.locator(DIALOG).inner_text()
        check(
            "round switch: loader shows, old round rows hidden",
            "تحميل" in dlg_txt and page.locator(f"{DIALOG} button[role='listitem']").count() == 0,
            f"rowsBefore={before_rows}",
        )
        release(page)
        page.wait_for_timeout(900)
        dlg_txt = page.locator(DIALOG).inner_text()
        ok = (
            page.locator(f"{DIALOG} button[role='listitem']").count() > 0
            or "لا توجد مباريات في هذه الجولة" in dlg_txt
        )
        check("round switch: new round renders (or empty state)", ok)
    else:
        print("SKIP  round switch (single round visible)")

    page.keyboard.press("Escape")
    page.wait_for_timeout(400)

    # ---- 6. console errors -------------------------------------------------
    check("no console/page errors", len(console_errors) == 0, "; ".join(console_errors[:3]))

    browser.close()

fails = [r for r in results if not r[1]]
print(f"\n{len(results) - len(fails)}/{len(results)} checks passed")
sys.exit(1 if fails else 0)
