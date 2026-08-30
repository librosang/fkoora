"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CalendarDays, Loader2, RefreshCw, Users, X } from "lucide-react";
import type {
  Lang,
  MatchRow,
  SquadPlayer,
  TeamInfo,
  TeamRef,
} from "@/lib/goal/types";
import {
  compLabel,
  formatDateTime,
  formatTime,
  nameOf,
  positionLabel,
  statusDisplay,
  t,
} from "@/lib/i18n";
import { Crest } from "./crest";

interface TeamDialogProps {
  team: TeamRef | null;
  lang: Lang;
  onClose: () => void;
  /** open the match dialog for one of this team's matches */
  onOpenMatch: (m: MatchRow) => void;
  /** open the player dialog for one of this team's players */
  onOpenPlayer: (p: SquadPlayer) => void;
  /** SSR-provided info for `team` (team page): seeds the state and skips the
   *  first client fetch entirely */
  initialInfo?: TeamInfo | null;
  /** when defined, the dialog's open state is fully controlled by the parent
   *  (team page summary card); undefined = self-managed (listing behavior) */
  openOverride?: boolean;
  /** true when this dialog must stack ABOVE another dialog already open
   *  (e.g. the team dialog opened from the player dialog's club chip) */
  elevated?: boolean;
}

/**
 * Team dialog: recent results, upcoming fixtures and the squad list.
 * Opened from the match dialog header, the standings rows and team pages.
 */
export function TeamDialog({
  team,
  lang,
  onClose,
  onOpenMatch,
  onOpenPlayer,
  initialInfo,
  openOverride,
  elevated,
}: TeamDialogProps) {
  const s = t(lang);
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState<TeamInfo | null>(null);
  const [error, setError] = useState(false);
  // which team the error belongs to (an error from a previous team must never
  // flash when opening a different one)
  const [errorFor, setErrorFor] = useState<string | null>(null);

  const teamId = team?.id ?? null;

  // Only use the info when it belongs to the team currently open - the state
  // briefly still holds the PREVIOUS team's info after switching.
  const currentInfo = info && info.team.id === teamId ? info : null;
  const currentError = error && errorFor === teamId;

  const load = useCallback(async () => {
    if (!teamId) return;
    setError(false);
    try {
      const res = await fetch(`/api/team/${encodeURIComponent(teamId)}`);
      if (!res.ok) throw new Error("failed");
      setInfo(await res.json());
    } catch {
      setError(true);
      setErrorFor(teamId);
    }
  }, [teamId]);

  // data effect: seed from SSR info when it belongs to this team, else fetch
  useEffect(() => {
    if (!team) return;
    if (initialInfo && initialInfo.team.id === team.id) {
      setInfo(initialInfo);
      return;
    }
    setInfo(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [team, load, initialInfo]);

  // open-state effect: skipped when the parent controls the dialog
  useEffect(() => {
    if (openOverride !== undefined) return;
    setOpen(!!team);
  }, [openOverride, team]);

  const isOpen = openOverride !== undefined ? openOverride : open;

  const headerTeam = currentInfo?.team || team;
  const hasSquad = (currentInfo?.squad?.length ?? 0) > 0;

  return (
    <Dialog open={isOpen} onOpenChange={(v) => !v && onClose()}>
      <DialogContent
        aria-describedby={undefined}
        dir={lang === "ar" ? "rtl" : "ltr"}
        showCloseButton={false}
        overlayClassName={elevated ? "z-[55]" : undefined}
        className={`max-h-[92dvh] grid-cols-[minmax(0,1fr)] gap-0 overflow-y-auto rounded-lg border border-[#b9c8dd] p-0 sm:max-w-2xl${elevated ? " z-[60]" : ""}`}
      >
        {team && (
          <>
            {/* header */}
            <div className="bg-gradient-to-b from-[#1d4f92] to-[#123a70] px-4 pb-3 pt-4 text-white">
              <DialogClose
                aria-label={s.close}
                title={s.close}
                className="absolute end-3 top-3 z-20 rounded-md p-1.5 text-white/80 transition-colors hover:bg-white/15 hover:text-white focus:outline-none"
              >
                <X className="h-4 w-4" />
              </DialogClose>
              {/* same layout discipline as the competition dialog: name line,
                  meta line UNDER it - pe-10 keeps long names clear of the X */}
              <DialogTitle className="flex flex-col gap-1 pe-10 leading-snug">
                <span className="flex items-center gap-2 text-[15px] font-bold">
                  <Crest url={headerTeam?.crestUrl} size={26} />
                  <span className="min-w-0 truncate">{nameOf(headerTeam || {}, lang)}</span>
                </span>
                {(headerTeam?.code || headerTeam?.shortNameEn) && (
                  <span className="ms-[34px] inline-flex shrink-0 items-center self-start rounded bg-white/15 px-1.5 py-0.5 text-[11px] font-semibold">
                    {headerTeam.code || headerTeam.shortNameEn}
                  </span>
                )}
              </DialogTitle>
            </div>

            {/* body: loading/error show IN PLACE - never the previous team's
                content (currentInfo is keyed to the team id) */}
            <div className="p-3 sm:p-4" aria-busy={!currentInfo && !currentError}>
              {!currentInfo && !currentError && (
                <div className="flex items-center justify-center gap-2 py-10 text-[#5b6b80]">
                  <Loader2 className="h-5 w-5 animate-spin" />
                  <span className="text-sm">{s.loading}</span>
                </div>
              )}

              {currentError && !currentInfo && (
                <div className="flex flex-col items-center gap-3 py-8">
                  <p className="text-sm text-[#b3392f]">{s.loadError}</p>
                  <button
                    type="button"
                    onClick={load}
                    className="flex items-center gap-1.5 rounded border border-[#17457f] bg-[#17457f] px-3 py-1.5 text-[13px] font-semibold text-white hover:bg-[#123a70]"
                  >
                    <RefreshCw className="h-3.5 w-3.5" /> {s.retry}
                  </button>
                </div>
              )}

              {currentInfo && (
                <Tabs defaultValue="matches" dir={lang === "ar" ? "rtl" : "ltr"}>
                  <TabsList className="w-full gap-1 rounded-md border border-[#c3cedd] bg-[#eef3fa] p-1">
                    <TabsTrigger
                      value="matches"
                      className="flex-1 data-[state=active]:bg-white data-[state=active]:text-[#17457f] data-[state=active]:shadow-sm"
                    >
                      {s.matchesTab}
                    </TabsTrigger>
                    <TabsTrigger
                      value="squad"
                      className="flex-1 data-[state=active]:bg-white data-[state=active]:text-[#17457f] data-[state=active]:shadow-sm"
                    >
                      {s.squadTab}
                    </TabsTrigger>
                  </TabsList>

                  {/* ---------- matches ---------- */}
                  <TabsContent value="matches" className="mt-3">
                    <TeamMatchesView
                      info={currentInfo}
                      lang={lang}
                      strings={s}
                      onOpenMatch={onOpenMatch}
                    />
                  </TabsContent>

                  {/* ---------- squad ---------- */}
                  <TabsContent value="squad" className="mt-3">
                    <SquadView
                      squad={currentInfo.squad}
                      lang={lang}
                      strings={s}
                      onOpenPlayer={onOpenPlayer}
                    />
                  </TabsContent>
                </Tabs>
              )}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// matches tab: upcoming fixtures + recent results
// ---------------------------------------------------------------------------
function TeamMatchesView({
  info,
  lang,
  strings: s,
  onOpenMatch,
}: {
  info: TeamInfo;
  lang: Lang;
  strings: ReturnType<typeof t>;
  onOpenMatch: (m: MatchRow) => void;
}) {
  const upcoming = info.upcomingMatches ?? [];
  const recent = info.recentMatches ?? [];

  if (upcoming.length === 0 && recent.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-[#7d8ea3]">{s.noTeamMatches}</p>
    );
  }

  return (
    <div className="space-y-4">
      {upcoming.length > 0 && (
        <section>
          <h3 className="mb-1.5 flex items-center gap-1.5 px-1 text-[12px] font-bold text-[#17457f]">
            <CalendarDays className="h-3.5 w-3.5" />
            {s.upcomingFixtures}
          </h3>
          <div className="overflow-hidden rounded-md border border-[#dbe4ef] bg-white">
            {upcoming.map((m, i) => (
              <TeamMatchRow
                key={m.matchId}
                m={m}
                lang={lang}
                zebra={i % 2 === 1}
                strings={s}
                onOpen={onOpenMatch}
              />
            ))}
          </div>
        </section>
      )}

      {recent.length > 0 && (
        <section>
          <h3 className="mb-1.5 px-1 text-[12px] font-bold text-[#17457f]">
            {s.recentResults}
          </h3>
          <div className="overflow-hidden rounded-md border border-[#dbe4ef] bg-white">
            {recent.map((m, i) => (
              <TeamMatchRow
                key={m.matchId}
                m={m}
                lang={lang}
                zebra={i % 2 === 1}
                strings={s}
                onOpen={onOpenMatch}
              />
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

/**
 * Compact one-line match row: [date/time | home | score | away] with the
 * opponent emphasised and a W/D/L chip for finished matches.
 */
function TeamMatchRow({
  m,
  lang,
  zebra,
  strings: s,
  onOpen,
}: {
  m: MatchRow;
  lang: Lang;
  zebra: boolean;
  strings: ReturnType<typeof t>;
  onOpen: (m: MatchRow) => void;
}) {
  const st = statusDisplay(m, lang);
  const hasScore = m.homeScore !== null && m.awayScore !== null;
  const live = st.kind === "live";
  const when = formatDateTime(m.kickoffUtc, lang);
  // weekday + day + time is long; keep just the short form under the status
  const shortWhen = formatTime(m.kickoffUtc, lang);

  return (
    <button
      type="button"
      onClick={() => onOpen(m)}
      className={`block w-full border-b border-[#e2e9f2] px-2 py-2 text-start transition-colors last:border-b-0 hover:bg-[#e8f1fb] ${
        zebra ? "bg-[#f6f9fd]" : "bg-white"
      }`}
    >
      <div className="flex w-full items-center gap-2">
        {/* status / time */}
        <div className="w-[52px] shrink-0 text-center" title={when}>
          {live ? (
            <span className="text-[12px] font-extrabold tabular-nums text-[#d31f26]">
              {st.main}
            </span>
          ) : st.kind === "done" ? (
            <span className="text-[11px] font-semibold text-[#5b6b80]">{st.main}</span>
          ) : st.kind === "cancelled" ? (
            <span className="text-[11px] font-semibold text-[#98a3b3]">{st.main}</span>
          ) : (
            <span className="text-[12.5px] font-bold tabular-nums text-[#33455e]">
              {shortWhen}
            </span>
          )}
        </div>

        {/* home */}
        <div className="flex min-w-0 flex-1 items-center justify-end gap-1.5">
          <span className="truncate text-[13px] font-medium text-[#1c2b3a]">
            {nameOf(m.homeTeam, lang)}
          </span>
          <Crest url={m.homeTeam.crestUrl} size={17} />
        </div>

        {/* score */}
        <div className="w-[58px] shrink-0 text-center">
          {hasScore ? (
            <span
              className={`text-[14px] font-extrabold tabular-nums leading-tight ${
                live ? "text-[#d31f26]" : "text-[#14263a]"
              }`}
            >
              {m.homeScore} - {m.awayScore}
            </span>
          ) : (
            <span className="text-[13px] font-semibold text-[#a5b1c0]">-</span>
          )}
        </div>

        {/* away */}
        <div className="flex min-w-0 flex-1 items-center gap-1.5">
          <Crest url={m.awayTeam.crestUrl} size={17} />
          <span className="truncate text-[13px] font-medium text-[#1c2b3a]">
            {nameOf(m.awayTeam, lang)}
          </span>
        </div>
      </div>
      {/* competition line - small, second row */}
      <div className="mt-0.5 flex items-center justify-center gap-1.5 text-[10.5px] text-[#7d8ea3]">
        <span className="truncate">{compLabel(m.competition, lang)}</span>
        {m.roundName && <span className="shrink-0">· {m.roundName}</span>}
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// squad tab: players grouped by position
// ---------------------------------------------------------------------------
const POSITION_ORDER = ["GOALKEEPER", "DEFENDER", "MIDFIELDER", "FORWARD", "OTHER"];

function SquadView({
  squad,
  lang,
  strings: s,
  onOpenPlayer,
}: {
  squad: SquadPlayer[];
  lang: Lang;
  strings: ReturnType<typeof t>;
  onOpenPlayer: (p: SquadPlayer) => void;
}) {
  const groups = useMemo(() => {
    const byKey = new Map<string, SquadPlayer[]>();
    for (const p of squad) {
      const raw = p.position ? p.position.toUpperCase() : "OTHER";
      const key = ["GOALKEEPER", "DEFENDER", "MIDFIELDER", "FORWARD"].includes(raw)
        ? raw
        : "OTHER"; // unknown provider positions land in one shared bucket
      if (!byKey.has(key)) byKey.set(key, []);
      byKey.get(key)!.push(p);
    }
    return POSITION_ORDER.filter((k) => byKey.has(k)).map((k) => ({
      key: k,
      players: byKey.get(k)!,
    }));
  }, [squad]);

  if (squad.length === 0) {
    return (
      <div className="rounded-md border border-[#dbe4ef] bg-[#f6f9fd] p-4 text-center text-sm text-[#7d8ea3]">
        {s.noSquad}
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="flex items-center gap-1.5 px-1 text-[11px] text-[#7d8ea3]">
        <Users className="h-3.5 w-3.5" />
        {squad.length} {s.matchesCount}
      </p>
      {groups.map((g) => (
        <div key={g.key} className="overflow-hidden rounded-md border border-[#dbe4ef] bg-white">
          <div className="border-b border-[#dbe4ef] bg-[#eef3fa] px-3 py-1 text-[11.5px] font-bold text-[#17457f]">
            {positionLabel(g.key === "OTHER" ? null : g.key, lang) || s.posOther}
          </div>
          <div role="list">
            {g.players.map((p) => (
              <button
                key={p.id}
                type="button"
                role="listitem"
                onClick={() => onOpenPlayer(p)}
                className="flex w-full items-center gap-2 border-b border-[#e2e9f2] px-3 py-1.5 text-start transition-colors last:border-b-0 hover:bg-[#e8f1fb]"
              >
                <span className="w-6 shrink-0 text-end text-[12px] font-bold tabular-nums text-[#4a6b96]">
                  {p.shirtNumber ?? ""}
                </span>
                <Crest url={p.imageUrl} size={18} className="rounded-full" />
                <span className="min-w-0 flex-1 truncate text-[13px] font-semibold text-[#1c2b3a]">
                  {nameOf(p, lang)}
                </span>
                <span className="shrink-0 rounded bg-[#eef3fa] px-1.5 py-0.5 text-[10.5px] font-semibold text-[#33455e]">
                  {positionLabel(p.position, lang) || s.posOther}
                </span>
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
