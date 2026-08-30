import { NextRequest, NextResponse } from "next/server";

/**
 * Image proxy:  GET /api/img?t=<opaque token>
 *
 * Pass-through to the Python backend's image endpoint. The token is opaque
 * (an HMAC prefix mapped to the upstream URL inside the backend's database);
 * neither this server nor the browser ever sees the original CDN link.
 * Images are disk-cached (and memory-cached under load) by the backend and
 * cached here/browser-side for 24h.
 *
 * The browser's If-None-Match is forwarded and the backend's 304 relayed,
 * so post-expiry revalidations of unchanged crests cost no bytes at all.
 */
export const dynamic = "force-dynamic";

const API_BASE = process.env.FOOTBALL_API_BASE || "http://127.0.0.1:9000";

export async function GET(req: NextRequest) {
  const token = req.nextUrl.searchParams.get("t");
  if (!token || !/^[0-9a-f]{1,128}$/.test(token)) {
    return new NextResponse("bad request", { status: 400 });
  }

  const headers: Record<string, string> = { Accept: "image/*" };
  const inm = req.headers.get("if-none-match");
  if (inm) headers["If-None-Match"] = inm;

  try {
    const upstream = await fetch(`${API_BASE}/api/img?t=${token}`, {
      headers,
      signal: AbortSignal.timeout(30_000),
    });

    if (upstream.status === 304) {
      const etag = upstream.headers.get("etag");
      if (etag) {
        return new NextResponse(null, {
          status: 304,
          headers: {
            ETag: etag,
            "Cache-Control": "public, max-age=86400, stale-while-revalidate=604800",
          },
        });
      }
    }

    if (!upstream.ok || !upstream.body) {
      return new NextResponse("upstream error", { status: upstream.status });
    }
    const type = upstream.headers.get("content-type") || "application/octet-stream";
    if (!type.startsWith("image/")) {
      return new NextResponse("not an image", { status: 415 });
    }

    const outHeaders = new Headers();
    outHeaders.set("Content-Type", type);
    outHeaders.set("Cache-Control", "public, max-age=86400, stale-while-revalidate=604800");
    outHeaders.set("X-Content-Type-Options", "nosniff");
    const etag = upstream.headers.get("etag");
    if (etag) outHeaders.set("ETag", etag);
    const len = upstream.headers.get("content-length");
    if (len) outHeaders.set("Content-Length", len);

    return new NextResponse(upstream.body, { status: 200, headers: outHeaders });
  } catch {
    return new NextResponse("fetch failed", { status: 502 });
  }
}
