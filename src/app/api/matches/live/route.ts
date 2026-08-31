import { NextRequest, NextResponse } from "next/server";
import type { ConditionalResult } from "@/lib/goal/service";
import { getConditional } from "@/lib/goal/service";
import { relayConditional } from "@/lib/goal/proxy";
import type { LiveMatchesResponse } from "@/lib/goal/types";

/**
 * GET /api/matches/live
 *
 * Thin proxy for the backend's live-matches endpoint. The data comes from
 * the Redis hot cache the scraper worker maintains after every committed
 * provider sync (PostgreSQL fallback when Redis is unavailable) - the
 * browser never talks to the football provider, and the payload carries
 * only the minimal live field set.
 *
 * The browser's If-None-Match is forwarded and the backend's 304 relayed:
 * while the live list is unchanged a poll costs a few hundred bytes.
 */
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  const result: ConditionalResult<LiveMatchesResponse> = await getConditional(
    "/api/matches/live",
    req.headers.get("if-none-match"),
  );
  return relayConditional(result);
}
