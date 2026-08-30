import { NextResponse } from "next/server";
import type { ConditionalResult } from "./service";

/**
 * Translate a backend ConditionalResult into the browser-facing response.
 *
 *  - 304 -> empty 304 carrying the ETag (the browser reuses its cached body,
 *    so an unchanged poll costs a few hundred bytes end to end)
 *  - 200 -> JSON body + the backend's ETag, so the NEXT poll revalidates
 *  - 404/5xx -> error JSON (never cached)
 */
export function relayConditional<T>(result: ConditionalResult<T>): NextResponse {
  if (result.status === 304 && result.etag) {
    return new NextResponse(null, {
      status: 304,
      headers: { ETag: result.etag, "Cache-Control": "no-cache" },
    });
  }
  if (result.data) {
    const headers: Record<string, string> = { "Cache-Control": "no-cache" };
    if (result.etag) headers["ETag"] = result.etag;
    return NextResponse.json(result.data, { headers });
  }
  return NextResponse.json(
    { error: result.error || "backend error" },
    { status: result.status || 502, headers: { "Cache-Control": "no-store" } },
  );
}
