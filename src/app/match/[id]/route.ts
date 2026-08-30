import { getMatchDetail } from "@/lib/goal/service";
import type { MatchDetail } from "@/lib/goal/types";
import { matchUrlPair } from "@/lib/seo";
import { notFoundHtml, retryHtml } from "@/lib/bare-page";

/**
 * GET /match/<id> - LEGACY match URL (no slug).
 *
 * A Route Handler (not a page) so it can answer with a REAL HTTP 308 before
 * anything streams: redirecting from a page component would arrive after
 * streaming began and Next.js would degrade it to a 200 + <meta refresh>.
 *
 * It fetches the match detail once, computes the canonical URL for the
 * REQUESTED language (?lang=en -> the English slug URL, default Arabic slug
 * URL) and 308-redirects there - so every legacy link (old sitemap entries,
 * shared URLs, search indices) consolidates its signals on the canonical
 * per-language URL.
 */
export const dynamic = "force-dynamic";

/** how long the detail fetch may take before falling back to the retry page */
const REDIRECT_FETCH_BUDGET_MS = 4_000;

interface RouteContext {
  params: Promise<{ id: string }>;
}

export async function GET(req: Request, ctx: RouteContext): Promise<Response> {
  const { id } = await ctx.params;
  const url = new URL(req.url);
  const langEn = url.searchParams.get("lang") === "en";

  let status = 0;
  let detail: MatchDetail | null = null;
  try {
    const result = await Promise.race([
      getMatchDetail(id),
      new Promise<null>((resolve) =>
        setTimeout(() => resolve(null), REDIRECT_FETCH_BUDGET_MS),
      ),
    ]);
    if (result === null) {
      status = 0; // budget hit
    } else if (result.status === 200 && result.data) {
      status = 200;
      detail = result.data;
    } else {
      status = result.status;
    }
  } catch {
    status = 0;
  }

  // happy path: permanent redirect to the canonical URL of the requested
  // language (the slug itself carries the language - no query param needed,
  // except the no-Arabic-names edge case where matchUrlPair keeps ?lang=en).
  // The Location is the PATH (+query), not an absolute URL: relative Location
  // headers are valid (RFC 7231) and resolve against the user's own origin,
  // so an internal hostname can never leak out through a misconfigured proxy.
  if (detail) {
    const pair = matchUrlPair(id, detail);
    const target = langEn ? pair.en : pair.ar;
    return new Response(null, {
      status: 308,
      headers: {
        Location: target,
        "Cache-Control": "public, max-age=3600",
      },
    });
  }

  // the backend knows this match does not exist: a real 404 (better for
  // crawlers than the not-found page's 200 + noindex quirk in Next 16)
  if (status === 404) {
    return new Response(
      notFoundHtml(langEn, {
        ar: "المباراة",
        en: "Match",
        bodyAr: "هذه المباراة غير موجودة أو تمت إزالتها.",
      }),
      { status: 404, headers: { "Content-Type": "text/html; charset=utf-8" } },
    );
  }

  // backend slow/unreachable: the slug can't be computed yet - serve a
  // noindex page that retries itself (crawlers skip it, users get through)
  return new Response(retryHtml(langEn, { ar: "المباراة", en: "match" }), {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}
