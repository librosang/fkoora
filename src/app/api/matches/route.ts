import { NextRequest, NextResponse } from "next/server";
import { getDayListing } from "@/lib/goal/service";
import { relayConditional } from "@/lib/goal/proxy";

/**
 * GET /api/matches?date&today&major&tz
 *
 * Thin proxy: the data comes from our Python scraper's database via its
 * Flask API (see football-scraper/scraper/api.py) - grouped bilingual day
 * listing, correct for the requesting user's local calendar/timezone.
 *
 * The browser's If-None-Match is forwarded, and the backend's 304 is
 * relayed as a 304: while the data is unchanged, the poll costs a few
 * hundred bytes instead of the full listing JSON.
 */
export const dynamic = "force-dynamic";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export async function GET(req: NextRequest) {
  const sp = req.nextUrl.searchParams;

  let date = sp.get("date") || todayIso();
  let today = sp.get("today") || todayIso();
  if (!DATE_RE.test(date)) date = todayIso();
  if (!DATE_RE.test(today)) today = todayIso();
  const majorOnly = sp.get("major") !== "0";
  // client timezone offset in minutes EAST of UTC (JS: -getTimezoneOffset()),
  // used to align listing days with the user's local calendar
  const tzRaw = parseInt(sp.get("tz") || "0", 10);
  const tzMin = isNaN(tzRaw) ? 0 : Math.max(-840, Math.min(840, tzRaw));

  const listing = await getDayListing(
    date,
    today,
    majorOnly,
    tzMin,
    req.headers.get("if-none-match"),
  );
  return relayConditional(listing);
}
