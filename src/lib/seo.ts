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
      ? `مباريات اليوم — النتائج المباشرة والمواعيد | فكوورة`
      : `Today's Football Matches — Live Scores & Fixtures | Fkoora`;
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

// ---------------------------------------------------------------------------
// URL slugs (bilingual: every match/competition has an AR and an EN slug)
// ---------------------------------------------------------------------------

/** Characters kept in URL slugs: a-z, 0-9 and Arabic letters. */
const SLUG_DISALLOWED = /[^a-z0-9\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]+/g;

/**
 * URL-slugify a name: strip accents (é -> e) and Arabic diacritics, lowercase,
 * keep Latin/digits/Arabic letters, everything else becomes a hyphen.
 * "Bayern München" -> "bayern-munchen", "الهلال" stays Arabic
 * (percent-encoded when placed in a URL). Returns "" when nothing remains.
 *
 * The Arabic strip runs AFTER NFKD because decomposition splits hamza-alef
 * letters (أ/إ/آ) into a bare alef + a combining hamza mark (U+0653..U+065F) -
 * dropping that mark normalizes them to plain ا, keeping slugs clean and
 * stable ("أبطال" -> "ابطال").
 */
export function slugifyText(input: string | null | undefined): string {
  if (!input) return "";
  const slug = input
    .normalize("NFKD")
    // Latin combining marks + Arabic tashkeel/hamza marks + tatweel
    .replace(/[\u0300-\u036F\u064B-\u065F\u0670\u0640]/g, "")
    .toLowerCase()
    .replace(SLUG_DISALLOWED, "-")
    .replace(/-{2,}/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug;
}

/** Decode a URL path segment defensively (invalid escapes pass through). */
export function safeDecodeSegment(raw: string): string {
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

/** The two canonical URLs of an entity - one per language. */
export interface LangUrlPair {
  ar: string;
  en: string;
}

/** Max combined slug length - long club names must not bloat the URL. */
const MATCH_SLUG_MAX = 80;

/** Any Arabic letter? (decides the Arabic-vs-Latin slug joiner). */
const HAS_ARABIC = /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]/;

/**
 * Canonical slug of a match in ONE language:
 *   en -> "real-madrid-vs-bayern-munchen"  (English names, Arabic fallback)
 *   ar -> "ريال-مدريد-ضد-بايرن-ميونخ"        (Arabic names, English fallback)
 *
 * Each language gets its own URL (its own slug), so Arabic users see/search
 * Arabic URLs and English users English ones - both are indexed, each is
 * self-canonical and they reference each other through hreflang. When NEITHER
 * team has an Arabic name the Arabic slug is built from the Latin names with
 * the Latin joiner ("botafogo-vs-palmeiras"), so the two language slugs
 * COLLIDE and matchUrlPair() switches the English variant to "?lang=en" on the
 * shared URL - instead of a bizarre mixed-script "botafogo-ضد-palmeiras".
 * Returns "" when neither team has a usable name.
 */
export function matchSlug(
  m: { homeTeam: TeamRef; awayTeam: TeamRef },
  lang: Lang,
): string {
  const home = slugifyText(
    lang === "ar"
      ? m.homeTeam?.nameAr || m.homeTeam?.nameEn
      : m.homeTeam?.nameEn || m.homeTeam?.nameAr,
  );
  const away = slugifyText(
    lang === "ar"
      ? m.awayTeam?.nameAr || m.awayTeam?.nameEn
      : m.awayTeam?.nameEn || m.awayTeam?.nameAr,
  );
  // Arabic joiner only when at least one side actually rendered in Arabic
  const joiner =
    lang === "ar" && (HAS_ARABIC.test(home) || HAS_ARABIC.test(away))
      ? "ضد"
      : "vs";
  let slug = home && away ? `${home}-${joiner}-${away}` : home || away;
  if (slug.length > MATCH_SLUG_MAX) {
    slug = slug.slice(0, MATCH_SLUG_MAX).replace(/-+$/g, "");
  }
  return slug;
}

/**
 * Path of a match page: "/match/<id>/<slug>" ("/match/<id>" when no slug is
 * derivable). The slug is percent-encoded, so Arabic slugs produce valid
 * URLs. Used EVERYWHERE (page metadata, JSON-LD, sitemap, client pushState)
 * so every generated match URL is identical.
 */
export function matchUrlPath(matchId: string, slug: string): string {
  const id = encodeURIComponent(matchId);
  return slug ? `/match/${id}/${encodeURIComponent(slug)}` : `/match/${id}`;
}

/**
 * The two canonical URLs of a match (path + query, no origin):
 *   ar -> "/match/<id>/<arabic-slug>"        (site default, no param)
 *   en -> "/match/<id>/<english-slug>"        (no param - the slug implies
 *        the language). When both slugs collide (e.g. no Arabic names), the
 *        English variant needs the explicit "?lang=en" switch on the shared
 *        URL - the caller does not have to think about that edge case.
 */
export function matchUrlPair(
  matchId: string,
  m: { homeTeam: TeamRef; awayTeam: TeamRef },
): LangUrlPair {
  const slugAr = matchSlug(m, "ar");
  const slugEn = matchSlug(m, "en");
  const ar = matchUrlPath(matchId, slugAr);
  const en =
    slugAr === slugEn
      ? `${matchUrlPath(matchId, slugEn)}?lang=en`
      : matchUrlPath(matchId, slugEn);
  return { ar, en };
}

/** The canonical URL of a match in ONE language (see matchUrlPair). */
export function matchUrlFor(
  matchId: string,
  m: { homeTeam: TeamRef; awayTeam: TeamRef },
  lang: Lang,
): string {
  return lang === "en"
    ? matchUrlPair(matchId, m).en
    : matchUrlPair(matchId, m).ar;
}

// ---------------------------------------------------------------------------
// competition page (per-competition URLs: /competition/<id>/<name>)
// ---------------------------------------------------------------------------

const COMP_SLUG_MAX = 60;

/** Slug of a competition name in one language ("premier-league" /
 *  "الدوري-الإنجليزي-الممتاز"). English fallback when the Arabic name is
 *  missing and vice versa. */
export function compSlug(
  c: { nameEn?: string | null; nameAr?: string | null } | null | undefined,
  lang: Lang,
): string {
  const name =
    lang === "ar" ? c?.nameAr || c?.nameEn : c?.nameEn || c?.nameAr;
  let slug = slugifyText(name);
  if (slug.length > COMP_SLUG_MAX) {
    slug = slug.slice(0, COMP_SLUG_MAX).replace(/-+$/g, "");
  }
  return slug;
}

/** Path of a competition page: "/competition/<id>/<slug>". */
export function compUrlPath(competitionId: string, slug: string): string {
  const id = encodeURIComponent(competitionId);
  return slug
    ? `/competition/${id}/${encodeURIComponent(slug)}`
    : `/competition/${id}`;
}

/** The two canonical URLs of a competition (same rules as matchUrlPair). */
export function compUrlPair(
  competitionId: string,
  c: { nameEn?: string | null; nameAr?: string | null } | null | undefined,
): LangUrlPair {
  const slugAr = compSlug(c, "ar");
  const slugEn = compSlug(c, "en");
  const ar = compUrlPath(competitionId, slugAr);
  const en =
    slugAr === slugEn
      ? `${compUrlPath(competitionId, slugEn)}?lang=en`
      : compUrlPath(competitionId, slugEn);
  return { ar, en };
}

/** The canonical URL of a competition in ONE language. */
export function compUrlFor(
  competitionId: string,
  c: { nameEn?: string | null; nameAr?: string | null } | null | undefined,
  lang: Lang,
): string {
  return lang === "en"
    ? compUrlPair(competitionId, c).en
    : compUrlPair(competitionId, c).ar;
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

/**
 * One SportsEvent JSON-LD node shared by the match page, the day listing and
 * the competition page. Google's Event rich result REQUIRES name, startDate,
 * location and eventStatus - so when the venue is unknown the node gets a
 * VirtualLocation pointing at the coverage page (this is an online event:
 * our coverage), keeping every node rich-result-complete in both languages.
 */
function buildSportsEvent(
  m: MatchMetaInput & { matchId: string },
  lang: Lang,
): Record<string, unknown> {
  const base = siteUrl();
  const home = displayName(m.homeTeam, lang);
  const away = displayName(m.awayTeam, lang);
  const hasScore =
    m.homeScore !== null && m.awayScore !== null && m.status !== "FIXTURE";
  const url = `${base}${matchUrlFor(m.matchId, m, lang)}`;
  // the OTHER language's team name: alternateName serves both audiences
  const altName = (team: TeamRef) =>
    lang === "ar" ? team.nameEn || team.nameAr : team.nameAr || team.nameEn;

  const event: Record<string, unknown> = {
    "@type": "SportsEvent",
    name: hasScore
      ? `${home} ${m.homeScore}-${m.awayScore} ${away}`
      : lang === "ar"
        ? `${home} ضد ${away}`
        : `${home} vs ${away}`,
    sport: lang === "ar" ? "كرة القدم" : "Football",
    url,
    inLanguage: lang,
    description: matchDescription(m, lang),
    image: [`${siteUrl()}/og-image.png`],
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
        alternateName: altName(m.homeTeam) || undefined,
        sport: lang === "ar" ? "كرة القدم" : "Football",
      },
      {
        "@type": "SportsTeam",
        name: away,
        alternateName: altName(m.awayTeam) || undefined,
        sport: lang === "ar" ? "كرة القدم" : "Football",
      },
    ],
  };
  if (m.kickoffUtc) event.startDate = m.kickoffUtc;
  const venue =
    lang === "ar"
      ? m.venueNameAr || m.venueNameEn
      : m.venueNameEn || m.venueNameAr;
  event.location = venue
    ? { "@type": "Place", name: venue }
    : { "@type": "VirtualLocation", url };
  return event;
}

/** JSON-LD for a match page: SportsEvent + breadcrumb. */
export function matchJsonLd(
  m: MatchMetaInput & { matchId: string },
  lang: Lang,
): string {
  const base = siteUrl();
  const event = buildSportsEvent(m, lang);
  const url = event.url as string;

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
 * JSON-LD for the day listing: an ItemList of SportsEvent entries (up to 30)
 * pointing at the per-language match pages. Bilingual team names go into the
 * SportsTeam alternateName so the graph serves both audiences regardless of
 * the rendering language.
 */
export function buildListingJsonLd(
  listing: ListingResponse | null,
  lang: Lang,
): string | null {
  if (!listing || listing.totalMatches === 0) return null;

  const matches: MatchRow[] = [];
  for (const g of listing.groups) {
    for (const m of g.matches) {
      matches.push(m);
      if (matches.length >= 30) break;
    }
    if (matches.length >= 30) break;
  }

  const graph = {
    "@context": "https://schema.org",
    "@type": "ItemList",
    name:
      lang === "ar"
        ? `مباريات يوم ${formatDateTitle(listing.date, lang)}`
        : `Football matches on ${formatDateTitle(listing.date, lang)}`,
    inLanguage: lang,
    numberOfItems: listing.totalMatches,
    itemListElement: matches.map((m, i) => ({
      "@type": "ListItem",
      position: i + 1,
      item: buildSportsEvent(m, lang),
    })),
  };
  return JSON.stringify(graph);
}

// ---------------------------------------------------------------------------
// competition page SEO (titles, description, JSON-LD)
// ---------------------------------------------------------------------------

/** What the competition title/description/JSON-LD builders need. */
export interface CompetitionMetaInput {
  competition: {
    nameEn?: string | null;
    nameAr?: string | null;
    areaNameEn?: string | null;
    areaNameAr?: string | null;
  } | null | undefined;
  seasonName?: string | null;
}

function compDisplayName(c: CompetitionMetaInput["competition"], lang: Lang): string {
  if (!c) return "";
  return (
    (lang === "ar" ? c.nameAr || c.nameEn : c.nameEn || c.nameAr) || ""
  );
}

/** <title> for a competition page (same string on server and client). */
export function competitionTitle(c: CompetitionMetaInput, lang: Lang): string {
  const name = compDisplayName(c.competition, lang);
  const season = c.seasonName ? ` ${c.seasonName}` : "";
  if (lang === "ar") {
    return name
      ? `ترتيب ${name}${season} — نتائج المباريات والجولات | ${APP_NAME_AR}`
      : `ترتيب البطولة — النتائج والجولات | ${APP_NAME_AR}`;
  }
  return name
    ? `${name}${season} Standings, Results & Fixtures | ${APP_NAME_EN}`
    : `Competition Standings, Results & Fixtures | ${APP_NAME_EN}`;
}

/** Meta description for a competition page. */
export function competitionDescription(
  c: CompetitionMetaInput,
  lang: Lang,
): string {
  const name = compDisplayName(c.competition, lang);
  const season = c.seasonName ? ` ${c.seasonName}` : "";
  if (lang === "ar") {
    return name
      ? `ترتيب فرق ${name}${season} محدّث لحظة بلحظة: نتائج جميع الجولات ومواعيد المباريات بتوقيتك المحلي وسجل مباريات الفرق — على ${APP_NAME_AR}.`
      : `ترتيب البطولة محدّث لحظة بلحظة مع نتائج جميع الجولات ومواعيد المباريات — على ${APP_NAME_AR}.`;
  }
  return name
    ? `${name}${season} live standings: results and fixtures for every round, kick-off times in your timezone and full match scores — bilingual on ${APP_NAME_EN}.`
    : `Live competition standings with results and fixtures for every round — on ${APP_NAME_EN}.`;
}

/**
 * JSON-LD for a competition page: an ItemList of the round's SportsEvent
 * entries (every match links to its own match page in the SAME language) plus
 * a BreadcrumbList. Both nodes carry inLanguage so each language variant is
 * independently rich-result-eligible.
 */
export function competitionJsonLd(
  input: {
    competitionId: string;
    competition: CompetitionMetaInput["competition"];
    seasonName?: string | null;
    /** localized name of the shown round (e.g. "Quarter-finals") */
    gamesetName?: string | null;
    matches: MatchRow[];
  },
  lang: Lang,
): string {
  const base = siteUrl();
  const name = compDisplayName(input.competition, lang) ||
    (lang === "ar" ? APP_NAME_AR : APP_NAME_EN);
  const url = `${base}${compUrlFor(input.competitionId, input.competition, lang)}`;

  const listName = input.gamesetName
    ? lang === "ar"
      ? `مباريات ${name} — ${input.gamesetName}`
      : `${name} matches — ${input.gamesetName}`
    : lang === "ar"
      ? `مباريات ${name}`
      : `${name} matches`;

  return JSON.stringify({
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "ItemList",
        name: listName,
        url,
        inLanguage: lang,
        numberOfItems: input.matches.length,
        itemListElement: input.matches.map((m, i) => ({
          "@type": "ListItem",
          position: i + 1,
          item: buildSportsEvent(m, lang),
        })),
      },
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
            name,
            item: url,
          },
        ],
      },
    ],
  });
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
