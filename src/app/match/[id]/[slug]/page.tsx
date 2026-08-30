import { cache } from "react";
import type { Metadata } from "next";
import { notFound, permanentRedirect } from "next/navigation";
import { getMatchDetail } from "@/lib/goal/service";
import type { Lang, MatchDetail } from "@/lib/goal/types";
import { MatchPageClient } from "@/components/mc/match-page-client";
import {
  matchDescription,
  matchJsonLd,
  matchSlug,
  matchTitle,
  matchUrlPath,
  siteUrl,
} from "@/lib/seo";

/**
 * Match page - SERVER component at the canonical slug URL
 * /match/<id>/<home>-vs-<away>.
 *
 * The slug makes the URL human-readable and searchable (team names in the
 * URL itself). Legacy /match/<id> links never reach this page - the route
 * handler at /match/[id]/route.ts answers them with a real HTTP 308 to the
 * canonical URL. A mistyped slug reaching this page still redirects (via a
 * meta-refresh fallback, since streaming has begun) and the canonical tag
 * always points at the correct slug URL.
 *
 * Clicking a match in the listing soft-navigates here (history.pushState -
 * the dialog opens over the list), and a direct load server-renders the full
 * match summary so crawlers see real content.
 *
 * Language: ?lang=en for English (Arabic default, like the home page).
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

function parseLang(value: string | undefined): Lang {
  return value === "en" ? "en" : "ar";
}

export async function generateMetadata({
  params,
  searchParams,
}: PageProps): Promise<Metadata> {
  const { id, slug } = await params;
  const lang = parseLang((await searchParams).lang);
  const base = siteUrl();
  const { detail } = await loadMatch(id);

  // mistyped/stale slug: redirect to the canonical slug URL. This runs in
  // generateMetadata (before streaming when possible); if it degrades to a
  // meta-refresh, the canonical tag below still consolidates signals.
  if (detail && slug !== matchSlug(detail)) {
    const target = matchUrlPath(id, matchSlug(detail));
    permanentRedirect(lang === "en" ? `${target}?lang=en` : target);
  }

  // no data (backend slow/down): keep the URL out of the index instead of
  // serving a thin generic page
  if (!detail) {
    return {
      title: {
        absolute:
          lang === "ar" ? `تفاصيل المباراة | فكوورة` : `Match Details | Fkoora`,
      },
      robots: { index: false, follow: true },
    };
  }

  const title = matchTitle(detail, lang);
  const description = matchDescription(detail, lang);
  const canonical = matchUrlPath(id, matchSlug(detail));

  return {
    title: { absolute: title },
    description,
    alternates: {
      canonical,
      languages: {
        ar: canonical,
        en: `${canonical}?lang=en`,
        "x-default": canonical,
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

export default async function MatchPage({ params, searchParams }: PageProps) {
  const { id, slug } = await params;
  const lang = parseLang((await searchParams).lang);
  const { status, detail } = await loadMatch(id);

  // the backend knows this match does not exist
  if (status === 404) notFound();

  // mistyped/stale slug reaching the page body: redirect again (fallback for
  // cases where generateMetadata could not, e.g. streaming had begun - it
  // then degrades to a meta refresh, still pointing at the canonical URL)
  if (detail && slug !== matchSlug(detail)) {
    const target = matchUrlPath(id, matchSlug(detail));
    permanentRedirect(lang === "en" ? `${target}?lang=en` : target);
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
