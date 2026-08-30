import { getTeam } from "@/lib/goal/service";
import type { TeamInfo } from "@/lib/goal/types";
import { teamUrlPair } from "@/lib/seo";
import { notFoundHtml, retryHtml } from "@/lib/bare-page";

/**
 * GET /team/<id> - LEGACY team URL (no slug).
 *
 * Same contract as /match/<id>: a Route Handler (not a page) so it can answer
 * with a REAL HTTP 308 before anything streams. It fetches the team once,
 * computes the canonical URL for the REQUESTED language (?lang=en -> English
 * slug URL, default Arabic slug URL) and redirects there.
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
  let info: TeamInfo | null = null;
  try {
    const result = await Promise.race([
      getTeam(id),
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
  // language (relative Location - resolves against the user's own origin)
  if (info) {
    const pair = teamUrlPair(id, info.team);
    const target = langEn ? pair.en : pair.ar;
    return new Response(null, {
      status: 308,
      headers: {
        Location: target,
        "Cache-Control": "public, max-age=3600",
      },
    });
  }

  // the backend knows this team does not exist: a real 404
  if (status === 404) {
    return new Response(
      notFoundHtml(langEn, {
        ar: "الفريق",
        en: "Team",
        bodyAr: "هذا الفريق غير موجود أو تمت إزالته.",
      }),
      { status: 404, headers: { "Content-Type": "text/html; charset=utf-8" } },
    );
  }

  // backend slow/unreachable: serve a noindex page that retries itself
  return new Response(retryHtml(langEn, { ar: "الفريق", en: "team" }), {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}
