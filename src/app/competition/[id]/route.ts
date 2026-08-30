import { getCompetition } from "@/lib/goal/service";
import type { CompetitionInfo } from "@/lib/goal/types";
import { compUrlPair } from "@/lib/seo";
import { notFoundHtml, retryHtml } from "@/lib/bare-page";

/**
 * GET /competition/<id> - legacy competition URL (no slug).
 *
 * Same treatment the matches get: a Route Handler (not a page) so it can
 * answer with a REAL HTTP 308 before anything streams. It fetches the
 * competition info once, computes the canonical URL for the REQUESTED
 * language (?lang=en -> the English slug URL, default the Arabic slug URL)
 * and redirects there permanently.
 */
export const dynamic = "force-dynamic";

/** how long the info fetch may take before falling back to the retry page */
const REDIRECT_FETCH_BUDGET_MS = 4_000;

interface RouteContext {
  params: Promise<{ id: string }>;
}

export async function GET(req: Request, ctx: RouteContext): Promise<Response> {
  const { id } = await ctx.params;
  const url = new URL(req.url);
  const langEn = url.searchParams.get("lang") === "en";

  let status = 0;
  let info: CompetitionInfo | null = null;
  try {
    const result = await Promise.race([
      getCompetition(id),
      new Promise<null>((resolve) =>
        setTimeout(() => resolve(null), REDIRECT_FETCH_BUDGET_MS),
      ),
    ]);
    if (result === null) {
      status = 0; // budget hit
    } else if (result.status === 200 && result.data) {
      status = 200;
      info = result.data;
    } else {
      status = result.status;
    }
  } catch {
    status = 0;
  }

  // happy path: permanent redirect to the canonical URL of the requested
  // language (the slug itself carries the language). Relative Location
  // header: resolves against the user's origin, never leaks an internal one.
  if (info) {
    const pair = compUrlPair(id, info.competition);
    const target = langEn ? pair.en : pair.ar;
    return new Response(null, {
      status: 308,
      headers: {
        Location: target,
        "Cache-Control": "public, max-age=3600",
      },
    });
  }

  // the backend knows this competition does not exist: a real 404
  if (status === 404) {
    return new Response(
      notFoundHtml(langEn, {
        ar: "البطولة",
        en: "Competition",
        bodyAr: "هذه البطولة غير موجودة أو تمت إزالتها.",
      }),
      { status: 404, headers: { "Content-Type": "text/html; charset=utf-8" } },
    );
  }

  // backend slow/unreachable: noindex page that retries itself
  return new Response(retryHtml(langEn, { ar: "البطولة", en: "competition" }), {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}
