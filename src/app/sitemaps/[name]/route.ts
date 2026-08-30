import {
  collectMatchUrls,
  collectCompetitionUrls,
  competitionChunkCount,
  dayEntriesForMonth,
  mainEntries,
  matchChunkCount,
  MATCHES_PER_SITEMAP,
  COMPETITIONS_PER_SITEMAP,
  urlSetXml,
  windowMonths,
  XML_HEADERS,
} from "@/lib/sitemap-xml";
import { siteUrl, utcToday } from "@/lib/seo";

/**
 * Child sitemaps referenced by the /sitemap.xml index:
 *
 *   /sitemaps/main.xml           - "/" and "/?lang=en"
 *   /sitemaps/days-YYYY-MM.xml   - the day-listing URLs of ONE month that
 *                                  fall inside the rolling window (past 7 +
 *                                  next 3 days). Months outside the window
 *                                  404 - the index never lists them, so
 *                                  stale files simply disappear.
 *   /sitemaps/matches-N.xml      - match pages (canonical /match/<id>/<slug>
 *                                  URLs in BOTH languages), 500 per file.
 *                                  Chunks beyond the discovered data 404.
 *   /sitemaps/competitions-N.xml - competition pages (canonical
 *                                  /competition/<id>/<slug> URLs in BOTH
 *                                  languages), 500 per file.
 *
 * Everything is computed per request (fail-safe on backend errors).
 */
export const dynamic = "force-dynamic";

interface RouteContext {
  params: Promise<{ name: string }>;
}

export async function GET(_req: Request, ctx: RouteContext): Promise<Response> {
  const { name } = await ctx.params;
  const base = siteUrl();
  const now = new Date().toISOString();
  const today = utcToday();

  if (name === "main.xml") {
    return new Response(urlSetXml(mainEntries(base, now)), {
      headers: XML_HEADERS,
    });
  }

  // days-YYYY-MM.xml - day listings grouped per calendar month
  const daysMatch = /^days-(\d{4}-\d{2})\.xml$/.exec(name);
  if (daysMatch) {
    const month = daysMatch[1];
    if (!windowMonths(today).includes(month)) {
      return new Response("Not Found", { status: 404 });
    }
    const entries = dayEntriesForMonth(base, today, month, now);
    return new Response(urlSetXml(entries), { headers: XML_HEADERS });
  }

  // matches-N.xml - match pages in 500-URL chunks (AR + EN per match)
  const matchesMatch = /^matches-(\d+)\.xml$/.exec(name);
  if (matchesMatch) {
    const page = Number.parseInt(matchesMatch[1], 10);
    if (!Number.isInteger(page) || page < 1 || page > 1000) {
      return new Response("Not Found", { status: 404 });
    }
    const urls = await collectMatchUrls(base);
    if (page > matchChunkCount(urls.length)) {
      return new Response("Not Found", { status: 404 });
    }
    const slice = urls.slice(
      (page - 1) * MATCHES_PER_SITEMAP,
      page * MATCHES_PER_SITEMAP,
    );
    const entries = slice.map((loc) => ({
      loc,
      lastmod: now,
      changefreq: "hourly" as const,
      priority: 0.7,
    }));
    return new Response(urlSetXml(entries), { headers: XML_HEADERS });
  }

  // competitions-N.xml - competition pages in 500-URL chunks (AR + EN each)
  const compsMatch = /^competitions-(\d+)\.xml$/.exec(name);
  if (compsMatch) {
    const page = Number.parseInt(compsMatch[1], 10);
    if (!Number.isInteger(page) || page < 1 || page > 1000) {
      return new Response("Not Found", { status: 404 });
    }
    const urls = await collectCompetitionUrls(base);
    if (page > competitionChunkCount(urls.length)) {
      return new Response("Not Found", { status: 404 });
    }
    const slice = urls.slice(
      (page - 1) * COMPETITIONS_PER_SITEMAP,
      page * COMPETITIONS_PER_SITEMAP,
    );
    const entries = slice.map((loc) => ({
      loc,
      lastmod: now,
      changefreq: "daily" as const,
      priority: 0.6,
    }));
    return new Response(urlSetXml(entries), { headers: XML_HEADERS });
  }

  return new Response("Not Found", { status: 404 });
}
