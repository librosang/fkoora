import { cache } from "react";
import type { Metadata } from "next";
import { notFound, permanentRedirect } from "next/navigation";
import { getMatchDetail } from "@/lib/goal/service";
import type { Lang, MatchDetail } from "@/lib/goal/types";
import { MatchPageClient } from "@/components/mc/match-page-client";
import {
  matchJsonLd,
  matchSlug,
  matchUrlPair,
  matchTitle,
  matchDescription,
  safeDecodeSegment,
  siteUrl,
} from "@/lib/seo";

/**
 * Match page - SERVER component at the canonical slug URL
 * /match/<id>/<slug>, where the slug CARRIES the language:
 *
 *   /match/<id>/chelsea-vs-brighton          -> English page (EN slug)
 *   /match/<id>/تشيلسي-ضد-برايتون             -> Arabic page (AR slug, default)
 *
 * Each language variant is its own URL, self-canonical, and both reference
 * each other through hreflang (ar / en / x-default) - so Arabic users get
 * searchable Arabic URLs and English users English ones. ?lang=en / ?lang=ar
 * still override the content language (backwards compatibility); the
 * canonical tag then consolidates the variant on the right slug URL.
 *
 * Legacy /match/<id> links never reach this page - the route handler answers
 * them with a real HTTP 308. A mistyped slug still redirects (meta-refresh
 * fallback once streaming began) and the canonical tag always points at the
 * correct URL.
 *
 * Clicking a match in the listing soft-navigates here (history.pushState -
 * the dialog opens over the list), and a direct load server-renders the full
 * match summary so crawlers see real content.
 */
export const dynamic = "force-dynamic";

/** how long the SSR detail fetch may take before the client takes over */
const SSR_FETCH_BUDGET_MS = 4_000;

interface PageProps {
  params: Promise<{ id: string; slug: string }>;
  searchParams: Promise<{ lang?: string }>;
}

/**
 * Budgeted, fail-safe detail fetch, memoized per request so generateMetadata
 * and the page body share ONE backend call (React cache dedupes by args).
 */
const loadMatch = cache(
  async (
    matchId: string,
  ): Promise<{ status: number; detail: MatchDetail | null }> => {
    try {
      const result = await Promise.race([
        getMatchDetail(matchId),
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

/**
 * Resolve the page language from the URL: an explicit ?lang param wins;
 * otherwise the slug itself implies the language (EN slug -> en, AR slug or
 * anything else -> ar, the site default).
 */
function resolveLang(
  langParam: string | undefined,
  slugMatchesAr: boolean,
  slugMatchesEn: boolean,
): Lang {
  if (langParam === "en") return "en";
  if (langParam === "ar") return "ar";
  return slugMatchesEn && !slugMatchesAr ? "en" : "ar";
}

export async function generateMetadata({
  params,
  searchParams,
}: PageProps): Promise<Metadata> {
  const { id, slug } = await params;
  const langParam = (await searchParams).lang;
  const base = siteUrl();
  const { detail } = await loadMatch(id);

  if (detail) {
    const decoded = safeDecodeSegment(slug);
    const slugAr = matchSlug(detail, "ar");
    const slugEn = matchSlug(detail, "en");
    const matchesAr = decoded === slugAr || slug === slugAr;
    const matchesEn = decoded === slugEn || slug === slugEn;
    const pair = matchUrlPair(id, detail);
    const lang = resolveLang(langParam, matchesAr, matchesEn);

    // mistyped/stale slug (matches NEITHER language): redirect to the
    // canonical URL of the resolved language. Runs in generateMetadata
    // (before streaming when possible); if it degrades to a meta-refresh,
    // the canonical tag below still consolidates signals.
    if (!matchesAr && !matchesEn) {
      permanentRedirect(lang === "en" ? pair.en : pair.ar);
    }

    const title = matchTitle(detail, lang);
    const description = matchDescription(detail, lang);
    // self-canonical per language: the EN slug URL is canonical for the EN
    // page, the AR slug URL for the AR page (?lang params are consolidated)
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

  // no data (backend slow/down): keep the URL out of the index instead of
  // serving a thin generic page
  return {
    title: {
      absolute:
        langParam === "en" ? `Match Details | Fkoora` : `تفاصيل المباراة | فكوورة`,
    },
    robots: { index: false, follow: true },
  };
}

export default async function MatchPage({ params, searchParams }: PageProps) {
  const { id, slug } = await params;
  const langParam = (await searchParams).lang;
  const { status, detail } = await loadMatch(id);

  // the backend knows this match does not exist
  if (status === 404) notFound();

  let lang: Lang = "ar";
  if (detail) {
    const decoded = safeDecodeSegment(slug);
    const slugAr = matchSlug(detail, "ar");
    const slugEn = matchSlug(detail, "en");
    const matchesAr = decoded === slugAr || slug === slugAr;
    const matchesEn = decoded === slugEn || slug === slugEn;
    const pair = matchUrlPair(id, detail);
    lang = resolveLang(langParam, matchesAr, matchesEn);

    // mistyped/stale slug reaching the page body: redirect again (fallback
    // for cases where generateMetadata could not, e.g. streaming had begun)
    if (!matchesAr && !matchesEn) {
      permanentRedirect(lang === "en" ? pair.en : pair.ar);
    }
  }

  const jsonLd = detail ? matchJsonLd(detail, lang) : null;

  return (
    <>
      {jsonLd && (
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: jsonLd }}
        />
      )}
      <MatchPageClient
        matchId={id}
        initialDetail={detail}
        initialLang={lang}
      />
    </>
  );
}
