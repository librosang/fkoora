import { NextRequest, NextResponse } from "next/server";
import { getPlayer } from "@/lib/goal/service";
import { relayConditional } from "@/lib/goal/proxy";

/**
 * GET /api/player/[id]
 *
 * Thin proxy to the Python scraper backend - player profile: bilingual bio
 * (position, height, weight, nationality, birth data) plus the full career
 * history, straight from the database. An unprofiled player is scraped
 * on demand upstream (same contract as match details). If-None-Match is
 * forwarded and the backend's 304 relayed.
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

  const player = await getPlayer(id, req.headers.get("if-none-match"));
  return relayConditional(player);
}
