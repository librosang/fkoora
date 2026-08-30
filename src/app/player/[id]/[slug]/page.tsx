import { cache } from "react";
import type { Metadata } from "next";
import { notFound, permanentRedirect } from "next/navigation";
import { getPlayer } from "@/lib/goal/service";
import type { Lang, PlayerDetail } from "@/lib/goal/types";
import { PlayerPageClient } from "@/components/mc/player-page-client";
import {
  playerDescription,
  playerJsonLd,
  playerSlug,
  playerTitle,
  playerUrlPair,
  safeDecodeSegment,
  siteUrl,
} from "@/lib/seo";
import { nameOf } from "@/lib/i18n";

/**
 * Player page - SERVER component at the canonical slug URL
 * /player/<id>/<slug>, where the slug CARRIES the language (mirrors the
 * match/competition/team pages):
 *
 *   /player/<id>/kylian-mbappe     -> English page (EN slug)
 *   /player/<id>/كيليان-مبابي       -> Arabic page (AR slug, default)
 *
 * The bio card and full career table are server-rendered (crawler food), and
 * JSON-LD (Person + BreadcrumbList) is emitted in the page language.
 */
export const dynamic = "force-dynamic";

/** how long the SSR detail fetch may take before the client takes over */
const SSR_FETCH_BUDGET_MS = 5_000;

interface PageProps {
  params: Promise<{ id: string; slug: string }>;
  searchParams: Promise<{ lang?: string }>;
}

/**
 * Budgeted, fail-safe detail fetch, memoized per request so generateMetadata
 * and the page body share ONE backend call (React cache dedupes by args).
 */
const loadPlayer = cache(
  async (
    playerId: string,
  ): Promise<{ status: number; detail: PlayerDetail | null }> => {
    try {
      const result = await Promise.race([
        getPlayer(playerId),
        new Promise<null>((resolve) =>
          setTimeout(() => resolve(null), SSR_FETCH_BUDGET_MS),
        ),
      ]);
      if (result === null) return { status: 0, detail: null }; // budget hit
      if (result.status === 200 && result.data) {
        return { status: 200, detail: result.data };
      }
      return { status: result.status, detail: null };
    } catch {
      return { status: 0, detail: null };
    }
  },
);

/** Same language resolution as the match/competition/team pages. */
function resolveLang(
  langParam: string | undefined,
  slugMatchesAr: boolean,
  slugMatchesEn: boolean,
): Lang {
  if (langParam === "en") return "en";
  if (langParam === "ar") return "ar";
  return slugMatchesEn && !slugMatchesAr ? "en" : "ar";
}

function resolveSlug(rawSlug: string, detail: PlayerDetail) {
  const decoded = safeDecodeSegment(rawSlug);
  const slugAr = playerSlug(detail.player, "ar");
  const slugEn = playerSlug(detail.player, "en");
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
  const { detail } = await loadPlayer(id);

  if (detail) {
    const { matchesAr, matchesEn } = resolveSlug(slug, detail);
    const pair = playerUrlPair(id, detail.player);
    const lang = resolveLang(langParam, matchesAr, matchesEn);

    // mistyped/stale slug: redirect to the canonical URL of the language
    if (!matchesAr && !matchesEn) {
      permanentRedirect(lang === "en" ? pair.en : pair.ar);
    }

    const title = playerTitle(detail.player, lang);
    const description = playerDescription(
      {
        player: detail.player,
        clubName: detail.currentClub ? nameOf(detail.currentClub, lang) : null,
      },
      lang,
    );
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
        langParam === "en" ? `Player Profile | Fkoora` : `ملف اللاعب | فكوورة`,
    },
    robots: { index: false, follow: true },
  };
}

export default async function PlayerPage({ params, searchParams }: PageProps) {
  const { id, slug } = await params;
  const langParam = (await searchParams).lang;
  const { status, detail } = await loadPlayer(id);

  // the backend knows this player does not exist
  if (status === 404) notFound();

  let lang: Lang = "ar";
  if (detail) {
    const { matchesAr, matchesEn } = resolveSlug(slug, detail);
    const pair = playerUrlPair(id, detail.player);
    lang = resolveLang(langParam, matchesAr, matchesEn);

    // mistyped/stale slug reaching the body: redirect again (fallback)
    if (!matchesAr && !matchesEn) {
      permanentRedirect(lang === "en" ? pair.en : pair.ar);
    }
  }

  const jsonLd = detail
    ? playerJsonLd(
        {
          playerId: id,
          player: detail.player,
          currentClub: detail.currentClub,
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
      <PlayerPageClient
        playerId={id}
        initialDetail={detail}
        initialLang={lang}
      />
    </>
  );
}
