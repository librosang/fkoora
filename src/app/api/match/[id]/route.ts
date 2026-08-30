import { NextRequest, NextResponse } from "next/server";
import { getMatchDetail } from "@/lib/goal/service";
import { relayConditional } from "@/lib/goal/proxy";

/**
 * GET /api/match/[id]
 *
 * Thin proxy to the Python scraper backend - full match detail (events incl.
 * VAR outcomes, bilingual lineups with ratings/formations, stats, venue,
 * referee) straight from the database. If-None-Match is forwarded and the
 * backend's 304 relayed, so re-opening an unchanged match is nearly free.
 */
export const dynamic = "force-dynamic";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!id || !/^[A-Za-z0-9_-]{4,64}$/.test(id)) {
    return NextResponse.json({ error: "invalid match id" }, { status: 400 });
  }

  const detail = await getMatchDetail(id, req.headers.get("if-none-match"));
  return relayConditional(detail);
}
