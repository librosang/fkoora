import { NextRequest, NextResponse } from "next/server";

/**
 * GET /api/events/live — Server-Sent Events pass-through.
 *
 * The browser opens `new EventSource('/api/events/live')` and this route
 * pipes the backend's text/event-stream straight through, chunk by chunk:
 *   * the upstream body is returned AS the response body (no buffering,
 *     no JSON re-serialization - bytes flow as they arrive),
 *   * `X-Accel-Buffering: no` + `no-transform` keep reverse proxies
 *     (nginx / NPM / Cloudflare) from buffering the stream,
 *   * the browser's `Last-Event-ID` header is forwarded so a reconnecting
 *     client gets the missed events replayed from the backend's bounded
 *     event log,
 *   * `req.signal` aborts the upstream request the moment the browser
 *     disconnects (EventSource close / tab close) - no dangling streams.
 *
 * Flow (server-side only, never per browser):
 *   worker -> PostgreSQL -> Redis Pub/Sub -> ONE subscription per API
 *   process -> this proxy -> every connected browser.
 */
export const dynamic = "force-dynamic";
export const runtime = "nodejs";

const API_BASE = process.env.FOOTBALL_API_BASE || "http://127.0.0.1:9000";

export async function GET(req: NextRequest) {
  const headers: Record<string, string> = {
    Accept: "text/event-stream",
    "Cache-Control": "no-cache",
  };
  const lastEventId = req.headers.get("last-event-id");
  if (lastEventId) headers["Last-Event-ID"] = lastEventId;

  let upstream: Response;
  try {
    upstream = await fetch(`${API_BASE}/api/events/live`, {
      headers,
      cache: "no-store",
      // abort the upstream stream when the browser disconnects
      signal: req.signal,
    });
  } catch {
    return new NextResponse(
      JSON.stringify({ error: "live stream unreachable" }),
      { status: 502, headers: { "Content-Type": "application/json" } },
    );
  }

  if (!upstream.ok || !upstream.body) {
    return new NextResponse(
      JSON.stringify({ error: `live stream error (${upstream.status})` }),
      { status: upstream.status || 502, headers: { "Content-Type": "application/json" } },
    );
  }

  return new NextResponse(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": "text/event-stream; charset=utf-8",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      // nginx family: do not buffer this response
      "X-Accel-Buffering": "no",
    },
  });
}
