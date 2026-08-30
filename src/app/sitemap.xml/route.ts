import {
  collectMatchUrls,
  matchChunkCount,
  sitemapIndexXml,
  windowMonths,
  XML_HEADERS,
} from "@/lib/sitemap-xml";
import { siteUrl, utcToday } from "@/lib/seo";

/**
 * /sitemap.xml - sitemap INDEX.
 *
 * Points crawlers at the split child sitemaps instead of listing every URL
 * in one giant file:
 *   /sitemaps/main.xml          - home page (/, /?lang=en)
 *   /sitemaps/days-YYYY-MM.xml  - day-listing pages, one file per month
 *   /sitemaps/matches-N.xml     - match pages, 500 URLs per file
 *
 * Generated at REQUEST time (never frozen at build): the rolling day window
 * advances, freshly played matches appear and runtime SITE_URL changes are
 * respected immediately.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  const base = siteUrl();
  const now = new Date().toISOString();
  const today = utcToday();

  const items = [
    { loc: `${base}/sitemaps/main.xml`, lastmod: now },
    ...windowMonths(today).map((month) => ({
      loc: `${base}/sitemaps/days-${month}.xml`,
      lastmod: now,
    })),
  ];

  // match sitemaps: one entry per 500-URL chunk (at least one, so crawlers
  // keep discovering /sitemaps/matches-1.xml even when the backend hiccups)
  const matchUrls = await collectMatchUrls(base);
  for (let i = 1; i <= matchChunkCount(matchUrls.length); i++) {
    items.push({ loc: `${base}/sitemaps/matches-${i}.xml`, lastmod: now });
  }

  return new Response(sitemapIndexXml(items), { headers: XML_HEADERS });
}
