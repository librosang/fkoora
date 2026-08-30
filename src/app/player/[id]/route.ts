import { getPlayer } from "@/lib/goal/service";
import type { PlayerDetail } from "@/lib/goal/types";
import { playerUrlPair } from "@/lib/seo";
import { notFoundHtml, retryHtml } from "@/lib/bare-page";

/**
 * GET /player/<id> - LEGACY player URL (no slug).
 *
 * Same contract as /match/<id> and /team/<id>: a Route Handler answering with
 * a REAL HTTP 308 to the canonical slug URL of the REQUESTED language
 * (?lang=en -> English slug URL, default Arabic slug URL).
 */
export const dynamic = "force-dynamic";

/** how long the detail fetch may take before falling back to the retry page */
const REDIRECT_FETCH_BUDGET_MS = 5_000;

interface RouteContext {
  params: Promise<{ id: string }>;
}

export async function GET(req: Request, ctx: RouteContext): Promise<Response> {
  const { id } = await ctx.params;
  const url = new URL(req.url);
  const langEn = url.searchParams.get("lang") === "en";

  let status = 0;
  let detail: PlayerDetail | null = null;
  try {
    const result = await Promise.race([
      getPlayer(id),
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
  // language (relative Location - resolves against the user's own origin)
  if (detail) {
    const pair = playerUrlPair(id, detail.player);
    const target = langEn ? pair.en : pair.ar;
    return new Response(null, {
      status: 308,
      headers: {
        Location: target,
        "Cache-Control": "public, max-age=3600",
      },
    });
  }

  // the backend knows this player does not exist: a real 404
  if (status === 404) {
    return new Response(
      notFoundHtml(langEn, {
        ar: "اللاعب",
        en: "Player",
        bodyAr: "هذا اللاعب غير موجود أو تمت إزالته.",
      }),
      { status: 404, headers: { "Content-Type": "text/html; charset=utf-8" } },
    );
  }

  // backend slow/unreachable: serve a noindex page that retries itself
  return new Response(retryHtml(langEn, { ar: "اللاعب", en: "player" }), {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}
