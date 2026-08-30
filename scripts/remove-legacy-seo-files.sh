#!/bin/sh
# ---------------------------------------------------------------------------
# Removes files left behind by OLDER drops of this project that CONFLICT
# with the current routes.
#
# Why: unzipping a new drop over an existing tree ADDS and OVERWRITES files
# but never DELETES files that the new version removed. Leftover legacy
# files make the Next.js (Turbopack) build fail with errors like:
#
#   Conflicting route and metadata at /robots.txt:
#       route at /robots.txt/route and metadata at /robots.txt/route
#   Conflicting route and metadata at /sitemap.xml:
#       route at /sitemap.xml/route and metadata at /sitemap.xml/route
#   Conflicting route and page at /match/[id]:
#       route at /match/[id]/route and page at /match/[id]/page
#
# The v4 SEO restructure replaced these build-time metadata files with
# runtime route handlers:
#   src/app/robots.ts            -> src/app/robots.txt/route.ts
#   src/app/sitemap.ts           -> src/app/sitemap.xml/route.ts (+ sitemaps/)
#   src/app/match/[id]/page.tsx  -> src/app/match/[id]/route.ts (+ [slug]/page.tsx)
#
# Safe to run any number of times (only deletes if present).
#
# Usage, from the repository root:
#   sh scripts/remove-legacy-seo-files.sh
# ---------------------------------------------------------------------------
set -e

removed=0
for f in \
  src/app/robots.ts \
  src/app/sitemap.ts \
  "src/app/match/[id]/page.tsx"
do
  if [ -f "$f" ]; then
    rm "$f"
    echo "removed stale legacy file: $f"
    removed=$((removed + 1))
  fi
done

if [ "$removed" -eq 0 ]; then
  echo "OK - no legacy SEO files found (tree is already clean)."
else
  echo "OK - removed $removed stale file(s). You can rebuild now."
fi
