import { NextRequest, NextResponse } from "next/server";
import { getPlayerDetail } from "@/lib/goal/service";
import { relayConditional } from "@/lib/goal/proxy";

/**
 * GET /api/player/[id]
 *
 * Thin proxy to the Python scraper backend - player drill-down (bio, career
 * history, last appearances). Read-only: served straight from the database
 * (profiles are fetched by the scraper's `players` / `bootstrap` walks).
 */
export const dynamic = "force-dynamic";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!id || !/^[A-Za-z0-9_-]{4,64}$/.test(id)) {
    return NextResponse.json({ error: "invalid player id" }, { status: 400 });
  }

  const detail = await getPlayerDetail(id, req.headers.get("if-none-match"));
  return relayConditional(detail);
}
