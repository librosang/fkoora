import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  /* config options here */
  typescript: {
    ignoreBuildErrors: true,
  },
  reactStrictMode: false,
  // SEO hygiene: no "X-Powered-By: Next.js" header
  poweredByHeader: false,
  // security-adjacent + SEO-adjacent defaults
  compress: true,
};

export default nextConfig;
