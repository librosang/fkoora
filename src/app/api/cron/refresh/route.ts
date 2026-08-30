import { NextRequest, NextResponse } from "next/server";

/**
 * Cache-warmer / scraping trigger:  GET /api/cron/refresh?secret=...
 *
 * Forwards to the Python backend, which immediately refreshes the listing
 * pages around "now" (yesterday / today / tomorrow) and kicks off detail
 * enrichment in the background. The backend's own scheduler usually makes
 * this redundant - this route exists for platforms with external cron
 * (e.g. Vercel Cron hitting a deployed frontend).
 *
 * Authentication:
 *   - set CRON_SECRET -> requests must carry it as ?secret=... or
 *     "Authorization: Bearer ..." (Vercel Cron sends the header automatically)
 *   - empty/absent -> open access (local dev / private network)
 */
export const dynamic = "force-dynamic";

const API_BASE = process.env.FOOTBALL_API_BASE || "http://127.0.0.1:8000";

function authorized(req: NextRequest): boolean {
  const secret = process.env.CRON_SECRET;
  if (!secret) return true;
  const q = req.nextUrl.searchParams.get("secret");
  if (q && q === secret) return true;
  const auth = req.headers.get("authorization") || "";
  return auth === `Bearer ${secret}`;
}

export async function GET(req: NextRequest) {
  if (!authorized(req)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  try {
    const upstream = await fetch(`${API_BASE}/api/cron/refresh`, {
      cache: "no-store",
      signal: AbortSignal.timeout(15_000),
    });
    const body = await upstream.text();
    return new NextResponse(body, {
      status: upstream.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json(
      { ok: false, error: "backend unreachable" },
      { status: 502 },
    );
  }
}
