import { NextRequest, NextResponse } from "next/server";
import { getCompetitionMatches } from "@/lib/goal/service";
import { relayConditional } from "@/lib/goal/proxy";

/**
 * GET /api/competition/[id]/matches?gameset=<gameSetTypeId>
 *
 * Thin proxy to the Python scraper backend - one round's matches (results +
 * fixtures) for a competition. Without ?gameset it returns the active round.
 * If-None-Match is forwarded and the backend's 304 relayed.
 */
export const dynamic = "force-dynamic";

const GS_RE = /^[A-Za-z0-9_-]{4,64}$/;

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!id || !GS_RE.test(id)) {
    return NextResponse.json({ error: "invalid competition id" }, { status: 400 });
  }
  const gameset = req.nextUrl.searchParams.get("gameset") || null;
  if (gameset && !GS_RE.test(gameset)) {
    return NextResponse.json({ error: "invalid gameset id" }, { status: 400 });
  }

  const payload = await getCompetitionMatches(
    id,
    gameset,
    req.headers.get("if-none-match"),
  );
  return relayConditional(payload);
}
