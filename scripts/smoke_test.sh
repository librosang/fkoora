#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Fkoora bilingual SEO smoke test (v6: bilingual slugs + competition pages +
# rich results in both languages).
#
# Requirements (both must already be running):
#   mock backend  : node scripts/mock_backend.js          (:9000)
#   frontend      : PORT=3100 FOOTBALL_API_BASE=http://127.0.0.1:9000 \
#                   SITE_URL=https://fkoora.site \
#                   node .next/standalone/server.js
#
# Usage: bash scripts/smoke_test.sh
# ---------------------------------------------------------------------------
set -u

# resolve this script's directory so python helpers work from any cwd
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

BASE="http://127.0.0.1:3100"
SITE="https://fkoora.site"
PASS=0
FAIL=0
FAILED_NAMES=()

ok()  { PASS=$((PASS+1)); printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
bad() { FAIL=$((FAIL+1)); FAILED_NAMES+=("$1"); printf '  \033[31mFAIL\033[0m  %s\n' "$1"; }

# has <name> <haystack> <fixed-string>
has() {
  if printf '%s' "$2" | grep -qF -- "$3"; then ok "$1"; else bad "$1"; fi
}
# rem <name> <haystack> <regex>
rem() {
  if printf '%s' "$2" | grep -qE -- "$3"; then ok "$1"; else bad "$1"; fi
}
# hasi/remi: case-insensitive variants (Next renders hrefLang= camelCase,
# which is valid case-insensitive HTML)
hasi() {
  if printf '%s' "$2" | grep -qiF -- "$3"; then ok "$1"; else bad "$1"; fi
}
remi() {
  if printf '%s' "$2" | grep -qiE -- "$3"; then ok "$1"; else bad "$1"; fi
}
# lacks <name> <haystack> <fixed-string>
lacks() {
  if printf '%s' "$2" | grep -qF -- "$3"; then bad "$1"; else ok "$1"; fi
}
# is <name> <actual> <expected>
is() {
  if [ "$2" = "$3" ]; then ok "$1"; else bad "$1 (got: $2, want: $3)"; fi
}

fetch() { curl -sS --max-time 20 "$1"; }
code_of() { curl -sS -o /dev/null --max-time 20 -w '%{http_code}' "$1"; }
redirect_of() {
  curl -sS -o /dev/null --max-time 20 -w '%{http_code} %{redirect_url}' "$1"
}
# raw Location header (curl's redirect_url resolves relative paths; we want
# to verify the header itself is RELATIVE so no internal origin can leak)
raw_location() {
  curl -sS -o /dev/null --max-time 20 -D - "$1" | tr -d '\r' | grep -i '^location:' | cut -d' ' -f2-
}

# quick availability check --------------------------------------------------
if [ "$(code_of "$BASE/robots.txt")" != "200" ]; then
  printf '%s\n' "frontend not reachable at $BASE - start it first:"
  printf '%s\n' "  PORT=3100 FOOTBALL_API_BASE=http://127.0.0.1:9000 SITE_URL=$SITE node .next/standalone/server.js"
  exit 1
fi
if [ "$(code_of "http://127.0.0.1:9000/api/matches")" != "200" ]; then
  printf '%s\n' "mock backend not reachable on :9000 - start it first:"
  printf '%s\n' "  node scripts/mock_backend.js"
  exit 1
fi

say() { printf '\n%s\n' "$*"; }

say "=== 1. robots.txt + manifest ========================================="

R=$(fetch "$BASE/robots.txt")
has "robots.txt points at $SITE/sitemap.xml" "$R" "Sitemap: $SITE/sitemap.xml"
is "manifest.webmanifest 200" "$(code_of "$BASE/manifest.webmanifest")" "200"

say "=== 2. sitemap index + children (bilingual URLs) ====================="

SM=$(fetch "$BASE/sitemap.xml")
has "index lists main.xml" "$SM" "$SITE/sitemaps/main.xml"
rem "index lists matches-1.xml" "$SM" "sitemaps/matches-1\.xml"
rem "index lists competitions-1.xml" "$SM" "sitemaps/competitions-1\.xml"
lacks "sitemap index has zero localhost" "$SM" "localhost"

MAIN=$(fetch "$BASE/sitemaps/main.xml")
has "main.xml has the Arabic home URL" "$MAIN" "<loc>$SITE/</loc>"
has "main.xml has the English home URL" "$MAIN" "$SITE/?lang=en"

MATCHES_XML=$(fetch "$BASE/sitemaps/matches-1.xml")
has "matches sitemap has the EN chelsea-vs-brighton-hove-albion slug" "$MATCHES_XML" "chelsea-vs-brighton-hove-albion"
has "matches sitemap has the user's real match id" "$MATCHES_XML" "match/KmnxUMTh30bqzp9LEGdDS"
rem "matches sitemap has percent-encoded ARABIC slug URLs" "$MATCHES_XML" "%D[89]%"
has "no-Arabic-names match uses the ?lang=en variant" "$MATCHES_XML" "botafogo-vs-palmeiras?lang=en"
lacks "matches sitemap has zero localhost" "$MATCHES_XML" "localhost"

COMPS_XML=$(fetch "$BASE/sitemaps/competitions-1.xml")
has "competitions sitemap has the EN premier-league slug" "$COMPS_XML" "competition/epl3/premier-league"
rem "competitions sitemap has the AR slug URL" "$COMPS_XML" "competition/epl3/%D[89]"
lacks "competitions sitemap has zero localhost" "$COMPS_XML" "localhost"

# XML validity of every file we produce
for f in /sitemap.xml /sitemaps/main.xml /sitemaps/matches-1.xml /sitemaps/competitions-1.xml; do
  VALID=$(fetch "$BASE$f" | python3 -c "import sys,xml.etree.ElementTree as ET; ET.fromstring(sys.stdin.read()); print('ok')" 2>/dev/null)
  is "$f is well-formed XML" "${VALID:-bad}" "ok"
done

is "out-of-window month sitemap 404s" "$(code_of "$BASE/sitemaps/days-2025-01.xml")" "404"
is "beyond-last-chunk matches sitemap 404s" "$(code_of "$BASE/sitemaps/matches-9.xml")" "404"

say "=== 3. match 308 redirects (legacy no-slug URLs) ====================="

RD=$(redirect_of "$BASE/match/m1liveucl")
rem "/match/<id> 308 -> ARABIC slug URL (site default)" "$RD" "^308 .*/match/m1liveucl/%D"
RD=$(redirect_of "$BASE/match/m1liveucl?lang=en")
rem "/match/<id>?lang=en 308 -> ENGLISH slug URL" "$RD" "^308 .*/match/m1liveucl/real-madrid-vs-bayern-munchen"
LOC=$(raw_location "$BASE/match/m1liveucl?lang=en")
rem "308 Location header is RELATIVE (no internal origin leak)" "$LOC" "^/match/m1liveucl/real-madrid-vs-bayern-munchen$"
RD=$(redirect_of "$BASE/match/KmnxUMTh30bqzp9LEGdDS")
rem "user's real match id 308 -> Arabic slug URL" "$RD" "^308 .*/match/KmnxUMTh30bqzp9LEGdDS/%D"

say "=== 4. competition 308 redirects ====================================="

RD=$(redirect_of "$BASE/competition/epl3")
rem "/competition/<id> 308 -> ARABIC slug URL" "$RD" "^308 .*/competition/epl3/%D"
RD=$(redirect_of "$BASE/competition/epl3?lang=en")
rem "/competition/<id>?lang=en 308 -> ENGLISH slug URL" "$RD" "^308 .*/competition/epl3/premier-league"

say "=== 5. match pages - ARABIC language variant ========================="

# the AR slug URL is exactly what the sitemap advertises (same builder on both
# sides) - take it from there instead of re-implementing slug rules in bash
AR_M1=$(printf '%s' "$MATCHES_XML" | python3 -c "
import sys, re
locs = re.findall(r'<loc>(.*?)</loc>', sys.stdin.read())
ar = [u for u in locs if 'm1liveucl' in u and '%D' in u]
print(ar[0] if ar else '')")
if [ -n "$AR_M1" ]; then ok "AR match URL discovered from sitemap"; else bad "AR match URL discovered from sitemap"; fi
LOCAL_AR_M1="${AR_M1//$SITE/$BASE}"

AR_HTML=$(fetch "$LOCAL_AR_M1")
hasi "AR match page declares hreflang variants" "$AR_HTML" 'hreflang="ar"'
has "AR match page SSRs <html lang=ar dir=rtl>" "$AR_HTML" '<html lang="ar" dir="rtl">'
has "AR match page SSRs Arabic team names" "$AR_HTML" 'ريال مدريد'
CANON_AR=$(printf '%s' "$AR_HTML" | grep -o '<link rel="canonical" href="[^"]*"' | head -1)
has "AR match page canonical == its own AR slug URL" "$CANON_AR" "$AR_M1"

# rich results: AR SportsEvent node must be complete + in Arabic
RR_AR=$(printf '%s' "$AR_HTML" | python3 "$SCRIPT_DIR/_rr_check.py" match ar 2>&1)
if [ "$(printf '%s' "$RR_AR" | cut -d' ' -f1)" = "OK" ]; then ok "AR match JSON-LD rich-result complete [$RR_AR]"; else bad "AR match JSON-LD rich-result complete [$RR_AR]"; fi

say "=== 6. match pages - ENGLISH language variant ========================"

EN_M1="$SITE/match/m1liveucl/real-madrid-vs-bayern-munchen"
EN_HTML=$(fetch "${EN_M1//$SITE/$BASE}")
has "EN match page SSRs <html lang=en dir=ltr>" "$EN_HTML" '<html lang="en" dir="ltr">'
has "EN match page SSRs English team names" "$EN_HTML" 'Real Madrid vs Bayern München'
CANON_EN=$(printf '%s' "$EN_HTML" | grep -o '<link rel="canonical" href="[^"]*"' | head -1)
has "EN match page canonical == its own EN slug URL" "$CANON_EN" "$EN_M1"
HL_AR=$(printf '%s' "$EN_HTML" | grep -o 'hrefLang="ar" href="[^"]*"' | head -1)
rem "EN match page hreflang ar -> AR slug URL" "$HL_AR" "m1liveucl/%D"

RR_EN=$(printf '%s' "$EN_HTML" | python3 "$SCRIPT_DIR/_rr_check.py" match en 2>&1)
if [ "$(printf '%s' "$RR_EN" | cut -d' ' -f1)" = "OK" ]; then ok "EN match JSON-LD rich-result complete [$RR_EN]"; else bad "EN match JSON-LD rich-result complete [$RR_EN]"; fi

# the no-Arabic-names edge case: both languages share one slug URL
COL_HTML=$(fetch "$BASE/match/m6nocoast/botafogo-vs-palmeiras")
CANON_COL=$(printf '%s' "$COL_HTML" | grep -o '<link rel="canonical" href="[^"]*"' | head -1)
has "slug-collision match (no Arabic names): AR canonical is the shared URL" "$CANON_COL" "$SITE/match/m6nocoast/botafogo-vs-palmeiras"
COL_EN_HTML=$(fetch "$BASE/match/m6nocoast/botafogo-vs-palmeiras?lang=en")
CANON_COL_EN=$(printf '%s' "$COL_EN_HTML" | grep -o '<link rel="canonical" href="[^"]*"' | head -1)
has "slug-collision match: EN canonical adds ?lang=en" "$CANON_COL_EN" "botafogo-vs-palmeiras?lang=en"

# mistyped slug -> redirect / canonical fix
RD=$(redirect_of "$BASE/match/m1liveucl/typo-slug-here")
if printf '%s' "$RD" | grep -qE "^308 "; then
  rem "mistyped slug 308s to a canonical slug URL" "$RD" "m1liveucl/(%D|real-madrid)"
else
  TYPO_HTML=$(fetch "$BASE/match/m1liveucl/typo-slug-here")
  if printf '%s' "$TYPO_HTML" | grep -qE 'http-equiv="refresh"|rel="canonical"'; then
    ok "mistyped slug serves canonical/meta-refresh fallback"
  else
    bad "mistyped slug serves canonical/meta-refresh fallback"
  fi
fi

is "unknown match id -> real HTTP 404" "$(code_of "$BASE/match/doesnotexist99")" "404"

say "=== 7. competition pages - ARABIC language variant ==================="

AR_EPL=$(printf '%s' "$COMPS_XML" | python3 -c "
import sys, re
locs = re.findall(r'<loc>(.*?)</loc>', sys.stdin.read())
ar = [u for u in locs if 'epl3' in u and '%D' in u]
print(ar[0] if ar else '')")
if [ -n "$AR_EPL" ]; then ok "AR competition URL discovered from sitemap"; else bad "AR competition URL discovered from sitemap"; fi
AR_EPL_HTML=$(fetch "${AR_EPL//$SITE/$BASE}")
has "AR competition page SSRs <html lang=ar dir=rtl>" "$AR_EPL_HTML" '<html lang="ar" dir="rtl">'
has "AR competition page SSRs the Arabic competition name" "$AR_EPL_HTML" 'الدوري الإنجليزي الممتاز'
has "AR competition page SSRs Arabic standings rows (crawler-visible table)" "$AR_EPL_HTML" 'ليفربول'
CANON_C_AR=$(printf '%s' "$AR_EPL_HTML" | grep -o '<link rel="canonical" href="[^"]*"' | head -1)
has "AR competition canonical == its own AR slug URL" "$CANON_C_AR" "$AR_EPL"

RR_C_AR=$(printf '%s' "$AR_EPL_HTML" | python3 "$SCRIPT_DIR/_rr_check.py" competition ar 2>&1)
if [ "$(printf '%s' "$RR_C_AR" | cut -d' ' -f1)" = "OK" ]; then ok "AR competition JSON-LD rich-result complete [$RR_C_AR]"; else bad "AR competition JSON-LD rich-result complete [$RR_C_AR]"; fi

say "=== 8. competition pages - ENGLISH language variant =================="

EN_EPL="$SITE/competition/epl3/premier-league"
EN_EPL_HTML=$(fetch "${EN_EPL//$SITE/$BASE}")
has "EN competition page SSRs <html lang=en dir=ltr>" "$EN_EPL_HTML" '<html lang="en" dir="ltr">'
has "EN competition page SSRs the English competition name" "$EN_EPL_HTML" 'Premier League'
has "EN competition page SSRs English standings rows" "$EN_EPL_HTML" 'Liverpool'
CANON_C_EN=$(printf '%s' "$EN_EPL_HTML" | grep -o '<link rel="canonical" href="[^"]*"' | head -1)
has "EN competition canonical == its own EN slug URL" "$CANON_C_EN" "$EN_EPL"
RR_C_EN=$(printf '%s' "$EN_EPL_HTML" | python3 "$SCRIPT_DIR/_rr_check.py" competition en 2>&1)
if [ "$(printf '%s' "$RR_C_EN" | cut -d' ' -f1)" = "OK" ]; then ok "EN competition JSON-LD rich-result complete [$RR_C_EN]"; else bad "EN competition JSON-LD rich-result complete [$RR_C_EN]"; fi
LINKS_EN=$(printf '%s' "$EN_EPL_HTML" | grep -o 'href="/match/KmnxUMTh30bqzp9LEGdDS/[^"]*"' | head -1)
has "EN competition page links internally to EN match slug URLs" "$LINKS_EN" "chelsea-vs-brighton-hove-albion"

# mistyped competition slug -> redirect / canonical fix
RD=$(redirect_of "$BASE/competition/epl3/typo")
if printf '%s' "$RD" | grep -qE "^308 "; then
  rem "mistyped competition slug 308s to a canonical slug URL" "$RD" "epl3/(%D|premier-league)"
else
  TYPO_C_HTML=$(fetch "$BASE/competition/epl3/typo")
  if printf '%s' "$TYPO_C_HTML" | grep -qE 'http-equiv="refresh"|rel="canonical"'; then
    ok "mistyped competition slug serves canonical/meta-refresh fallback"
  else
    bad "mistyped competition slug serves canonical/meta-refresh fallback"
  fi
fi

is "unknown competition id -> real HTTP 404" "$(code_of "$BASE/competition/doesnotexist9")" "404"

say "=== 9. API proxies ===================================================="

is "/api/matches proxy 200" "$(code_of "$BASE/api/matches?date=2026-08-30&today=2026-08-30&major=1&tz=0")" "200"
is "/api/competition/[id] proxy 200" "$(code_of "$BASE/api/competition/epl3")" "200"
is "/api/competition/[id]/matches proxy 200" "$(code_of "$BASE/api/competition/epl3/matches?gameset=gs-epl-28")" "200"
is "/api/match/[id] proxy 200" "$(code_of "$BASE/api/match/m1liveucl")" "200"

say "=== 10. home page ====================================================="

HOME=$(fetch "$BASE/")
has "home has a canonical tag" "$HOME" 'rel="canonical"'
hasi "home declares hreflang x-default" "$HOME" 'hreflang="x-default"'
has "home SSRs Arabic match content" "$HOME" 'ريال مدريد'
rem "home internal match links use the ARABIC slug URLs (default lang)" "$HOME" 'href="/match/m1liveucl/[^"]*%D'
HOME_EN=$(fetch "$BASE/?lang=en")
has "/?lang=en SSRs the English title" "$HOME_EN" "Today's Football Matches"

# home listing JSON-LD: ItemList of SportsEvents
RR_HOME=$(printf '%s' "$HOME" | python3 "$SCRIPT_DIR/_rr_check.py" home ar 2>&1)
if [ "$(printf '%s' "$RR_HOME" | cut -d' ' -f1)" = "OK" ]; then ok "home JSON-LD ItemList of complete SportsEvents [$RR_HOME]"; else bad "home JSON-LD ItemList of complete SportsEvents [$RR_HOME]"; fi

say "======================================================================="
printf 'RESULT: %s passed, %s failed\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
  printf '%s\n' "failed checks:"
  for n in "${FAILED_NAMES[@]}"; do printf '  - %s\n' "$n"; done
  exit 1
fi
exit 0
