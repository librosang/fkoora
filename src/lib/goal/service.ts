/**
 * Service layer: thin client for the Python scraper backend.
 *
 * ALL data comes from our own PostgreSQL database, kept fresh by the
 * Python scraper's scheduled jobs - this frontend never scrapes anything.
 *
 *   goal.com EN+AR ──scrape──▶ PostgreSQL ──SQL──▶ Flask API (:8000)
 *                                                          │
 *                                                          ▼
 *                                            this client (via /api proxies)
 *
 * The backend base URL is configured through FOOTBALL_API_BASE
 * (default http://127.0.0.1:8000 - see scraper/api.py).
 *
 * Conditional requests: the backend emits strong ETags on every JSON
 * endpoint. The proxy routes forward the browser's If-None-Match header
 * and translate the backend's 304 into a 304 for the browser, so while the
 * data is unchanged a poll costs a few hundred bytes on BOTH hops instead
 * of re-downloading the full payload.
 */
import type {
  CompetitionInfo,
  CompetitionMatchesResponse,
  ListingResponse,
  MatchDetail,
  PlayerDetail,
  TeamInfo,
} from "./types";

const API_BASE = process.env.FOOTBALL_API_BASE || "http://127.0.0.1:9000";

const TIMEOUT_MS = 45_000; // on-demand detail fetches can take a few seconds

/** Result of a conditional GET against the backend. */
export interface ConditionalResult<T> {
  /** HTTP status from the backend: 200, 304, 404, 5xx ... */
  status: number;
  /** The backend's strong ETag when it sent one (200 and 304 responses). */
  etag: string | null;
  /** Parsed body - only for status 200. */
  data: T | null;
  /** Human-readable error message for non-2xx/non-304 statuses. */
  error: string | null;
}

async function getConditional<T>(
  path: string,
  ifNoneMatch?: string | null,
): Promise<ConditionalResult<T>> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (ifNoneMatch) headers["If-None-Match"] = ifNoneMatch;

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
      headers,
    });
  } catch {
    return { status: 502, etag: null, data: null, error: "backend unreachable" };
  }

  const etag = res.headers.get("etag");
  if (res.status === 304) {
    return { status: 304, etag, data: null, error: null };
  }
  if (!res.ok) {
    let message = `backend error (${res.status})`;
    try {
      const body = (await res.json()) as { error?: string };
      if (body?.error) message = body.error;
    } catch {
      /* non-JSON error body */
    }
    return { status: res.status, etag: null, data: null, error: message };
  }
  return { status: 200, etag, data: (await res.json()) as T, error: null };
}

// ---------------------------------------------------------------------------
// day listing (bilingual groups, local-calendar correct for the user's tz)
// ---------------------------------------------------------------------------
export async function getDayListing(
  date: string,
  today: string,
  majorOnly: boolean,
  tzMin = 0,
  ifNoneMatch?: string | null,
): Promise<ConditionalResult<ListingResponse>> {
  const qs = new URLSearchParams({
    date,
    today,
    major: majorOnly ? "1" : "0",
    tz: String(tzMin),
  });
  return getConditional<ListingResponse>(`/api/matches?${qs.toString()}`, ifNoneMatch);
}

// ---------------------------------------------------------------------------
// match detail (events incl. VAR decisions, lineups, stats)
// ---------------------------------------------------------------------------
export async function getMatchDetail(
  matchId: string,
  ifNoneMatch?: string | null,
): Promise<ConditionalResult<MatchDetail>> {
  return getConditional<MatchDetail>(
    `/api/match/${encodeURIComponent(matchId)}`,
    ifNoneMatch,
  );
}

// ---------------------------------------------------------------------------
// competition info (standings + round list) & one round's matches
// ---------------------------------------------------------------------------
export async function getCompetition(
  competitionId: string,
  ifNoneMatch?: string | null,
): Promise<ConditionalResult<CompetitionInfo>> {
  return getConditional<CompetitionInfo>(
    `/api/competition/${encodeURIComponent(competitionId)}`,
    ifNoneMatch,
  );
}

export async function getCompetitionMatches(
  competitionId: string,
  gamesetId?: string | null,
  ifNoneMatch?: string | null,
): Promise<ConditionalResult<CompetitionMatchesResponse>> {
  const qs = gamesetId ? `?gameset=${encodeURIComponent(gamesetId)}` : "";
  return getConditional<CompetitionMatchesResponse>(
    `/api/competition/${encodeURIComponent(competitionId)}/matches${qs}`,
    ifNoneMatch,
  );
}

// ---------------------------------------------------------------------------
// team profile (recent results + upcoming fixtures + squad)
// ---------------------------------------------------------------------------
export async function getTeam(
  teamId: string,
  ifNoneMatch?: string | null,
): Promise<ConditionalResult<TeamInfo>> {
  return getConditional<TeamInfo>(
    `/api/team/${encodeURIComponent(teamId)}`,
    ifNoneMatch,
  );
}

// ---------------------------------------------------------------------------
// player profile (bio + career history)
// ---------------------------------------------------------------------------
export async function getPlayer(
  playerId: string,
  ifNoneMatch?: string | null,
): Promise<ConditionalResult<PlayerDetail>> {
  // an unprofiled player triggers a synchronous profile scrape upstream -
  // same generous timeout as match details
  return getConditional<PlayerDetail>(
    `/api/player/${encodeURIComponent(playerId)}`,
    ifNoneMatch,
  );
}

/** Backend base URL (diagnostics). */
export function backendBase(): string {
  return API_BASE;
}
