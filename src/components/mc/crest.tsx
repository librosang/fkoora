"use client";

import { useState } from "react";

/**
 * Team crest / competition logo.
 *
 * The API layer rewrites every provider image URL into an opaque local path
 * (/api/img?t=...) before the data leaves the server, so the browser only
 * ever talks to our own origin - the upstream link never reaches the client.
 * Broken or missing images fall back to an inline shield placeholder, and the
 * fixed box size means list rows never shift while images load.
 */

export function Crest({
  url,
  size = 20,
  className = "",
}: {
  url?: string | null;
  size?: number;
  className?: string;
}) {
  // track failure per URL so switching matches in the dialog resets cleanly
  const [failedUrl, setFailedUrl] = useState<string | null>(null);

  // same-origin proxy path -> render it. Anything else (empty, or an absolute
  // URL that somehow bypassed the proxy layer) -> placeholder, never the
  // direct upstream link.
  const usable = url && url.startsWith("/") && url !== failedUrl ? url : null;

  if (!usable) {
    return <ShieldPlaceholder size={size} className={className} />;
  }

  return (
    <img
      src={usable}
      alt=""
      width={size}
      height={size}
      loading="lazy"
      decoding="async"
      onError={() => setFailedUrl(url || null)}
      className={`shrink-0 object-contain ${className}`}
      style={{ width: size, height: size }}
    />
  );
}

/** Generic shield badge shown when no crest is available or loading failed. */
export function ShieldPlaceholder({
  size = 20,
  className = "",
}: {
  size?: number;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={`inline-flex shrink-0 items-center justify-center ${className}`}
      style={{ width: size, height: size }}
    >
      <svg viewBox="0 0 24 24" width={size} height={size} fill="none">
        <path
          d="M12 2.2l7.6 2.9v6c0 4.9-3.2 9-7.6 10.7C7.6 20.1 4.4 16 4.4 11.1v-6L12 2.2z"
          fill="#e6edf6"
          stroke="#b9c8dd"
          strokeWidth="1.2"
        />
        <path d="M12 7.4l3.2 2.3-1.2 3.7h-4l-1.2-3.7L12 7.4z" fill="#b9c8dd" />
      </svg>
    </span>
  );
}
