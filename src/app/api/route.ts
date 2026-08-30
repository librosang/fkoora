import { NextResponse } from "next/server";

/**
 * GET /api - health/status endpoint.
 *
 * Reports the frontend itself and whether the Python data backend
 * (football-scraper) is reachable and how fresh its data is.
 */
export const dynamic = "force-dynamic";

const API_BASE = process.env.FOOTBALL_API_BASE || "http://127.0.0.1:9000";

export async function GET() {
  let backend: Record<string, unknown> = { ok: false, error: "unreachable" };
  try {
    const res = await fetch(`${API_BASE}/api/health`, {
      cache: "no-store",
      signal: AbortSignal.timeout(5_000),
    });
    if (res.ok) backend = (await res.json()) as Record<string, unknown>;
  } catch {
    /* backend down - reported below */
  }

  return NextResponse.json({
    ok: true,
    app: "match-center",
    backend,
    time: new Date().toISOString(),
  });
}
