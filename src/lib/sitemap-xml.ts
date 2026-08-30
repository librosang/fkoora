/**
 * Sitemap XML generation for the split sitemap setup:
 *
 *   /sitemap.xml            -> sitemap INDEX (a list of child sitemaps)
 *   /sitemaps/main.xml      -> the home page (/, /?lang=en)
 *   /sitemaps/days-YYYY-MM  -> one sitemap PER MONTH of day-listing pages
 *                              (/?date=... rolling window: past 7 + next 3
 *                              days, so a month file never exceeds ~62 URLs)
 *   /sitemaps/matches-N.xml -> per-match pages in chunks of 500 URLs, so the
 *                              setup scales as the backend accumulates
 *                              matches without any single file growing huge
 *
 * Everything is computed at REQUEST time (force-dynamic route handlers): the
 * rolling window moves, new matches appear and SITE_URL changes take effect
 * without a rebuild. XML is built by hand so every value is properly escaped
 * ("&" in "?date=...&lang=en" MUST be &amp; - a raw "&" makes the whole file
 * unparsable: "EntityRef: expecting ';'").
 */
import { getDayListing } from "@/lib/goal/service";
import { matchSlug, matchUrlPath, utcToday } from "@/lib/seo";

/** how many PAST days get a day-listing URL in the sitemap (results pages) */
export const SITEMAP_DAYS_PAST = 7;
/** how many FUTURE days get a day-listing URL in the sitemap (fixture pages) */
export const SITEMAP_DAYS_FUTURE = 3;
/** max match URLs per /sitemaps/matches-N.xml file */
export const MATCHES_PER_SITEMAP = 500;
/** total cap on discovered match URLs (today + yesterday listings) */
export const MAX_MATCH_URLS = 2_000;
/** true -> only major-competition matches in the sitemap (quality over
 *  quantity: obscure matches would send crawlers hammering /api/match/:id) */
export const SITEMAP_MAJOR_ONLY = true;
/** the sitemap must never hang on a slow backend */
const SITEMAP_FETCH_BUDGET_MS = 3_500;

// ---------------------------------------------------------------------------
// date helpers
// ---------------------------------------------------------------------------

export function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

/** The months ("YYYY-MM") touched by the rolling day window, in order. */
export function windowMonths(today: string): string[] {
  const months: string[] = [];
  for (let offset = -SITEMAP_DAYS_PAST; offset <= SITEMAP_DAYS_FUTURE; offset++) {
    const month = addDays(today, offset).slice(0, 7);
    if (!months.includes(month)) months.push(month);
  }
  return months;
}

// ---------------------------------------------------------------------------
// XML building blocks
// ---------------------------------------------------------------------------

/** Escape a string for safe embedding in XML text nodes. */
export function xmlEscape(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

export interface SitemapUrlEntry {
  loc: string;
  lastmod?: string;
  changefreq?: "always" | "hourly" | "daily" | "weekly" | "monthly" | "yearly" | "never";
  priority?: number;
}

/** A complete <urlset> document (child sitemap). */
export function urlSetXml(entries: SitemapUrlEntry[]): string {
  const urls = entries
    .map((e) => {
      const parts = [`    <loc>${xmlEscape(e.loc)}</loc>`];
      if (e.lastmod) parts.push(`    <lastmod>${e.lastmod}</lastmod>`);
      if (e.changefreq) parts.push(`    <changefreq>${e.changefreq}</changefreq>`);
      if (e.priority !== undefined) {
        parts.push(`    <priority>${e.priority.toFixed(1)}</priority>`);
      }
      return `  <url>\n${parts.join("\n")}\n  </url>`;
    })
    .join("\n");
  return (
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    `${urls}\n` +
    `</urlset>\n`
  );
}

export interface SitemapIndexItem {
  loc: string;
  lastmod?: string;
}

/** A complete <sitemapindex> document (the /sitemap.xml root). */
export function sitemapIndexXml(items: SitemapIndexItem[]): string {
  const maps = items
    .map((m) => {
      const parts = [`    <loc>${xmlEscape(m.loc)}</loc>`];
      if (m.lastmod) parts.push(`    <lastmod>${m.lastmod}</lastmod>`);
      return `  <sitemap>\n${parts.join("\n")}\n  </sitemap>`;
    })
    .join("\n");
  return (
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    `${maps}\n` +
    `</sitemapindex>\n`
  );
}

/** Shared response headers for every sitemap XML response. */
export const XML_HEADERS: Record<string, string> = {
  "Content-Type": "application/xml; charset=utf-8",
  // sitemap files are recomputed on demand; this only lets crawlers/CDNs
  // reuse a copy for up to an hour instead of hammering the backend
  "Cache-Control": "public, max-age=3600",
};

// ---------------------------------------------------------------------------
// entry collectors
// ---------------------------------------------------------------------------

/** Home page entries (the "/" landing pages, both languages). */
export function mainEntries(base: string, lastmod: string): SitemapUrlEntry[] {
  return [
    { loc: `${base}/`, lastmod, changefreq: "hourly", priority: 1 },
    { loc: `${base}/?lang=en`, lastmod, changefreq: "hourly", priority: 0.9 },
  ];
}

/**
 * Day-listing entries for ONE month ("YYYY-MM") - only the days of the
 * rolling window that fall inside that month. Each day has an Arabic (main)
 * and an English (?lang=en) URL.
 */
export function dayEntriesForMonth(
  base: string,
  today: string,
  month: string,
  lastmod: string,
): SitemapUrlEntry[] {
  const entries: SitemapUrlEntry[] = [];
  for (
    let offset = -SITEMAP_DAYS_PAST;
    offset <= SITEMAP_DAYS_FUTURE;
    offset++
  ) {
    const date = addDays(today, offset);
    if (date.slice(0, 7) !== month) continue;
    const isToday = date === today;
    entries.push({
      loc: `${base}/?date=${date}`,
      lastmod,
      changefreq: isToday ? "hourly" : "daily",
      priority: isToday ? 0.9 : 0.8,
    });
    entries.push({
      loc: `${base}/?date=${date}&lang=en`,
      lastmod,
      changefreq: isToday ? "hourly" : "daily",
      priority: isToday ? 0.8 : 0.7,
    });
  }
  return entries;
}

/**
 * Absolute match-page URLs (with the /match/<id>/<slug> canonical form),
 * discovered from today's + yesterday's listings. Fail-safe: a slow or down
 * backend yields an empty list (the sitemap then simply has no match URLs
 * until the next crawl) instead of timing the response out.
 */
export async function collectMatchUrls(base: string): Promise<string[]> {
  const today = utcToday();
  const days = [today, addDays(today, -1)];
  try {
    const listings = await Promise.race([
      Promise.all(days.map((d) => getDayListing(d, today, SITEMAP_MAJOR_ONLY, 0))),
      new Promise<null>((resolve) =>
        setTimeout(() => resolve(null), SITEMAP_FETCH_BUDGET_MS),
      ),
    ]);
    if (!listings) return [];
    const urls: string[] = [];
    const seen = new Set<string>();
    for (const listing of listings) {
      if (listing.status !== 200 || !listing.data) continue;
      for (const g of listing.data.groups) {
        for (const m of g.matches) {
          if (seen.has(m.matchId)) continue;
          seen.add(m.matchId);
          urls.push(`${base}${matchUrlPath(m.matchId, matchSlug(m))}`);
          if (urls.length >= MAX_MATCH_URLS) return urls;
        }
      }
    }
    return urls;
  } catch {
    return [];
  }
}

/** Number of /sitemaps/matches-N.xml chunks for a discovered URL count. */
export function matchChunkCount(urlCount: number): number {
  return Math.max(1, Math.ceil(urlCount / MATCHES_PER_SITEMAP));
}
