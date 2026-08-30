import { NextResponse, type NextRequest } from "next/server";

/**
 * Passes the request path (+ query) downstream as a request header so the
 * ROOT LAYOUT can render <html lang/dir> in the right language on the FIRST
 * paint (layouts cannot read params/searchParams of nested pages):
 *
 *   /?lang=en                          -> English home
 *   /match/<id>/chelsea-vs-brighton    -> English match page (EN slug)
 *   /match/<id>/تشيلسي-ضد-برايتون       -> Arabic match page (AR slug)
 *
 * The pathname+search is all the layout needs (see pageLang() in layout.tsx).
 * Only page routes are matched - API/asset/sitemap requests skip the
 * middleware entirely.
 */
export function middleware(req: NextRequest) {
  const url = req.nextUrl;
  const headers = new Headers(req.headers);
  headers.set("x-pathname", `${url.pathname}${url.search}`);
  return NextResponse.next({ request: { headers } });
}

export const config = {
  matcher: [
    "/",
    "/match/:path*",
    "/competition/:path*",
    "/team/:path*",
    "/player/:path*",
  ],
};
