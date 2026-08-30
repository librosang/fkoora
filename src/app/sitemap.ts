import type { MetadataRoute } from "next";
import { getDayListing } from "@/lib/goal/service";
import { siteUrl, utcToday } from "@/lib/seo";

/**
 * /sitemap.xml - the homepage, a rolling window of day pages (yesterday
 * through +3 days) and the current match pages (/match/<id>) discovered from
 * today's + yesterday's listings. Day URLs are the crawlable landing pages
 * for "matches today / results / fixtures" queries; match URLs are the
 * per-match landing pages. The window rolls forward on every regeneration so
 * stale future dates drop out automatically.
 */

function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/**
 * Next.js writes <loc> values VERBATIM into the XML, so "&" (e.g. the
 * "&lang=en" of an English variant URL) MUST be pre-escaped here. A raw "&"
 * makes the whole sitemap unparsable ("EntityRef: expecting ';'").
 */
function xmlUrl(u: string): string {
  return u
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

/** max match URLs per sitemap generation */
const MAX_MATCH_URLS = 200;
/** the sitemap must never hang on a slow backend */
const SITEMAP_FETCH_BUDGET_MS = 3_500;

/** match ids from today's + yesterday's major listings (fail-safe). */
async function matchIds(): Promise<string[]> {
  const today = utcToday();
  const days = [today, addDays(today, -1)];
  try {
    const listings = await Promise.race([
      Promise.all(days.map((d) => getDayListing(d, today, true, 0))),
      new Promise<null>((resolve) =>
        setTimeout(() => resolve(null), SITEMAP_FETCH_BUDGET_MS),
      ),
    ]);
    if (!listings) return [];
    const ids: string[] = [];
    const seen = new Set<string>();
    for (const listing of listings) {
      if (listing.status !== 200 || !listing.data) continue;
      for (const g of listing.data.groups) {
        for (const m of g.matches) {
          if (seen.has(m.matchId)) continue;
          seen.add(m.matchId);
          ids.push(m.matchId);
          if (ids.length >= MAX_MATCH_URLS) return ids;
        }
      }
    }
    return ids;
  } catch {
    return [];
  }
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const base = siteUrl();
  const today = utcToday();
  const now = new Date();

  const entries: MetadataRoute.Sitemap = [
    {
      url: xmlUrl(`${base}/`),
      lastModified: now,
      changeFrequency: "hourly",
      priority: 1,
    },
    {
      url: xmlUrl(`${base}/?lang=en`),
      lastModified: now,
      changeFrequency: "hourly",
      priority: 0.9,
    },
  ];

  // yesterday .. +3 days (past = results pages, future = fixture pages)
  for (let offset = -1; offset <= 3; offset++) {
    const date = addDays(today, offset);
    const freq = offset <= 0 ? "hourly" : "daily";
    const prio = offset === 0 ? 0.9 : 0.8;
    entries.push({
      url: xmlUrl(`${base}/?date=${date}`),
      lastModified: now,
      changeFrequency: freq,
      priority: prio,
    });
    entries.push({
      url: xmlUrl(`${base}/?date=${date}&lang=en`),
      lastModified: now,
      changeFrequency: freq,
      priority: offset === 0 ? 0.8 : 0.7,
    });
  }

  // per-match pages (today + yesterday)
  for (const id of await matchIds()) {
    entries.push({
      url: xmlUrl(`${base}/match/${encodeURIComponent(id)}`),
      lastModified: now,
      changeFrequency: "hourly",
      priority: 0.7,
    });
  }

  return entries;
}
