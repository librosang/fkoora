/**
 * Types for the goal.com bilingual (EN + AR) data layer.
 *
 * goal.com serves the SAME data in ~40 locales with IDENTICAL entity IDs
 * (matches, teams, competitions, players). We fetch the English pages for
 * structure + English names, and the Arabic pages purely for Arabic names.
 */

export type Lang = "en" | "ar";
export type DayType = "past" | "today" | "future";

export interface TeamRef {
  id: string;
  nameEn?: string | null;
  nameAr?: string | null;
  shortNameEn?: string | null;
  code?: string | null;
  crestUrl?: string | null;
}

export interface CompetitionRef {
  id: string;
  nameEn?: string | null;
  nameAr?: string | null;
  areaNameEn?: string | null;
  areaNameAr?: string | null;
  areaCode?: string | null;
  imageUrl?: string | null;
}

export interface MatchRow {
  matchId: string;
  kickoffUtc: string | null;
  status: string; // FIXTURE | LIVE | RESULT | AET | PEN | CANCELLED | ...
  period?: string | null; // e.g. "LIVE 63", "HT"
  homeTeam: TeamRef;
  awayTeam: TeamRef;
  competition: CompetitionRef;
  homeScore: number | null;
  awayScore: number | null;
  homeAggScore?: number | null;
  awayAggScore?: number | null;
  homeRedCards: number;
  awayRedCards: number;
  roundName?: string | null;
  gamesetName?: string | null;
  gamesetNameAr?: string | null;
  venueNameEn?: string | null;
  venueNameAr?: string | null;
  slugEn?: string | null;
  slugAr?: string | null;
}

export interface CompetitionGroup {
  competition: CompetitionRef;
  matches: MatchRow[];
  isMajor: boolean;
}

export interface ListingResponse {
  date: string;
  dayType: DayType;
  generatedAt: string;
  totalMatches: number;
  groups: CompetitionGroup[];
}

export interface PersonRef {
  id?: string | null;
  nameEn?: string | null;
  nameAr?: string | null;
}

export interface MatchEvent {
  teamSide: "home" | "away" | null;
  eventType: string; // GOAL | YELLOW_CARD | RED_CARD | SUBSTITUTION | VAR_* | ...
  minute: number | null;
  extraMinute: number | null;
  player: PersonRef;
  relatedPlayer: PersonRef;
  homeScoreAfter: number | null;
  awayScoreAfter: number | null;
  /** VAR events: what the review concluded, e.g. "NO_GOAL" / "NO_PENALTY" */
  outcome?: string | null;
  /** VAR events: e.g. "CANCELLED" (disallowed goal / overturned penalty) */
  decision?: string | null;
}

export interface LineupEntry {
  person: PersonRef;
  isStarter: boolean;
  shirtNumber: number | null;
  isCaptain: boolean;
  positionX: number | null;
  positionY: number | null;
  rating: number | null;
}

export interface LineupTeam {
  teamId: string | null;
  formation: string | null;
  manager: PersonRef;
  entries: LineupEntry[];
}

export interface StatRow {
  statType: string;
  homeValue: number | string;
  awayValue: number | string;
}

// ---------------------------------------------------------------------------
// competition feature (standings + rounds)
// ---------------------------------------------------------------------------
export interface StandingFormEntry {
  wdl: string; // WIN | DRAW | LOSS
  matchId?: string | null;
}

export interface StandingRow {
  position: number;
  team: TeamRef;
  played: number | null;
  win: number | null;
  draw: number | null;
  lose: number | null;
  goalsFor: number | null;
  goalsAgainst: number | null;
  goalDiff: number | null;
  points: number | null;
  form: StandingFormEntry[];
  markers: string[]; // zone ids, e.g. CHAMPIONS_LEAGUE / RELEGATION
}

export interface StandingsTable {
  name: string | null; // group name (only shown when several tables exist)
  rows: StandingRow[];
}

export interface StandingsMarker {
  id: string;
  nameEn: string | null;
  nameAr: string | null;
  type: string | null; // PROMOTION | RELEGATION | ...
}

export interface GamesetRef {
  gameSetTypeId: string;
  nameEn: string | null;
  nameAr: string | null;
  isActive: boolean;
  sortOrder: number;
  matchCount: number;
}

export interface StandingsPayload {
  tables: StandingsTable[];
  markers: StandingsMarker[];
}

export interface CompetitionInfo {
  competition: CompetitionRef;
  season: { id: string; name: string | null } | null;
  standings: StandingsPayload | null;
  gamesets: GamesetRef[];
  generatedAt?: string;
  /** true when the backend served possibly-stale data and is refreshing it
   *  in the background (stale-while-revalidate) - re-fetch in a few seconds */
  refreshing?: boolean;
}

export interface CompetitionMatchesResponse {
  gameset: GamesetRef;
  competition: CompetitionRef;
  matches: MatchRow[];
  /** true when a background refresh is running - re-fetch in a few seconds */
  refreshing?: boolean;
}

export interface MatchDetail {
  matchId: string;
  kickoffUtc: string | null;
  status: string;
  period?: string | null;
  homeTeam: TeamRef;
  awayTeam: TeamRef;
  homeScore: number | null;
  awayScore: number | null;
  homePenScore?: number | null;
  awayPenScore?: number | null;
  homeScoreHt?: number | null;
  awayScoreHt?: number | null;
  competition: CompetitionRef;
  roundName?: string | null;
  seasonName?: string | null;
  venueNameEn?: string | null;
  venueNameAr?: string | null;
  referee?: string | null;
  events: MatchEvent[];
  lineups: {
    confirmed: boolean;
    home?: LineupTeam;
    away?: LineupTeam;
  };
  stats: StatRow[];
}
