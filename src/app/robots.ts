import type { MetadataRoute } from "next";
import { siteUrl } from "@/lib/seo";

/**
 * /robots.txt - crawl everything user-facing, keep the API proxies out of
 * the index (they are JSON endpoints used by the client).
 */
export default function robots(): MetadataRoute.Robots {
  const base = siteUrl();
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: ["/api/"],
      },
    ],
    sitemap: `${base}/sitemap.xml`,
    host: base,
  };
}
