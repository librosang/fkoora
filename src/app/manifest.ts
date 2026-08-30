import type { MetadataRoute } from "next";

/**
 * /manifest.webmanifest - installable PWA ("Add to home screen") with the
 * app's brand colors and icons.
 */
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "فكوورة — نتائج ومواعيد المباريات مباشرة",
    short_name: "فكوورة",
    description:
      "نتائج ومواعيد مباريات كرة القدم مباشرة بالعربية والإنجليزية | Live football scores & fixtures",
    start_url: "/",
    scope: "/",
    display: "standalone",
    orientation: "portrait",
    background_color: "#e9edf2",
    theme_color: "#123a70",
    lang: "ar",
    dir: "rtl",
    categories: ["sports", "news"],
    icons: [
      {
        src: "/icon-192.png",
        sizes: "192x192",
        type: "image/png",
      },
      {
        src: "/icon-512.png",
        sizes: "512x512",
        type: "image/png",
      },
      {
        src: "/icon-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
