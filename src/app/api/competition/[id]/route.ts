import { NextRequest, NextResponse } from "next/server";
import { getCompetition } from "@/lib/goal/service";
import { relayConditional } from "@/lib/goal/proxy";

/**
 * GET /api/competition/[id]
 *
 * Thin proxy to the Python scraper backend - competition info, the standings
 * table (bilingual, with form + zone markers) and the list of rounds
 * (gamesets) for the current season, straight from the database.
 * If-None-Match is forwarded and the backend's 304 relayed.
 */
export const dynamic = "force-dynamic";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!id || !/^[A-Za-z0-9_-]{4,64}$/.test(id)) {
    return NextResponse.json({ error: "invalid competition id" }, { status: 400 });
  }

  const comp = await getCompetition(id, req.headers.get("if-none-match"));
  return relayConditional(comp);
}
