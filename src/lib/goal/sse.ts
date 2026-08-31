/**
 * Client-side live match stream: EventSource wrapper with graceful
 * degradation.
 *
 * The page loads its data via normal HTTP (day listing) and then subscribes
 * once to `/api/events/live`. Incoming `match.updated` deltas patch ONLY
 * the affected match row - the rest of the page state is untouched.
 *
 * Failure policy: EventSource reconnects automatically (the server sends
 * `retry:` and heartbeats). We count consecutive `error` events; after
 * SSE_MAX_FAILURES the stream is closed and the caller is told
 * ("failed") so it can fall back to its existing periodic HTTP refresh
 * (60 s while live). One successful event resets the counter.
 */
import type { LiveEvent, LiveMatchesResponse } from "./types";

export type LiveStreamStatus = "connecting" | "open" | "failed";

export const SSE_MAX_FAILURES = 3;

export interface LiveStreamHandlers {
  onEvent: (event: LiveEvent) => void;
  onStatusChange?: (status: LiveStreamStatus) => void;
}

export class LiveMatchStream {
  private source: EventSource | null = null;
  private failures = 0;
  private handlers: LiveStreamHandlers;
  private closed = false;

  constructor(handlers: LiveStreamHandlers) {
    this.handlers = handlers;
  }

  connect(): void {
    if (this.source || this.closed) return;
    this.handlers.onStatusChange?.("connecting");
    // EventSource sends Accept: text/event-stream and reconnects with
    // Last-Event-ID automatically on transient drops.
    const source = new EventSource("/api/events/live");
    this.source = source;

    source.onopen = () => {
      this.failures = 0;
      this.handlers.onStatusChange?.("open");
    };

    const wrap = (type: string) => (ev: MessageEvent<string>) => {
      this.failures = 0; // any data means the pipe is alive
      try {
        const parsed = JSON.parse(ev.data) as LiveEvent;
        if (parsed && parsed.type) {
          this.handlers.onEvent(parsed);
        }
      } catch {
        /* malformed payload - ignore, the next snapshot re-syncs */
      }
    };

    source.addEventListener("match.updated", wrap("match.updated") as EventListener);
    source.addEventListener("match.event", wrap("match.event") as EventListener);
    source.addEventListener("live.snapshot", wrap("live.snapshot") as EventListener);
    source.onmessage = wrap("message") as EventListener;

    source.onerror = () => {
      this.failures += 1;
      if (this.failures >= SSE_MAX_FAILURES) {
        // repeated failures: stop hammering, let the caller fall back to
        // its periodic HTTP refresh (the data path stays functional)
        this.close();
        this.handlers.onStatusChange?.("failed");
      }
      // otherwise: EventSource retries on its own (server `retry:` hint)
    };
  }

  close(): void {
    this.closed = true;
    if (this.source) {
      this.source.close();
      this.source = null;
    }
  }

  get isFailed(): boolean {
    return this.closed && this.failures > 0;
  }
}

/** Apply a `match.updated` delta onto a listing-shaped MatchRow (in place
 *  copy): only the fields the event carries are touched. */
export function applyMatchDelta<
  T extends {
    matchId: string;
    status: string;
    period?: string | null;
    homeScore: number | null;
    awayScore: number | null;
    homeAggScore?: number | null;
    awayAggScore?: number | null;
    homeRedCards: number;
    awayRedCards: number;
    kickoffUtc?: string | null;
  },
>(row: T, delta: LiveMatchesResponse["matches"][number] & { dataVersion?: number }): T {
  return {
    ...row,
    status: delta.status ?? row.status,
    period: delta.period ?? row.period,
    homeScore: delta.homeScore ?? row.homeScore,
    awayScore: delta.awayScore ?? row.awayScore,
    homeAggScore: delta.homeAggScore ?? row.homeAggScore,
    awayAggScore: delta.awayAggScore ?? row.awayAggScore,
    homeRedCards: delta.homeRedCards ?? row.homeRedCards,
    awayRedCards: delta.awayRedCards ?? row.awayRedCards,
    kickoffUtc: delta.kickoffUtc ?? row.kickoffUtc,
  };
}
