import { siteUrl } from "@/lib/seo";

/**
 * /robots.txt - generated at REQUEST time (a route handler instead of the
 * build-time robots.ts convention) so the Sitemap/Host lines always reflect
 * the CURRENT site URL: the runtime SITE_URL override works without a
 * rebuild, and the hardcoded production fallback means localhost can never
 * leak into a deployed robots.txt.
 */
export const dynamic = "force-dynamic";

export async function GET() {
  const base = siteUrl();
  const host = base.replace(/^https?:\/\//i, "");
  const body =
    `User-agent: *\n` +
    `Allow: /\n` +
    `Disallow: /api/\n` +
    `\n` +
    `Sitemap: ${base}/sitemap.xml\n` +
    `Host: ${host}\n`;
  return new Response(body, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
}
