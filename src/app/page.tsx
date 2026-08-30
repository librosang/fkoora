import type { Metadata } from "next";
import { getDayListing } from "@/lib/goal/service";
import type { Lang, ListingResponse } from "@/lib/goal/types";
import { HomeClient } from "@/components/mc/home-client";
import {
  buildListingJsonLd,
  isValidDate,
  normalizeDate,
  pageDescription,
  pageTitle,
  siteUrl,
  utcToday,
} from "@/lib/seo";

/**
 * Home - SERVER component.
 *
 * SEO: the day listing is fetched on the server and rendered into the first
 * paint, so crawlers (and social scrapers that do not execute JS) see real
 * match content instead of a spinner. The client component revalidates in
 * the background (timezone alignment + live updates).
 *
 * Crawlable URLs:
 *   /                     today's matches (Arabic, default)
 *   /?lang=en             today's matches (English)
 *   /?date=yyyy-mm-dd     a specific day (results or fixtures)
 */
export const dynamic = "force-dynamic";

/** how long the SSR data fetch may take before the page falls back to
 *  client-side loading (the browser fetch has no such budget) */
const SSR_FETCH_BUDGET_MS = 4_000;

interface PageProps {
  searchParams: Promise<{ date?: string; lang?: string; major?: string }>;
}

function parseLang(value: string | undefined): Lang {
  return value === "en" ? "en" : "ar";
}

export async function generateMetadata({
  searchParams,
}: PageProps): Promise<Metadata> {
  const sp = await searchParams;
  const today = utcToday();
  const date = normalizeDate(sp.date);
  const lang = parseLang(sp.lang);
  const base = siteUrl();

  // canonical: date only (lang variants exposed through alternates.languages)
  const canonical = date === today ? "/" : `/?date=${date}`;

  return {
    title: { absolute: pageTitle(date, today, lang) },
    description: pageDescription(date, today, lang),
    alternates: {
      canonical,
      languages: {
        ar: date === today ? "/" : `/?date=${date}`,
        en: date === today ? "/?lang=en" : `/?date=${date}&lang=en`,
        "x-default": date === today ? "/" : `/?date=${date}`,
      },
    },
    openGraph: {
      type: "website",
      url: canonical,
      siteName: lang === "ar" ? "فكوورة" : "Fkoora",
      title: pageTitle(date, today, lang),
      description: pageDescription(date, today, lang),
      locale: lang === "ar" ? "ar_MA" : "en_GB",
      alternateLocale: lang === "ar" ? ["en_GB"] : ["ar_MA"],
      images: [
        {
          url: `${base}/og-image.png`,
          width: 1200,
          height: 630,
          alt:
            lang === "ar"
              ? "فكوورة — نتائج ومواعيد المباريات مباشرة"
              : "Fkoora — live football scores & fixtures",
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title: pageTitle(date, today, lang),
      description: pageDescription(date, today, lang),
      images: [`${base}/og-image.png`],
    },
    other: {
      "og:updated_time": new Date().toISOString(),
    },
  };
}

export default async function Home({ searchParams }: PageProps) {
  const sp = await searchParams;
  const today = utcToday();
  const date = normalizeDate(sp.date);
  const dateFromUrl = isValidDate(sp.date);
  const lang = parseLang(sp.lang);
  const major = sp.major !== "0";

  // SSR fetch of the listing - bounded, fail-safe: if the backend is slow or
  // unreachable the client loader takes over after hydration (same UX as
  // before this was server-rendered)
  let initialData: ListingResponse | null = null;
  try {
    const budget = new Promise<null>((resolve) =>
      setTimeout(() => resolve(null), SSR_FETCH_BUDGET_MS),
    );
    const result = await Promise.race([
      getDayListing(date, today, major, 0, null),
      budget,
    ]);
    if (result && result.status === 200 && result.data) {
      initialData = result.data;
    }
  } catch {
    /* client-side fallback */
  }

  const jsonLd = buildListingJsonLd(initialData, lang);

  return (
    <>
      {jsonLd && (
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: jsonLd }}
        />
      )}
      <HomeClient
        initialData={initialData}
        initialDate={date}
        initialDateFromUrl={dateFromUrl}
        initialLang={lang}
        initialMajor={major}
      />
    </>
  );
}
