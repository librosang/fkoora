import { cache } from "react";
import type { Metadata } from "next";
import { notFound, permanentRedirect } from "next/navigation";
import { getCompetition, getCompetitionMatches } from "@/lib/goal/service";
import type {
  CompetitionInfo,
  CompetitionMatchesResponse,
  Lang,
} from "@/lib/goal/types";
import { CompetitionPageClient } from "@/components/mc/competition-page-client";
import {
  compSlug,
  compUrlPair,
  competitionDescription,
  competitionJsonLd,
  competitionTitle,
  safeDecodeSegment,
  siteUrl,
} from "@/lib/seo";
import { nameOf } from "@/lib/i18n";

/**
 * Competition page - SERVER component at the canonical slug URL
 * /competition/<id>/<slug>, where the slug CARRIES the language:
 *
 *   /competition/<id>/uefa-champions-league      -> English page
 *   /competition/<id>/دوري-أبطال-أوروبا           -> Arabic page (default)
 *
 * Mirrors the match pages exactly: per-language canonical URLs + hreflang,
 * the standings table and the active round's matches server-rendered (so
 * crawlers see real content and internal links to every match page), and
 * JSON-LD (ItemList of SportsEvent + BreadcrumbList) in the page's language.
 */
export const dynamic = "force-dynamic";

/** how long the SSR info fetch may take before the client takes over */
const SSR_INFO_BUDGET_MS = 4_000;
/** extra budget for the active round's matches */
const SSR_ROUND_BUDGET_MS = 2_500;

interface PageProps {
  params: Promise<{ id: string; slug: string }>;
  searchParams: Promise<{ lang?: string }>;
}

/**
 * Budgeted, fail-safe info fetch, memoized per request so generateMetadata
 * and the page body share ONE backend call (React cache dedupes by args).
 */
const loadInfo = cache(
  async (
    competitionId: string,
  ): Promise<{ status: number; info: CompetitionInfo | null }> => {
    try {
      const result = await Promise.race([
        getCompetition(competitionId),
        new Promise<null>((resolve) =>
          setTimeout(() => resolve(null), SSR_INFO_BUDGET_MS),
        ),
      ]);
      if (result === null) return { status: 0, info: null };
      if (result.status === 200 && result.data) {
        return { status: 200, info: result.data };
      }
      return { status: result.status, info: null };
    } catch {
      return { status: 0, info: null };
    }
  },
);

/** the round shown on the page: the active one, else the first */
function activeGameset(info: CompetitionInfo | null) {
  if (!info || info.gamesets.length === 0) return null;
  return info.gamesets.find((g) => g.isActive) || info.gamesets[0];
}

/** localized name of a gameset for titles/JSON-LD */
function gamesetLabel(
  g: { nameEn: string | null; nameAr: string | null } | null,
  lang: Lang,
): string | null {
  if (!g) return null;
  return nameOf(g, lang) || null;
}

/** budgeted fetch of one round's matches (fail-safe: null when slow) */
async function loadRound(
  competitionId: string,
  gamesetId: string | null,
): Promise<CompetitionMatchesResponse | null> {
  if (!gamesetId) return null;
  try {
    const result = await Promise.race([
      getCompetitionMatches(competitionId, gamesetId),
      new Promise<null>((resolve) =>
        setTimeout(() => resolve(null), SSR_ROUND_BUDGET_MS),
      ),
    ]);
    if (result && result.status === 200 && result.data) return result.data;
    return null;
  } catch {
    return null;
  }
}

/** Same language resolution as the match page: ?lang wins, else the slug. */
function resolveLang(
  langParam: string | undefined,
  slugMatchesAr: boolean,
  slugMatchesEn: boolean,
): Lang {
  if (langParam === "en") return "en";
  if (langParam === "ar") return "ar";
  return slugMatchesEn && !slugMatchesAr ? "en" : "ar";
}

function resolveSlug(
  rawSlug: string,
  info: CompetitionInfo,
): { matchesAr: boolean; matchesEn: boolean } {
  const decoded = safeDecodeSegment(rawSlug);
  const slugAr = compSlug(info.competition, "ar");
  const slugEn = compSlug(info.competition, "en");
  return {
    matchesAr: decoded === slugAr || rawSlug === slugAr,
    matchesEn: decoded === slugEn || rawSlug === slugEn,
  };
}

export async function generateMetadata({
  params,
  searchParams,
}: PageProps): Promise<Metadata> {
  const { id, slug } = await params;
  const langParam = (await searchParams).lang;
  const base = siteUrl();
  const { info } = await loadInfo(id);

  if (info) {
    const { matchesAr, matchesEn } = resolveSlug(slug, info);
    const pair = compUrlPair(id, info.competition);
    const lang = resolveLang(langParam, matchesAr, matchesEn);

    // mistyped/stale slug (matches NEITHER language): redirect to the
    // canonical URL of the resolved language
    if (!matchesAr && !matchesEn) {
      permanentRedirect(lang === "en" ? pair.en : pair.ar);
    }

    const metaInput = {
      competition: info.competition,
      seasonName: info.season?.name ?? null,
    };
    const title = competitionTitle(metaInput, lang);
    const description = competitionDescription(metaInput, lang);
    const canonical = lang === "en" ? pair.en : pair.ar;

    return {
      title: { absolute: title },
      description,
      alternates: {
        canonical,
        languages: {
          ar: pair.ar,
          en: pair.en,
          "x-default": pair.ar,
        },
      },
      openGraph: {
        type: "website",
        url: canonical,
        siteName: lang === "ar" ? "فكوورة" : "Fkoora",
        title,
        description,
        locale: lang === "ar" ? "ar_MA" : "en_GB",
        alternateLocale: lang === "ar" ? ["en_GB"] : ["ar_MA"],
        images: [
          {
            url: `${base}/og-image.png`,
            width: 1200,
            height: 630,
            alt: title,
          },
        ],
      },
      twitter: {
        card: "summary_large_image",
        title,
        description,
        images: [`${base}/og-image.png`],
      },
    };
  }

  // no data (backend slow/down): keep the URL out of the index
  return {
    title: {
      absolute:
        langParam === "en"
          ? `Competition Standings | Fkoora`
          : `ترتيب البطولة | فكوورة`,
    },
    robots: { index: false, follow: true },
  };
}

export default async function CompetitionPage({
  params,
  searchParams,
}: PageProps) {
  const { id, slug } = await params;
  const langParam = (await searchParams).lang;
  const { status, info } = await loadInfo(id);

  // the backend knows this competition does not exist
  if (status === 404) notFound();

  let lang: Lang = "ar";
  if (info) {
    const { matchesAr, matchesEn } = resolveSlug(slug, info);
    const pair = compUrlPair(id, info.competition);
    lang = resolveLang(langParam, matchesAr, matchesEn);

    // mistyped slug reaching the body: redirect again (fallback when
    // generateMetadata could not - streaming had already begun)
    if (!matchesAr && !matchesEn) {
      permanentRedirect(lang === "en" ? pair.en : pair.ar);
    }
  }

  const gameset = activeGameset(info);
  const round = info ? await loadRound(id, gameset?.gameSetTypeId ?? null) : null;

  const jsonLd = info
    ? competitionJsonLd(
        {
          competitionId: id,
          competition: info.competition,
          seasonName: info.season?.name ?? null,
          gamesetName: gamesetLabel(round?.gameset ?? gameset, lang),
          matches: round?.matches ?? [],
        },
        lang,
      )
    : null;

  return (
    <>
      {jsonLd && (
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: jsonLd }}
        />
      )}
      <CompetitionPageClient
        competitionId={id}
        initialInfo={info}
        initialRound={round}
        initialLang={lang}
      />
    </>
  );
}
