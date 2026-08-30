import { getMatchDetail } from "@/lib/goal/service";
import type { MatchDetail } from "@/lib/goal/types";
import { matchSlug, matchUrlPath } from "@/lib/seo";

/**
 * GET /match/<id> - LEGACY match URL (no slug).
 *
 * A Route Handler (not a page) so it can answer with a REAL HTTP 308 before
 * anything streams: redirecting from a page component would arrive after
 * streaming began and Next.js would degrade it to a 200 + <meta refresh>.
 *
 * It fetches the match detail once, computes the canonical slug URL
 * (/match/<id>/<home>-vs-<away>) and 308-redirects there - so every legacy
 * link (old sitemap entries, shared URLs, search indices) consolidates its
 * signals on the single canonical slug URL.
 */
export const dynamic = "force-dynamic";

/** how long the detail fetch may take before falling back to the retry page */
const REDIRECT_FETCH_BUDGET_MS = 4_000;

interface RouteContext {
  params: Promise<{ id: string }>;
}

/** Minimal branded HTML (no React here - this is a plain route handler). */
function minimalHtml(langEn: boolean, title: string, body: string, refresh = 0): string {
  const dir = langEn ? "ltr" : "ltr";
  return `<!DOCTYPE html>
<html lang="${langEn ? "en" : "ar"}" dir="${dir}">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="robots" content="noindex, follow"/>${refresh > 0 ? `<meta http-equiv="refresh" content="${refresh}"/>` : ""}
<title>${title}</title>
<style>body{font-family:system-ui,sans-serif;background:#e9edf2;color:#1c2b3a;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}main{max-width:420px;padding:24px;text-align:center}h1{font-size:18px;margin:0 0 8px}p{font-size:14px;color:#5b6b80;margin:0 0 16px}a{display:inline-block;padding:8px 16px;border-radius:6px;background:#17457f;color:#fff;text-decoration:none;font-size:14px;font-weight:600}</style>
</head>
<body><main>
<svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="#17457f" stroke-width="1.6" aria-hidden="true"><circle cx="12" cy="12" r="9.5"/><path d="M12 8.2l3.6 2.6-1.4 4.2H9.8L8.4 10.8 12 8.2z" fill="#17457f" stroke="none"/></svg>
<h1>${title}</h1>
${body}
</main></body></html>`;
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

  // happy path: permanent redirect to the canonical slug URL (same host the
  // visitor is on; ?lang=en is preserved)
  if (detail) {
    const target = matchUrlPath(id, matchSlug(detail));
    const location = langEn ? `${target}?lang=en` : target;
    return new Response(null, {
      status: 308,
      headers: {
        Location: new URL(location, url).toString(),
        "Cache-Control": "public, max-age=3600",
      },
    });
  }

  // the backend knows this match does not exist: a real 404 (better for
  // crawlers than the not-found page's 200 + noindex quirk in Next 16)
  if (status === 404) {
    const html = minimalHtml(
      langEn,
      langEn ? "Match not found | Fkoora" : "المباراة غير موجودة | فكوورة",
      langEn
        ? `<p>This match does not exist (or has been removed).</p><a href="/">${"Fkoora home"}</a>`
        : `<p>هذه المباراة غير موجودة أو تمت إزالتها.</p><a href="/">صفحة فكوورة الرئيسية</a>`,
    );
    return new Response(html, {
      status: 404,
      headers: { "Content-Type": "text/html; charset=utf-8" },
    });
  }

  // backend slow/unreachable: the slug can't be computed yet - serve a
  // noindex page that retries itself (crawlers skip it, users get through)
  const retryHtml = minimalHtml(
    langEn,
    langEn ? "Loading match… | Fkoora" : "جارٍ تحميل المباراة… | فكوورة",
    langEn
      ? `<p>Match details are taking longer than usual. This page will retry automatically…</p><a href="/">${"Go to today's matches"}</a>`
      : `<p>تفاصيل المباراة تستغرق وقتًا أطول من المعتاد. سيعيد تحديث الصفحة تلقائيًا…</p><a href="/">الذهاب لمباريات اليوم</a>`,
    5,
  );
  return new Response(retryHtml, {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8" },
  });
}
