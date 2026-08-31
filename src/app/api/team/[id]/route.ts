import { NextRequest, NextResponse } from "next/server";
import { getTeamDetail } from "@/lib/goal/service";
import { relayConditional } from "@/lib/goal/proxy";

/**
 * GET /api/team/[id]
 *
 * Thin proxy to the Python scraper backend - team drill-down (info, last
 * results, upcoming fixtures, table rows). Read-only: served straight from
 * the database, no scraping is triggered, so it answers in milliseconds.
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

  const detail = await getTeamDetail(id, req.headers.get("if-none-match"));
  return relayConditional(detail);
}
