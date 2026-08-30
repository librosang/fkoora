"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CalendarDays, ChevronLeft, ChevronRight, Loader2, RefreshCw, Trophy, X } from "lucide-react";
import type {
  CompetitionInfo,
  CompetitionMatchesResponse,
  CompetitionRef,
  GamesetRef,
  Lang,
  MatchRow,
  StandingsMarker,
  StandingsTable,
  TeamRef,
} from "@/lib/goal/types";
import { compLabel, formatTime, nameOf, statusDisplay, t } from "@/lib/i18n";
import { Crest } from "./crest";

interface CompetitionDialogProps {
  competition: CompetitionRef | null;
  lang: Lang;
  onClose: () => void;
  /** open the match dialog for a match inside the selected round */
  onOpenMatch: (m: MatchRow) => void;
  /** open the team dialog for a team in the standings table */
  onOpenTeam?: (team: TeamRef) => void;
}

/**
 * Competition dialog: standings table + every round's results/fixtures.
 * Opened from the competition bar of the match list.
 */
export function CompetitionDialog({
  competition,
  lang,
  onClose,
  onOpenMatch,
  onOpenTeam,
}: CompetitionDialogProps) {
  const s = t(lang);
  const [open, setOpen] = useState(false);
  const [info, setInfo] = useState<CompetitionInfo | null>(null);
  const [error, setError] = useState(false);
  // which competition the error belongs to (an error from a previous
  // competition must never flash when opening a different one)
  const [errorFor, setErrorFor] = useState<string | null>(null);

  // selected round (gameSetTypeId) + its matches
  const [round, setRound] = useState<CompetitionMatchesResponse | null>(null);
  const [roundLoading, setRoundLoading] = useState(false);
  const [roundError, setRoundError] = useState(false);
  const [gamesetId, setGamesetId] = useState<string | null>(null);
  const roundReqId = useRef(0);
  // stale-while-revalidate re-fetch budget: responses served while a
  // background refresh runs carry refreshing=true; we quietly re-fetch a
  // few seconds later so the fresh standings/matches appear on their own
  // (bounded - a failing upstream can never turn this into a poll loop)
  const refreshChain = useRef(0);

  const compId = competition?.id ?? null;

  // Only use data that belongs to the competition currently open - the state
  // briefly still holds the PREVIOUS competition's info/round after
  // switching, which would flash its standings under the new header.
  const currentInfo = info && info.competition.id === compId ? info : null;
  const currentRound =
    round && round.competition.id === compId ? round : null;
  const currentError = error && errorFor === compId;

  const hasStandings =
    !!currentInfo?.standings && (currentInfo.standings.tables?.length ?? 0) > 0;

  // ---- load competition info ------------------------------------------------
  const loadInfo = useCallback(async () => {
    if (!compId) return;
    setError(false);
    try {
      const res = await fetch(`/api/competition/${encodeURIComponent(compId)}`);
      if (!res.ok) throw new Error("failed");
      const json: CompetitionInfo = await res.json();
      setInfo(json);
    } catch {
      setError(true);
      setErrorFor(compId);
    }
  }, [compId]);

  useEffect(() => {
    if (competition) {
      setOpen(true);
      setInfo(null);
      setRound(null);
      setGamesetId(null);
      setRoundError(false);
      refreshChain.current = 0;
      loadInfo();
    } else {
      setOpen(false);
    }
  }, [competition, loadInfo]);

  // ---- load one round's matches ---------------------------------------------
  const loadRound = useCallback(
    async (id: string | null, silent = false) => {
      if (!compId) return;
      const reqId = ++roundReqId.current;
      if (!silent) setRoundLoading(true);
      setRoundError(false);
      try {
        const qs = id ? `?gameset=${encodeURIComponent(id)}` : "";
        const res = await fetch(
          `/api/competition/${encodeURIComponent(compId)}/matches${qs}`,
        );
        if (!res.ok) throw new Error("failed");
        const json: CompetitionMatchesResponse = await res.json();
        if (reqId !== roundReqId.current) return; // stale response
        setRound(json);
      } catch {
        if (reqId !== roundReqId.current) return;
        setRoundError(true);
      } finally {
        if (reqId === roundReqId.current && !silent) setRoundLoading(false);
      }
    },
    [compId],
  );

  // once the round list is known, load the active round (default selection)
  useEffect(() => {
    if (currentInfo && !gamesetId && currentInfo.gamesets.length > 0) {
      loadRound(null); // backend picks the active round
    }
  }, [currentInfo, gamesetId, loadRound]);

  // stale-while-revalidate: when the backend served possibly-stale data and
  // is refreshing it in the background (refreshing=true), quietly re-fetch
  // a few seconds later so the fresh copy appears without reopening the
  // dialog. Chain is capped and resets as soon as fresh data lands.
  useEffect(() => {
    if (!open) return;
    const infoRefreshing = !!currentInfo?.refreshing;
    const roundRefreshing = !!currentRound?.refreshing;
    if (!infoRefreshing && !roundRefreshing) {
      refreshChain.current = 0;
      return;
    }
    if (refreshChain.current >= 4) return;
    refreshChain.current += 1;
    const timer = setTimeout(() => {
      if (currentInfo?.refreshing) loadInfo();
      if (currentRound?.refreshing) {
        loadRound(currentRound.gameset.gameSetTypeId, true);
      }
    }, 6000);
    return () => clearTimeout(timer);
  }, [open, currentInfo, currentRound, loadInfo, loadRound]);

  const selectRound = (gs: GamesetRef) => {
    if (gs.gameSetTypeId === gamesetId) return;
    setGamesetId(gs.gameSetTypeId);
    setRound(null);
    loadRound(gs.gameSetTypeId);
  };

  const gamesets = currentInfo?.gamesets ?? [];
  const selectedGs =
    currentRound?.gameset ??
    (gamesetId ? gamesets.find((g) => g.gameSetTypeId === gamesetId) ?? null : null);
  // busy = a round fetch is in flight, or the round list is known but the
  // default round hasn't landed yet (covers the frame before loadRound starts)
  const roundBusy =
    roundLoading || (!currentRound && !roundError && gamesets.length > 0);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent
        aria-describedby={undefined}
        dir={lang === "ar" ? "rtl" : "ltr"}
        showCloseButton={false}
        className="max-h-[92dvh] grid-cols-[minmax(0,1fr)] gap-0 overflow-y-auto rounded-lg border border-[#b9c8dd] p-0 sm:max-w-2xl"
      >
        {competition && (
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
              {/* League name on the first line, season year UNDER it. The
                  year used to sit at the inline-end of the title row
                  (ms-auto) - exactly where the absolutely-positioned close
                  button lives - so the two merged into one confusing
                  "[X] 2026/2027" blob and the close button looked missing.
                  pe-10 reserves the close button's corner so a long league
                  name truncates before ever flowing under it. */}
              <DialogTitle className="flex flex-col gap-1 pe-10 leading-snug">
                <span className="flex items-center gap-2 text-[15px] font-bold">
                  <Crest url={competition.imageUrl} size={26} />
                  <span className="min-w-0 truncate">{compLabel(competition, lang)}</span>
                  {currentInfo?.refreshing && (
                    <span
                      className="ms-1 flex shrink-0 items-center gap-1 rounded bg-white/15 px-1.5 py-0.5 text-[10px] font-semibold text-white/85"
                      title={s.refreshing}
                    >
                      <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
                      {s.refreshing}
                    </span>
                  )}
                </span>
                {currentInfo?.season?.name && (
                  <span className="ms-[34px] inline-flex shrink-0 items-center self-start rounded bg-white/15 px-1.5 py-0.5 text-[11px] font-semibold">
                    {currentInfo.season.name}
                  </span>
                )}
              </DialogTitle>
            </div>

            {/* body: loading/error show IN PLACE - never the previous
                competition's standings (currentInfo is keyed to compId) */}
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
                    onClick={loadInfo}
                    className="flex items-center gap-1.5 rounded border border-[#17457f] bg-[#17457f] px-3 py-1.5 text-[13px] font-semibold text-white hover:bg-[#123a70]"
                  >
                    <RefreshCw className="h-3.5 w-3.5" /> {s.retry}
                  </button>
                </div>
              )}

              {currentInfo && (
                <Tabs defaultValue={hasStandings ? "table" : "rounds"} dir={lang === "ar" ? "rtl" : "ltr"}>
                  {/* flex + flex-1 triggers: robust on every mobile engine
                      (grid minmax(0,1fr) + nowrap labels failed to paint on
                      some Android browsers) */}
                  <TabsList className="w-full gap-1 rounded-md border border-[#c3cedd] bg-[#eef3fa] p-1">
                    {hasStandings && (
                      <TabsTrigger
                        value="table"
                        className="data-[state=active]:bg-white data-[state=active]:text-[#17457f] data-[state=active]:shadow-sm"
                      >
                        {s.tableTab}
                      </TabsTrigger>
                    )}
                    <TabsTrigger
                      value="rounds"
                      className="data-[state=active]:bg-white data-[state=active]:text-[#17457f] data-[state=active]:shadow-sm"
                    >
                      {s.roundsTab}
                    </TabsTrigger>
                  </TabsList>

                  {/* ---------- standings table ---------- */}
                  {hasStandings && currentInfo.standings && (
                    <TabsContent value="table" className="mt-3">
                      <StandingsView
                        tables={currentInfo.standings.tables}
                        markers={currentInfo.standings.markers}
                        lang={lang}
                        strings={s}
                        onOpenTeam={onOpenTeam}
                      />
                    </TabsContent>
                  )}

                  {/* ---------- rounds & results ---------- */}
                  <TabsContent value="rounds" className="mt-3">
                    <RoundsView
                      gamesets={gamesets}
                      selected={selectedGs}
                      round={currentRound}
                      loading={roundBusy}
                      error={roundError}
                      lang={lang}
                      strings={s}
                      onSelect={selectRound}
                      onRetry={() => loadRound(gamesetId)}
                      onOpenMatch={onOpenMatch}
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
// standings table
// ---------------------------------------------------------------------------
function markerColor(marker: StandingsMarker | undefined): string | null {
  if (!marker) return null;
  if (marker.type === "RELEGATION") return "#b3392f";
  if (marker.type === "PROMOTION") return "#1d7a1d";
  return "#17457f";
}

function StandingsView({
  tables,
  markers,
  lang,
  strings: s,
  onOpenTeam,
}: {
  tables: StandingsTable[];
  markers: StandingsMarker[];
  lang: Lang;
  strings: ReturnType<typeof t>;
  onOpenTeam?: (team: TeamRef) => void;
}) {
  const markerById = useMemo(
    () => Object.fromEntries(markers.map((m) => [m.id, m])),
    [markers],
  );
  const showTableNames = tables.length > 1;

  return (
    <div className="space-y-4">
      {tables.map((tbl, ti) => (
        <div key={ti} className="overflow-hidden rounded-md border border-[#dbe4ef] bg-white">
          {showTableNames && tbl.name && (
            <div className="border-b border-[#dbe4ef] bg-[#eef3fa] px-3 py-1.5 text-[12px] font-bold text-[#17457f]">
              {tbl.name}
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="w-full min-w-[340px] border-collapse text-[12.5px]">
              <thead>
                <tr className="bg-[#eef3fa] text-[11px] font-bold text-[#4a5a70]">
                  <th className="w-8 px-1 py-1.5 text-center">{s.posCol}</th>
                  <th className="px-2 py-1.5 text-start font-bold">&nbsp;</th>
                  <th className="w-9 px-1 py-1.5 text-center">{s.playedCol}</th>
                  <th className="hidden w-9 px-1 py-1.5 text-center md:table-cell">{s.winCol}</th>
                  <th className="hidden w-9 px-1 py-1.5 text-center md:table-cell">{s.drawCol}</th>
                  <th className="hidden w-9 px-1 py-1.5 text-center md:table-cell">{s.loseCol}</th>
                  <th className="hidden w-9 px-1 py-1.5 text-center md:table-cell">{s.gfCol}</th>
                  <th className="hidden w-9 px-1 py-1.5 text-center md:table-cell">{s.gaCol}</th>
                  <th className="hidden w-10 px-1 py-1.5 text-center sm:table-cell">{s.gdCol}</th>
                  <th className="w-10 px-1 py-1.5 text-center">{s.pointsCol}</th>
                  <th className="hidden w-[104px] px-2 py-1.5 text-center md:table-cell">
                    {s.formCol}
                  </th>
                </tr>
              </thead>
              <tbody>
                {tbl.rows.map((r, i) => {
                  const zone = markerById[r.markers[0]];
                  const zoneColor = markerColor(zone);
                  return (
                    <tr
                      key={r.team.id}
                      className={`border-t border-[#e2e9f2] ${i % 2 === 1 ? "bg-[#f6f9fd]" : "bg-white"}`}
                    >
                      <td className="px-1 py-1.5 text-center">
                        <span
                          className="inline-flex h-5 w-5 items-center justify-center rounded text-[11px] font-bold tabular-nums text-white"
                          style={{ backgroundColor: zoneColor ?? "#93a1b3" }}
                        >
                          {r.position}
                        </span>
                      </td>
                      <td className="px-2 py-1.5">
                        {/* team cell: a button (valid inside td) that opens the
                            team dialog when the parent handles it */}
                        {onOpenTeam && r.team.id ? (
                          <button
                            type="button"
                            onClick={() => onOpenTeam(r.team)}
                            title={s.teamInfo}
                            className="group/team flex min-w-0 items-center gap-1.5 rounded px-0.5 py-0.5 text-start transition-colors hover:bg-[#e8f1fb] focus:outline-none"
                          >
                            <Crest url={r.team.crestUrl} size={17} />
                            <span className="truncate font-semibold text-[#1c2b3a] underline-offset-2 group-hover/team:underline">
                              {nameOf(r.team, lang)}
                            </span>
                          </button>
                        ) : (
                          <span className="flex min-w-0 items-center gap-1.5">
                            <Crest url={r.team.crestUrl} size={17} />
                            <span className="truncate font-semibold text-[#1c2b3a]">
                              {nameOf(r.team, lang)}
                            </span>
                          </span>
                        )}
                      </td>
                      <td className="px-1 py-1.5 text-center tabular-nums text-[#33455e]">{r.played}</td>
                      <td className="hidden px-1 py-1.5 text-center tabular-nums text-[#33455e] md:table-cell">{r.win}</td>
                      <td className="hidden px-1 py-1.5 text-center tabular-nums text-[#33455e] md:table-cell">{r.draw}</td>
                      <td className="hidden px-1 py-1.5 text-center tabular-nums text-[#33455e] md:table-cell">{r.lose}</td>
                      <td className="hidden px-1 py-1.5 text-center tabular-nums text-[#33455e] md:table-cell">{r.goalsFor}</td>
                      <td className="hidden px-1 py-1.5 text-center tabular-nums text-[#33455e] md:table-cell">{r.goalsAgainst}</td>
                      <td className="hidden px-1 py-1.5 text-center tabular-nums font-semibold text-[#33455e] sm:table-cell">
                        {r.goalDiff !== null && r.goalDiff > 0 ? `+${r.goalDiff}` : r.goalDiff}
                      </td>
                      <td className="px-1 py-1.5 text-center font-extrabold tabular-nums text-[#14263a]">
                        {r.points}
                      </td>
                      <td className="hidden px-2 py-1.5 md:table-cell">
                        <span className="flex items-center justify-center gap-1">
                          {r.form.slice(-5).map((f, fi) => (
                            <span
                              key={fi}
                              className={`inline-flex h-[18px] w-[18px] items-center justify-center rounded text-[10px] font-bold ${
                                f.wdl === "WIN"
                                  ? "bg-[#e2f2e2] text-[#1d7a1d]"
                                  : f.wdl === "DRAW"
                                    ? "bg-[#eef0f3] text-[#5b6b80]"
                                    : "bg-[#fbe7e5] text-[#b3392f]"
                              }`}
                            >
                              {f.wdl === "WIN" ? s.winForm : f.wdl === "DRAW" ? s.drawForm : s.loseForm}
                            </span>
                          ))}
                        </span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      {/* zone legend */}
      {markers.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-1 text-[11px] text-[#5b6b80]">
          {markers.map((m) => (
            <span key={m.id} className="inline-flex items-center gap-1.5">
              <span
                className="inline-block h-2.5 w-2.5 rounded-sm"
                style={{ backgroundColor: markerColor(m) ?? "#93a1b3" }}
              />
              {lang === "ar" ? m.nameAr || m.nameEn : m.nameEn || m.nameAr}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// rounds & results
// ---------------------------------------------------------------------------
function RoundsView({
  gamesets,
  selected,
  round,
  loading,
  error,
  lang,
  strings: s,
  onSelect,
  onRetry,
  onOpenMatch,
}: {
  gamesets: GamesetRef[];
  selected: GamesetRef | null;
  round: CompetitionMatchesResponse | null;
  loading: boolean;
  error: boolean;
  lang: Lang;
  strings: ReturnType<typeof t>;
  onSelect: (gs: GamesetRef) => void;
  onRetry: () => void;
  onOpenMatch: (m: MatchRow) => void;
}) {
  const scrollerRef = useRef<HTMLDivElement>(null);

  // group round matches by local calendar date
  const groups = useMemo(() => {
    const out: { key: string; label: string; matches: MatchRow[] }[] = [];
    const byKey = new Map<string, MatchRow[]>();
    for (const m of round?.matches ?? []) {
      if (!m.kickoffUtc) continue;
      const d = new Date(m.kickoffUtc);
      if (isNaN(d.getTime())) continue;
      const key = d.toLocaleDateString("en-CA"); // YYYY-MM-DD local
      const label = new Intl.DateTimeFormat(lang === "ar" ? "ar-MA-u-nu-latn" : "en-GB", {
        weekday: "long",
        day: "numeric",
        month: "long",
      }).format(d);
      if (!byKey.has(key)) {
        byKey.set(key, []);
        out.push({ key, label, matches: byKey.get(key)! });
      }
      byKey.get(key)!.push(m);
    }
    return out;
  }, [round, lang]);

  if (gamesets.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-[#7d8ea3]">{s.noMatchesInRound}</p>
    );
  }

  return (
    <div className="space-y-3">
      {/* round chips */}
      <div className="flex items-center gap-1.5">
        <button
          type="button"
          aria-label="scroll rounds"
          onClick={() => scrollerRef.current?.scrollBy({ left: (lang === "ar" ? 1 : -1) * 200, behavior: "smooth" })}
          className="hidden h-8 w-6 shrink-0 items-center justify-center rounded border border-[#c3cedd] bg-white text-[#4a5a70] hover:bg-[#eef3fa] sm:flex"
        >
          <ChevronLeft className="h-4 w-4 rtl:hidden" />
          <ChevronRight className="h-4 w-4 ltr:hidden" />
        </button>
        <div
          ref={scrollerRef}
          className="flex min-w-0 flex-1 gap-1.5 overflow-x-auto pb-1 [scrollbar-width:thin]"
          role="tablist"
          aria-label={s.selectRound}
        >
          {gamesets.map((gs) => {
            const active = gs.gameSetTypeId === (selected?.gameSetTypeId ?? "");
            return (
              <button
                key={gs.gameSetTypeId}
                type="button"
                role="tab"
                aria-selected={active}
                onClick={() => onSelect(gs)}
                className={`shrink-0 whitespace-nowrap rounded-full border px-3 py-1 text-[12px] font-semibold transition-colors ${
                  active
                    ? "border-[#17457f] bg-[#17457f] text-white"
                    : "border-[#c3cedd] bg-white text-[#33455e] hover:border-[#17457f] hover:text-[#17457f]"
                }`}
              >
                {lang === "ar" ? gs.nameAr || gs.nameEn : gs.nameEn || gs.nameAr}
                {gs.isActive && !active && (
                  <span className="ms-1.5 inline-block h-1.5 w-1.5 rounded-full bg-[#d31f26] align-middle" />
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* selected round header */}
      {selected && (
        <div className="flex items-center justify-between px-1 text-[12px] font-semibold text-[#4a5a70]">
          <span className="flex items-center gap-1.5">
            <CalendarDays className="h-3.5 w-3.5" />
            {lang === "ar" ? selected.nameAr || selected.nameEn : selected.nameEn || selected.nameAr}
            {selected.isActive && (
              <span className="rounded bg-[#fdeaea] px-1.5 py-0.5 text-[10px] font-bold text-[#d31f26]">
                {s.live}
              </span>
            )}
          </span>
          <span className="tabular-nums">
            {selected.matchCount} {s.roundMatchesCount}
          </span>
        </div>
      )}

      {/* matches */}
      {loading && !round && (
        <div className="flex items-center justify-center gap-2 py-8 text-[#5b6b80]">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span className="text-sm">{s.loading}</span>
        </div>
      )}

      {error && (
        <div className="flex flex-col items-center gap-3 py-6">
          <p className="text-sm text-[#b3392f]">{s.loadError}</p>
          <button
            type="button"
            onClick={onRetry}
            className="flex items-center gap-1.5 rounded border border-[#17457f] bg-[#17457f] px-3 py-1.5 text-[13px] font-semibold text-white hover:bg-[#123a70]"
          >
            <RefreshCw className="h-3.5 w-3.5" /> {s.retry}
          </button>
        </div>
      )}

      {round && !error && (
        <div className="space-y-3">
          {groups.length === 0 ? (
            <p className="py-6 text-center text-sm text-[#7d8ea3]">{s.noMatchesInRound}</p>
          ) : (
            groups.map((g) => (
              <div key={g.key} className="overflow-hidden rounded-md border border-[#dbe4ef] bg-white">
                <div className="border-b border-[#dbe4ef] bg-[#eef3fa] px-3 py-1 text-[11.5px] font-bold text-[#17457f]">
                  {g.label}
                </div>
                <div role="list">
                  {g.matches.map((m, i) => (
                    <RoundMatchRow
                      key={m.matchId}
                      m={m}
                      lang={lang}
                      zebra={i % 2 === 1}
                      onOpen={onOpenMatch}
                    />
                  ))}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}

/** Compact match row used inside a round (time | home | score | away). */
function RoundMatchRow({
  m,
  lang,
  zebra,
  onOpen,
}: {
  m: MatchRow;
  lang: Lang;
  zebra: boolean;
  onOpen: (m: MatchRow) => void;
}) {
  const st = statusDisplay(m, lang);
  const hasScore = m.homeScore !== null && m.awayScore !== null;
  const homeWin = hasScore && (m.homeScore ?? 0) > (m.awayScore ?? 0);
  const awayWin = hasScore && (m.awayScore ?? 0) > (m.homeScore ?? 0);
  const live = st.kind === "live";

  return (
    <button
      type="button"
      role="listitem"
      onClick={() => onOpen(m)}
      className={`block w-full border-b border-[#e2e9f2] px-2 py-2 text-start transition-colors last:border-b-0 hover:bg-[#e8f1fb] ${
        zebra ? "bg-[#f6f9fd]" : "bg-white"
      }`}
    >
      <div className="flex w-full items-center gap-2">
        {/* status / time */}
        <div className="w-[52px] shrink-0 text-center">
          {live ? (
            <span className="text-[12px] font-extrabold tabular-nums text-[#d31f26]">
              {st.main}
            </span>
          ) : st.kind === "done" ? (
            <span className="text-[11px] font-semibold text-[#5b6b80]">{st.main}</span>
          ) : st.kind === "cancelled" ? (
            <span className="text-[11px] font-semibold text-[#98a3b3]">{st.main}</span>
          ) : (
            <span className="text-[13px] font-bold tabular-nums text-[#33455e]">
              {formatTime(m.kickoffUtc, lang)}
            </span>
          )}
        </div>

        {/* home */}
        <div className="flex min-w-0 flex-1 items-center justify-end gap-1.5">
          <span
            className={`truncate text-[13.5px] ${
              homeWin ? "font-bold text-[#14263a]" : "font-medium text-[#1c2b3a]"
            }`}
          >
            {nameOf(m.homeTeam, lang)}
          </span>
          <Crest url={m.homeTeam.crestUrl} size={18} />
        </div>

        {/* score */}
        <div className="w-[64px] shrink-0 text-center">
          {hasScore ? (
            <span
              className={`text-[14.5px] font-extrabold tabular-nums leading-tight ${
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
          <Crest url={m.awayTeam.crestUrl} size={18} />
          <span
            className={`truncate text-[13.5px] ${
              awayWin ? "font-bold text-[#14263a]" : "font-medium text-[#1c2b3a]"
            }`}
          >
            {nameOf(m.awayTeam, lang)}
          </span>
        </div>
      </div>
    </button>
  );
}

/** Small icon button shown on the competition bar (opens this dialog). */
export function CompetitionButtonIcon() {
  return <Trophy className="h-4 w-4" />;
}
