/**
 * SEO helpers shared by the server pages (metadata + JSON-LD) and the client
 * (document.title sync). Client-safe: no node-only APIs.
 */
import type { Lang, ListingResponse, MatchRow, TeamRef } from "@/lib/goal/types";
import { compLabel, formatDateTime } from "@/lib/i18n";

/** Private production domain - the FINAL fallback so a missing env var can
 *  never leak http://localhost:3000 into canonicals, OG tags or sitemaps. */
const PRODUCTION_SITE_URL = "https://fkoora.site";

/** Normalize a configured site URL: ensure protocol, strip trailing slashes. */
function normalizeSiteUrl(url: string): string {
  let v = url.trim();
  if (v && !/^https?:\/\//i.test(v)) v = `https://${v}`;
  return v.replace(/\/+$/, "");
}

/**
 * Absolute site URL (no trailing slash).
 *
 * Resolution order:
 *  1. SITE_URL              - RUNTIME override. Plain env vars are read at
 *                             request time by the server (sitemap.xml,
 *                             robots.txt, page metadata), so exporting
 *                             SITE_URL on the running server (standalone
 *                             `server.js`, Docker, VPS...) changes every
 *                             generated URL WITHOUT a rebuild.
 *  2. NEXT_PUBLIC_SITE_URL  - build-time value. NEXT_PUBLIC_* variables are
 *                             INLINED when the app is compiled: after
 *                             changing .env you must run `next build` again
 *                             (the standalone server ignores .env changes).
 *  3. VERCEL_PROJECT_PRODUCTION_URL / VERCEL_URL - automatic on Vercel.
 *  4. https://fkoora.site   - this site's real production domain. localhost
 *                             must never appear in production metadata, so
 *                             it is not part of the fallback chain.
 */
export function siteUrl(): string {
  const runtime = process.env.SITE_URL;
  if (runtime && runtime.trim()) return normalizeSiteUrl(runtime);
  const buildTime = process.env.NEXT_PUBLIC_SITE_URL;
  if (buildTime && buildTime.trim()) return normalizeSiteUrl(buildTime);
  const vurl =
    process.env.VERCEL_PROJECT_PRODUCTION_URL || process.env.VERCEL_URL;
  if (vurl && vurl.trim()) return `https://${vurl.trim()}`;
  return PRODUCTION_SITE_URL;
}

/** metadataBase-safe URL (an invalid env value must never throw the build). */
export function safeMetaBase(): URL {
  try {
    return new URL(siteUrl());
  } catch {
    return new URL(PRODUCTION_SITE_URL);
  }
}

/** UTC calendar day, ISO yyyy-mm-dd. */
export function utcToday(): string {
  return new Date().toISOString().slice(0, 10);
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** Validate + normalize a yyyy-mm-dd string, falling back to today (UTC). */
export function normalizeDate(value: string | undefined | null): string {
  if (value && DATE_RE.test(value)) return value;
  return utcToday();
}

export function isValidDate(value: string | undefined | null): boolean {
  return !!value && DATE_RE.test(value);
}

/** "السبت 30 أغسطس 2026" / "Saturday, 30 August 2026" (Latin digits for ar). */
export function formatDateTitle(date: string, lang: Lang): string {
  try {
    return new Intl.DateTimeFormat(
      lang === "ar" ? "ar-MA-u-nu-latn" : "en-GB",
      { weekday: "long", day: "numeric", month: "long", year: "numeric" },
    ).format(new Date(`${date}T12:00:00Z`));
  } catch {
    return date;
  }
}

/** Full <title> for a given day view (same string on server and client). */
export function pageTitle(date: string, today: string, lang: Lang): string {
  const d = formatDateTitle(date, lang);
  if (date === today) {
    return lang === "ar"
      ? `مباريات اليوم ${d} — النتائج المباشرة والمواعيد | فكوورة`
      : `Today's Football Matches, ${d} — Live Scores & Fixtures | Fkoora`;
  }
  const past = date < today;
  if (lang === "ar") {
    return past
      ? `نتائج مباريات ${d} والأهداف والترتيب | فكوورة`
      : `مواعيد مباريات ${d} والقنوات الناقلة | فكوورة`;
  }
  return past
    ? `Football Results ${d} — Scores & Tables | Fkoora`
    : `Football Fixtures ${d} — Kick-off Times | Fkoora`;
}

/** Meta description for a given day view. */
export function pageDescription(
  date: string,
  today: string,
  lang: Lang,
): string {
  const d = formatDateTitle(date, lang);
  if (date === today) {
    return lang === "ar"
      ? `مباريات اليوم ${d} لحظة بلحظة: النتائج المباشرة، مواعيد المباريات بتوقيتك المحلي، الترتيب والجولات لأهم الدوريات العربية والأوروبية وبطولات كأس العالم — كل ذلك بالعربية على فكوورة.`
      : `Follow today's football matches (${d}) live: real-time scores, kick-off times in your timezone, standings and rounds for the top Arab, European and international competitions — bilingual Arabic/English on Fkoora.`;
  }
  const past = date < today;
  if (lang === "ar") {
    return past
      ? `كل نتائج ومباريات ${d}: أهداف المباريات، الترتيب والجولات لأهم الدوريات والبطولات العربية والعالمية — بالعربية على فكوورة.`
      : `مواعيد مباريات ${d} بتوقيتك المحلي مع الترتيب والجولات لأهم الدوريات العربية والأوروبية — بالعربية على فكوورة.`;
  }
  return past
    ? `All football results for ${d}: final scores, standings and rounds for the top Arab and world competitions — on Fkoora.`
    : `Football fixtures for ${d} with kick-off times in your timezone, plus standings and rounds — on Fkoora.`;
}

const APP_NAME_AR = "فكوورة";
const APP_NAME_EN = "Fkoora";

function teamName(m: MatchRow, side: "home" | "away", lang: Lang): string {
  const team = side === "home" ? m.homeTeam : m.awayTeam;
  const name = lang === "ar" ? team.nameAr || team.nameEn : team.nameEn || team.nameAr;
  return name || "";
}

// ---------------------------------------------------------------------------
// match page (per-match URLs: /match/<id>/<home>-vs-<away>)
// ---------------------------------------------------------------------------

/** Characters kept in URL slugs: a-z, 0-9 and Arabic letters. */
const SLUG_DISALLOWED = /[^a-z0-9\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]+/g;

/**
 * URL-slugify a name: strip accents (é -> e), lowercase, keep Latin/digits/
 * Arabic letters, everything else becomes a hyphen. "Bayern München" ->
 * "bayern-munchen", "الهلال" stays Arabic (percent-encoded when placed in a
 * URL). Returns "" when nothing usable remains.
 */
export function slugifyText(input: string | null | undefined): string {
  if (!input) return "";
  const slug = input
    .normalize("NFKD")
    .replace(/[\u0300-\u036F]/g, "")
    .toLowerCase()
    .replace(SLUG_DISALLOWED, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug;
}

/** Max combined slug length - long club names must not bloat the URL. */
const MATCH_SLUG_MAX = 80;

/**
 * Canonical slug of a match: "<home>-vs-<away>" from the English team names
 * (Arabic fallback), e.g. "real-madrid-vs-bayern-munich". The slug is
 * language-neutral (always English when available) so the ar/en hreflang
 * pair shares ONE url. Returns "" when neither team has a usable name.
 */
export function matchSlug(m: {
  homeTeam: TeamRef;
  awayTeam: TeamRef;
}): string {
  const home = slugifyText(m.homeTeam?.nameEn || m.homeTeam?.nameAr);
  const away = slugifyText(m.awayTeam?.nameEn || m.awayTeam?.nameAr);
  let slug = home && away ? `${home}-vs-${away}` : home || away;
  if (slug.length > MATCH_SLUG_MAX) {
    slug = slug.slice(0, MATCH_SLUG_MAX).replace(/-+$/g, "");
  }
  return slug;
}

/**
 * Path of a match page: "/match/<id>/<slug>" ("/match/<id>" when no slug is
 * derivable). The slug is percent-encoded, so Arabic fallback slugs produce
 * valid URLs. Used EVERYWHERE (page metadata, JSON-LD, sitemap, client
 * pushState) so every generated match URL is identical.
 */
export function matchUrlPath(matchId: string, slug: string): string {
  const id = encodeURIComponent(matchId);
  return slug ? `/match/${id}/${encodeURIComponent(slug)}` : `/match/${id}`;
}
/** Structural subset shared by MatchRow and MatchDetail. */
export interface MatchMetaInput {
  matchId?: string;
  homeTeam: TeamRef;
  awayTeam: TeamRef;
  homeScore: number | null;
  awayScore: number | null;
  status: string;
  kickoffUtc: string | null;
  competition?: { nameEn?: string | null; nameAr?: string | null; areaNameEn?: string | null; areaNameAr?: string | null } | null;
  venueNameEn?: string | null;
  venueNameAr?: string | null;
  roundName?: string | null;
}

function displayName(team: TeamRef, lang: Lang): string {
  const name = lang === "ar" ? team.nameAr || team.nameEn : team.nameEn || team.nameAr;
  return name || "";
}

/** <title> for a match page (same string on server and client). */
export function matchTitle(m: MatchMetaInput, lang: Lang): string {
  const home = displayName(m.homeTeam, lang);
  const away = displayName(m.awayTeam, lang);
  const hasScore = m.homeScore !== null && m.awayScore !== null && m.status !== "FIXTURE";
  if (lang === "ar") {
    return hasScore
      ? `${home} ${m.homeScore}-${m.awayScore} ${away} — ملخص وأهداف المباراة | ${APP_NAME_AR}`
      : `${home} ضد ${away} — موعد وملخص المباراة | ${APP_NAME_AR}`;
  }
  return hasScore
    ? `${home} ${m.homeScore}-${m.awayScore} ${away} — Match Summary & Goals | ${APP_NAME_EN}`
    : `${home} vs ${away} — Kick-off Time & Match Summary | ${APP_NAME_EN}`;
}

/** Meta description for a match page. */
export function matchDescription(m: MatchMetaInput, lang: Lang): string {
  const home = displayName(m.homeTeam, lang);
  const away = displayName(m.awayTeam, lang);
  const comp = m.competition ? compLabel(m.competition, lang) : "";
  const venue =
    lang === "ar"
      ? m.venueNameAr || m.venueNameEn || ""
      : m.venueNameEn || m.venueNameAr || "";
  const when = formatDateTime(m.kickoffUtc, lang);
  const hasScore = m.homeScore !== null && m.awayScore !== null && m.status !== "FIXTURE";

  const bits: string[] = [];
  if (comp) bits.push(comp);
  if (when) bits.push(when);
  if (venue) bits.push(lang === "ar" ? `ملعب ${venue}` : `${venue} stadium`);
  const context = bits.join(" · ");

  if (lang === "ar") {
    return hasScore
      ? `مباراة ${home} و${away} انتهت ${m.homeScore}-${m.awayScore}${context ? ` — ${context}` : ""}. ملخص المباراة بالأهداف والتشكيلات والإحصائيات على ${APP_NAME_AR}.`
      : `موعد مباراة ${home} ضد ${away}${context ? ` — ${context}` : ""}. تابع التشكيلات والأحداث لحظة بلحظة على ${APP_NAME_AR}.`;
  }
  return hasScore
    ? `${home} vs ${away}, final score ${m.homeScore}-${m.awayScore}${context ? ` — ${context}` : ""}. Full match summary with goals, lineups and stats on ${APP_NAME_EN}.`
    : `${home} vs ${away}${context ? ` — ${context}` : ""}. Kick-off time, lineups and live coverage on ${APP_NAME_EN}.`;
}

/** JSON-LD for a match page: SportsEvent + breadcrumb. */
export function matchJsonLd(m: MatchMetaInput & { matchId: string }, lang: Lang): string {
  const base = siteUrl();
  const home = displayName(m.homeTeam, lang);
  const away = displayName(m.awayTeam, lang);
  const hasScore = m.homeScore !== null && m.awayScore !== null && m.status !== "FIXTURE";
  const url = `${base}${matchUrlPath(m.matchId, matchSlug(m))}`;

  const event: Record<string, unknown> = {
    "@type": "SportsEvent",
    name: hasScore
      ? `${home} ${m.homeScore}-${m.awayScore} ${away}`
      : lang === "ar"
        ? `${home} ضد ${away}`
        : `${home} vs ${away}`,
    sport: lang === "ar" ? "كرة القدم" : "Football",
    url,
    eventAttendanceMode: "https://schema.org/OnlineEventAttendanceMode",
    isAccessibleForFree: true,
    eventStatus:
      m.status === "CANCELLED"
        ? "https://schema.org/EventCancelled"
        : m.status === "POSTPONED"
          ? "https://schema.org/EventPostponed"
          : "https://schema.org/EventScheduled",
    competitor: [
      {
        "@type": "SportsTeam",
        name: home,
        alternateName: m.homeTeam.nameEn || m.homeTeam.nameAr || undefined,
        sport: lang === "ar" ? "كرة القدم" : "Football",
      },
      {
        "@type": "SportsTeam",
        name: away,
        alternateName: m.awayTeam.nameEn || m.awayTeam.nameAr || undefined,
        sport: lang === "ar" ? "كرة القدم" : "Football",
      },
    ],
  };
  if (m.kickoffUtc) event.startDate = m.kickoffUtc;
  const venue =
    lang === "ar"
      ? m.venueNameAr || m.venueNameEn
      : m.venueNameEn || m.venueNameAr;
  if (venue) event.location = { "@type": "Place", name: venue };

  return JSON.stringify({
    "@context": "https://schema.org",
    "@graph": [
      event,
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          {
            "@type": "ListItem",
            position: 1,
            name: lang === "ar" ? APP_NAME_AR : APP_NAME_EN,
            item: base,
          },
          {
            "@type": "ListItem",
            position: 2,
            name: event.name,
            item: url,
          },
        ],
      },
    ],
  });
}

/**
 * JSON-LD for the day listing: an ItemList of SportsEvent entries (up to 30).
 * Bilingual team names go into the SportsTeam alternateName so the graph
 * serves both audiences regardless of the rendering language.
 */
export function buildListingJsonLd(
  listing: ListingResponse | null,
  lang: Lang,
): string | null {
  if (!listing || listing.totalMatches === 0) return null;

  const base = siteUrl();
  const matches: MatchRow[] = [];
  for (const g of listing.groups) {
    for (const m of g.matches) {
      matches.push(m);
      if (matches.length >= 30) break;
    }
    if (matches.length >= 30) break;
  }

  const events = matches.map((m) => {
    const home = teamName(m, "home", lang);
    const away = teamName(m, "away", lang);
    const homeAlt = m.homeTeam.nameEn || m.homeTeam.nameAr || "";
    const awayAlt = m.awayTeam.nameEn || m.awayTeam.nameAr || "";
    const event: Record<string, unknown> = {
      "@type": "SportsEvent",
      name: lang === "ar" ? `${home} ضد ${away}` : `${home} vs ${away}`,
      sport: lang === "ar" ? "كرة القدم" : "Football",
      url: `${base}${matchUrlPath(m.matchId, matchSlug(m))}`,
      isAccessibleForFree: true,
      eventAttendanceMode: "https://schema.org/OnlineEventAttendanceMode",
      eventStatus: "https://schema.org/EventScheduled",
      competitor: [
        {
          "@type": "SportsTeam",
          name: home,
          alternateName: homeAlt || undefined,
          sport: lang === "ar" ? "كرة القدم" : "Football",
        },
        {
          "@type": "SportsTeam",
          name: away,
          alternateName: awayAlt || undefined,
          sport: lang === "ar" ? "كرة القدم" : "Football",
        },
      ],
    };
    if (m.kickoffUtc) event.startDate = m.kickoffUtc;
    if (m.venueNameEn || m.venueNameAr) {
      event.location = {
        "@type": "Place",
        name: lang === "ar" ? m.venueNameAr || m.venueNameEn : m.venueNameEn || m.venueNameAr,
      };
    }
    // finished matches carry a result in the name (richer snippet wording)
    if (
      m.status !== "FIXTURE" &&
      m.homeScore !== null &&
      m.awayScore !== null
    ) {
      event.name = `${event.name} ${m.homeScore}-${m.awayScore}`;
    }
    return event;
  });

  const graph = {
    "@context": "https://schema.org",
    "@type": "ItemList",
    name:
      lang === "ar"
        ? `مباريات يوم ${formatDateTitle(listing.date, lang)}`
        : `Football matches on ${formatDateTitle(listing.date, lang)}`,
    numberOfItems: listing.totalMatches,
    itemListElement: events.map((e, i) => ({
      "@type": "ListItem",
      position: i + 1,
      item: e,
    })),
  };
  return JSON.stringify(graph);
}

/** Site-wide JSON-LD (WebSite + Organization) - rendered in the root layout. */
export function buildSiteJsonLd(): string {
  const base = siteUrl();
  return JSON.stringify({
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "WebSite",
        "@id": `${base}/#website`,
        url: base,
        name: `${APP_NAME_AR} | ${APP_NAME_EN}`,
        alternateName: [APP_NAME_AR, APP_NAME_EN],
        description:
          "نتائج ومواعيد مباريات كرة القدم مباشرة بالعربية والإنجليزية | Live football scores and fixtures in Arabic & English",
        inLanguage: ["ar", "en"],
        publisher: { "@id": `${base}/#organization` },
      },
      {
        "@type": "Organization",
        "@id": `${base}/#organization`,
        name: `${APP_NAME_AR} (${APP_NAME_EN})`,
        url: base,
        logo: {
          "@type": "ImageObject",
          url: `${base}/icon-512.png`,
          width: 512,
          height: 512,
        },
      },
    ],
  });
}
