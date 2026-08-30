import { cache } from "react";
import type { Metadata } from "next";
import { notFound, permanentRedirect } from "next/navigation";
import { getTeam } from "@/lib/goal/service";
import type { Lang, TeamInfo } from "@/lib/goal/types";
import { TeamPageClient } from "@/components/mc/team-page-client";
import {
  safeDecodeSegment,
  siteUrl,
  teamDescription,
  teamJsonLd,
  teamSlug,
  teamTitle,
  teamUrlPair,
} from "@/lib/seo";

/**
 * Team page - SERVER component at the canonical slug URL /team/<id>/<slug>,
 * where the slug CARRIES the language (mirrors the match/competition pages):
 *
 *   /team/<id>/real-madrid        -> English page (EN slug)
 *   /team/<id>/ريال-مدريد          -> Arabic page (AR slug, default)
 *
 * The team header, recent results, upcoming fixtures and squad are
 * server-rendered so crawlers see real content, and JSON-LD (SportsTeam +
 * ItemList of SportsEvents + BreadcrumbList) is emitted in the page language.
 */
export const dynamic = "force-dynamic";

/** how long the SSR info fetch may take before the client takes over */
const SSR_FETCH_BUDGET_MS = 4_000;

interface PageProps {
  params: Promise<{ id: string; slug: string }>;
  searchParams: Promise<{ lang?: string }>;
}

/**
 * Budgeted, fail-safe info fetch, memoized per request so generateMetadata
 * and the page body share ONE backend call (React cache dedupes by args).
 */
const loadTeam = cache(
  async (
    teamId: string,
  ): Promise<{ status: number; info: TeamInfo | null }> => {
    try {
      const result = await Promise.race([
        getTeam(teamId),
        new Promise<null>((resolve) =>
          setTimeout(() => resolve(null), SSR_FETCH_BUDGET_MS),
        ),
      ]);
      if (result === null) return { status: 0, info: null }; // budget hit
      if (result.status === 200 && result.data) {
        return { status: 200, info: result.data };
      }
      return { status: result.status, info: null };
    } catch {
      return { status: 0, info: null };
    }
  },
);

/** Same language resolution as the match/competition pages: ?lang wins, else slug. */
function resolveLang(
  langParam: string | undefined,
  slugMatchesAr: boolean,
  slugMatchesEn: boolean,
): Lang {
  if (langParam === "en") return "en";
  if (langParam === "ar") return "ar";
  return slugMatchesEn && !slugMatchesAr ? "en" : "ar";
}

function resolveSlug(rawSlug: string, info: TeamInfo) {
  const decoded = safeDecodeSegment(rawSlug);
  const slugAr = teamSlug(info.team, "ar");
  const slugEn = teamSlug(info.team, "en");
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
  const { info } = await loadTeam(id);

  if (info) {
    const { matchesAr, matchesEn } = resolveSlug(slug, info);
    const pair = teamUrlPair(id, info.team);
    const lang = resolveLang(langParam, matchesAr, matchesEn);

    // mistyped/stale slug: redirect to the canonical URL of the language
    if (!matchesAr && !matchesEn) {
      permanentRedirect(lang === "en" ? pair.en : pair.ar);
    }

    const title = teamTitle(info.team, lang);
    const description = teamDescription(info.team, lang);
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
        langParam === "en" ? `Team Matches | Fkoora` : `مباريات الفريق | فكوورة`,
    },
    robots: { index: false, follow: true },
  };
}

export default async function TeamPage({ params, searchParams }: PageProps) {
  const { id, slug } = await params;
  const langParam = (await searchParams).lang;
  const { status, info } = await loadTeam(id);

  // the backend knows this team does not exist
  if (status === 404) notFound();

  let lang: Lang = "ar";
  if (info) {
    const { matchesAr, matchesEn } = resolveSlug(slug, info);
    const pair = teamUrlPair(id, info.team);
    lang = resolveLang(langParam, matchesAr, matchesEn);

    // mistyped/stale slug reaching the body: redirect again (fallback)
    if (!matchesAr && !matchesEn) {
      permanentRedirect(lang === "en" ? pair.en : pair.ar);
    }
  }

  const jsonLd = info
    ? teamJsonLd(
        {
          teamId: id,
          team: info.team,
          recentMatches: info.recentMatches,
          upcomingMatches: info.upcomingMatches,
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
      <TeamPageClient teamId={id} initialInfo={info} initialLang={lang} />
    </>
  );
}
