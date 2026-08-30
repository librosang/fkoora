/**
 * Tiny classic-style SVG icons (football, cards, arrows) - no emoji,
 * matching the classic text-first aesthetic.
 */

export function BallIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true">
      <circle cx="12" cy="12" r="9.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M12 8.2l3.6 2.6-1.4 4.2H9.8L8.4 10.8 12 8.2z" fill="currentColor" />
      <path
        d="M12 8.2V3.4M15.6 10.8l4.3-1.4M14.2 15l2.7 3.7M9.8 15l-2.7 3.7M8.4 10.8L4.1 9.4"
        stroke="currentColor"
        strokeWidth="1.3"
        fill="none"
      />
    </svg>
  );
}

export function CardIcon({
  color,
  className,
}: {
  color: "yellow" | "red";
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={`inline-block h-3.5 w-2.5 rounded-[2px] border border-black/25 ${className || ""}`}
      style={{ backgroundColor: color === "yellow" ? "#f2c500" : "#d31f26" }}
    />
  );
}

export function SubIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="2">
      <path d="M7 4v12" strokeLinecap="round" />
      <path d="M3.5 8.5L7 12l3.5-3.5" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M17 20V8" strokeLinecap="round" />
      <path d="M13.5 15.5L17 12l3.5 3.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** VAR review monitor icon; `cancelled` renders it with a red strike. */
export function VarIcon({
  cancelled,
  className,
}: {
  cancelled?: boolean;
  className?: string;
}) {
  return (
    <span className={`relative inline-flex shrink-0 ${className || ""}`} aria-hidden="true">
      <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none">
        <rect
          x="2.5"
          y="4"
          width="19"
          height="13"
          rx="2"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.8"
        />
        <path d="M9 20.5h6M12 17v3.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
        {cancelled ? (
          <path
            d="M4.5 6.5l15 8M19.5 6.5l-15 8"
            stroke="currentColor"
            strokeWidth="1.4"
            strokeLinecap="round"
          />
        ) : (
          <path
            d="M6.5 10.5l3 3 3.5-5 4.5 5"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        )}
      </svg>
    </span>
  );
}

/** Small red-rectangle cluster shown next to a team with red cards. */
export function RedCardChips({ n }: { n: number }) {
  if (!n || n <= 0) return null;
  return (
    <span className="inline-flex items-center gap-[2px]" title={`${n} × 🟥`.replace(" 🟥", " red cards")}>
      <span className="inline-block h-3 w-2 rounded-[1px] bg-[#d31f26]" />
      {n > 1 && <span className="text-[10px] font-bold leading-none text-[#d31f26]">×{n}</span>}
    </span>
  );
}
