import type { Metadata, Viewport } from "next";
import { buildSiteJsonLd, safeMetaBase } from "@/lib/seo";
import "./globals.css";

const SITE_NAME_AR = "فكوورة";
const SITE_NAME_EN = "Fkoora";
const DEFAULT_TITLE = `${SITE_NAME_AR} | ${SITE_NAME_EN}`;
const DEFAULT_DESCRIPTION =
  "نتائج ومواعيد ومباريات اليوم مباشرة بالعربية والإنجليزية: أهم الدوريات العربية والأوروبية وبطولات كأس العالم — الترتيب والجولات لحظة بلحظة | Live football scores, today's fixtures and results in Arabic & English.";

export const metadata: Metadata = {
  metadataBase: safeMetaBase(),
  title: {
    default: DEFAULT_TITLE,
    template: `%s | ${SITE_NAME_AR}`,
  },
  description: DEFAULT_DESCRIPTION,
  applicationName: SITE_NAME_EN,
  category: "sports",
  keywords: [
    // Arabic - high-intent queries
    "مباريات",
    "مباريات اليوم",
    "نتائج المباريات",
    "نتائج مباريات اليوم",
    "مباريات مباشرة",
    "مواعيد المباريات",
    "الترتيب",
    "ترتيب الدوري",
    "أهداف",
    "ملخص المباريات",
    "الدوري السعودي",
    "الدوري المصري",
    "دوري أبطال أوروبا",
    "الدوري الإنجليزي",
    "الدوري الإسباني",
    // English
    "matches",
    "results",
    "live scores",
    "fixtures",
    "football",
    "soccer",
    "standings",
    "today matches",
    "kooora",
    "fkoora",
  ],
  alternates: {
    canonical: "/",
    languages: {
      ar: "/",
      en: "/?lang=en",
      "x-default": "/",
    },
  },
  openGraph: {
    type: "website",
    url: "/",
    siteName: `${SITE_NAME_AR} | ${SITE_NAME_EN}`,
    title: DEFAULT_TITLE,
    description: DEFAULT_DESCRIPTION,
    locale: "ar_MA",
    alternateLocale: ["en_GB"],
    images: [
      {
        url: "/og-image.png",
        width: 1200,
        height: 630,
        alt: "فكوورة Fkoora — live football scores",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: DEFAULT_TITLE,
    description: DEFAULT_DESCRIPTION,
    images: ["/og-image.png"],
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      "max-image-preview": "large",
      "max-snippet": -1,
      "max-video-preview": -1,
    },
  },
  appleWebApp: {
    capable: true,
    title: SITE_NAME_AR,
    statusBarStyle: "black-translucent",
  },
  formatDetection: {
    telephone: false,
  },
  // icons come from file conventions: src/app/icon.svg + src/app/apple-icon.png
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: "#123a70",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ar" dir="rtl" suppressHydrationWarning>
      <body className="font-app antialiased">
        {/* site-wide structured data (WebSite + Organization) */}
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: buildSiteJsonLd() }}
        />
        {children}
      </body>
    </html>
  );
}
