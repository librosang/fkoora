import { NextRequest, NextResponse } from "next/server";
import { getTeam } from "@/lib/goal/service";
import { relayConditional } from "@/lib/goal/proxy";

/**
 * GET /api/team/[id]
 *
 * Thin proxy to the Python scraper backend - team profile: recent results,
 * upcoming fixtures and the known squad (bilingual), straight from the
 * database. If-None-Match is forwarded and the backend's 304 relayed.
 */
export const dynamic = "force-dynamic";

export async function GET(
  req: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!id || !/^[A-Za-z0-9_-]{4,64}$/.test(id)) {
    return NextResponse.json({ error: "invalid team id" }, { status: 400 });
  }

  const team = await getTeam(id, req.headers.get("if-none-match"));
  return relayConditional(team);
}
