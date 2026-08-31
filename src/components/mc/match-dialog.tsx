"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Loader2, RefreshCw, X } from "lucide-react";
import type { Lang, LineupTeam, MatchDetail, MatchEvent, MatchRow } from "@/lib/goal/types";
import {
  compLabel,
  formatDateTime,
  nameOf,
  statLabel,
  statPercent,
  t,
} from "@/lib/i18n";
import { BallIcon, CardIcon, SubIcon, VarIcon } from "./icons";
import { Crest } from "./crest";

interface MatchDialogProps {
  match: MatchRow | null;
  lang: Lang;
  onClose: () => void;
  /** SSR-provided detail for `match` (match page): seeds the state and skips
   *  the first client fetch entirely */
  initialDetail?: MatchDetail | null;
  /** when defined, the dialog's open state is fully controlled by the parent
   *  (match page summary card); undefined = self-managed (listing behavior) */
  openOverride?: boolean;
  /** optional drill-downs: click a team name/crest or a player name */
  onOpenTeam?: (teamId: string) => void;
  onOpenPlayer?: (playerId: string) => void;
}

export function MatchDialog({
  match,
  lang,
  onClose,
  initialDetail,
  openOverride,
  onOpenTeam,
  onOpenPlayer,
}: MatchDialogProps) {
  const s = t(lang);
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<MatchDetail | null>(null);
  const [error, setError] = useState(false);

  // Only use the detail when it belongs to the match currently open - the
  // state briefly still holds the PREVIOUS match's detail after switching,
  // which would flash its events/lineups under the new match's header.
  const currentDetail =
    detail && match && detail.matchId === match.matchId ? detail : null;

  const load = useCallback(async () => {
    if (!match) return;
    setError(false);
    try {
      const qs = new URLSearchParams();
      if (match.slugAr) qs.set("slugAr", match.slugAr);
      if (match.slugEn) qs.set("slugEn", match.slugEn);
      const res = await fetch(`/api/match/${match.matchId}?${qs.toString()}`);
      if (!res.ok) throw new Error("failed");
      setDetail(await res.json());
    } catch {
      setError(true);
    }
  }, [match]);

  // data effect: seed from SSR detail when it belongs to this match (no
  // client fetch needed), otherwise fetch as before
  useEffect(() => {
    if (!match) return;
    if (initialDetail && initialDetail.matchId === match.matchId) {
      setDetail(initialDetail);
      return;
    }
    setDetail(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [match, load, initialDetail]);

  // open-state effect: skipped entirely when the parent controls the dialog
  // (openOverride defined) - it then opens/closes with the prop alone
  useEffect(() => {
    if (openOverride !== undefined) return;
    setOpen(!!match);
  }, [openOverride, match]);

  const isOpen = openOverride !== undefined ? openOverride : open;

  const home = currentDetail?.homeTeam || match?.homeTeam;
  const away = currentDetail?.awayTeam || match?.awayTeam;
  const isFixture = match ? match.status === "FIXTURE" : false;
  const showScore = match && match.homeScore !== null && match.awayScore !== null;

  const minuteLabel = (ev: MatchEvent) =>
    ev.minute === null || ev.minute === undefined
      ? "—"
      : `${ev.minute}${ev.extraMinute ? `+${ev.extraMinute}` : ""}'`;

  const visibleEvents = (currentDetail?.events || []).filter(
    (ev) => !ev.eventType.startsWith("PERIOD_"),
  );

  return (
    <Dialog open={isOpen} onOpenChange={(v) => !v && onClose()}>
      <DialogContent
        aria-describedby={undefined}
        dir={lang === "ar" ? "rtl" : "ltr"}
        showCloseButton={false}
        className="max-h-[92dvh] grid-cols-[minmax(0,1fr)] gap-0 overflow-y-auto rounded-lg border border-[#b9c8dd] p-0 sm:max-w-2xl"
      >
        {match && (
          <>
            {/* header block */}
            <div className="bg-gradient-to-b from-[#1d4f92] to-[#123a70] px-4 pb-3 pt-4 text-white">
              {/* close button - logical positioning keeps it correct in RTL & LTR */}
              <DialogClose
                aria-label={s.close}
                title={s.close}
                className="absolute end-3 top-3 z-20 rounded-md p-1.5 text-white/80 transition-colors hover:bg-white/15 hover:text-white focus:outline-none"
              >
                <X className="h-4 w-4" />
              </DialogClose>
              <DialogTitle className="flex items-start gap-2 text-[13px] font-semibold leading-snug">
                {compLabel(
                  currentDetail?.competition || match.competition,
                  lang,
                )}
                {(() => {
                  // the provider's round name is often just the competition
                  // name ("LaLiga") - prefer the gameset label (Game Week 4 /
                  // الجولة 4) which is what the round actually is
                  const gs =
                    lang === "ar"
                      ? match.gamesetNameAr || match.gamesetName
                      : match.gamesetName || match.gamesetNameAr;
                  const label = gs || currentDetail?.roundName || match.roundName;
                  return label ? (
                    <span className="rounded bg-white/15 px-1.5 py-0.5 text-[11px] font-medium">
                      {label}
                    </span>
                  ) : null;
                })()}
              </DialogTitle>

              {/* score row */}
              <div className="mt-3 grid grid-cols-[1fr_auto_1fr] items-center gap-2">
                <div className="flex flex-col items-center gap-1">
                  <TeamButton team={home} lang={lang} onOpenTeam={onOpenTeam} />
                </div>

                <div className="flex flex-col items-center gap-1">
                  {showScore ? (
                    // each number is its own span: in RTL the home score lands
                    // on the right (next to the home team) with zero bidi ambiguity
                    <span className="flex items-center gap-2 text-3xl font-extrabold tabular-nums tracking-wider">
                      <span>{match.homeScore}</span>
                      <span className="text-2xl font-bold text-white/60">-</span>
                      <span>{match.awayScore}</span>
                    </span>
                  ) : (
                    <span className="text-lg font-bold text-white/80">{s.vs}</span>
                  )}
                  <span className="rounded-full bg-white/15 px-2 py-0.5 text-[11px] font-semibold">
                    {match.status === "LIVE" && (
                      <span className="me-1 inline-block h-2 w-2 animate-pulse rounded-full bg-[#ff6b6b] align-middle" />
                    )}
                    {match.status === "RESULT" && s.ftLabel}
                    {match.status === "LIVE" && (match.period || s.liveNow)}
                    {match.status === "AET" && s.aetShort}
                    {match.status === "PEN" && s.pensShort}
                    {match.status === "CANCELLED" && s.cancelled}
                    {match.status === "POSTPONED" && s.postponed}
                    {match.status === "FIXTURE" && formatDateTime(match.kickoffUtc, lang)}
                  </span>
                  {currentDetail?.homeScoreHt !== null && currentDetail?.homeScoreHt !== undefined && (
                    <span className="inline-flex items-center gap-1 text-[11px] text-white/70">
                      {s.firstHalf}:
                      <span className="font-semibold tabular-nums">{currentDetail.homeScoreHt}</span>
                      <span>-</span>
                      <span className="font-semibold tabular-nums">{currentDetail.awayScoreHt}</span>
                    </span>
                  )}
                  {currentDetail?.homePenScore !== null && currentDetail?.homePenScore !== undefined && (
                    <span className="inline-flex items-center gap-1 text-[11px] text-white/70">
                      {s.pensShort}:
                      <span className="font-semibold tabular-nums">{currentDetail.homePenScore}</span>
                      <span>-</span>
                      <span className="font-semibold tabular-nums">{currentDetail.awayPenScore}</span>
                    </span>
                  )}
                </div>

                <div className="flex flex-col items-center gap-1">
                  <TeamButton team={away} lang={lang} onOpenTeam={onOpenTeam} />
                </div>
              </div>

              {/* meta line */}
              <div className="mt-3 flex flex-wrap items-center justify-center gap-x-4 gap-y-1 border-t border-white/20 pt-2 text-[11px] text-white/80">
                <span>{formatDateTime(currentDetail?.kickoffUtc || match.kickoffUtc, lang)}</span>
                {(currentDetail?.venueNameEn || currentDetail?.venueNameAr || match.venueNameEn || match.venueNameAr) && (
                  <span>
                    {s.venue}:{" "}
                    {lang === "ar"
                      ? currentDetail?.venueNameAr || match.venueNameAr || currentDetail?.venueNameEn || match.venueNameEn
                      : currentDetail?.venueNameEn || match.venueNameEn || currentDetail?.venueNameAr || match.venueNameAr}
                  </span>
                )}
                {currentDetail?.referee && (
                  <span>
                    {s.referee}: {currentDetail.referee}
                  </span>
                )}
              </div>
            </div>

            {/* body: loading shows IN PLACE - never the previous match's
                content (currentDetail is null until THIS match's data lands) */}
            <div className="p-3 sm:p-4" aria-busy={!currentDetail && !error}>
              {!currentDetail && !error && (
                <div className="flex items-center justify-center gap-2 py-10 text-[#5b6b80]">
                  <Loader2 className="h-5 w-5 animate-spin" />
                  <span className="text-sm">{s.loading}</span>
                </div>
              )}

              {error && !currentDetail && (
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

              {currentDetail && (
                <Tabs
                  defaultValue="events"
                  // Radix Tabs defaults to dir="ltr" when unset, which mirrored
                  // the whole tab body (events columns / lineups / stats) against
                  // the RTL header - pass the dialog direction explicitly.
                  dir={lang === "ar" ? "rtl" : "ltr"}
                >
                  {/* flex + flex-1 triggers: robust on every mobile engine
                      (grid minmax(0,1fr) + nowrap labels failed to paint on
                      some Android browsers) */}
                  <TabsList className="w-full gap-1 rounded-md border border-[#c3cedd] bg-[#eef3fa] p-1">
                    <TabsTrigger
                      value="events"
                      className="data-[state=active]:bg-white data-[state=active]:text-[#17457f] data-[state=active]:shadow-sm"
                    >
                      {s.events}
                    </TabsTrigger>
                    <TabsTrigger
                      value="lineups"
                      className="data-[state=active]:bg-white data-[state=active]:text-[#17457f] data-[state=active]:shadow-sm"
                    >
                      {s.lineups}
                    </TabsTrigger>
                    <TabsTrigger
                      value="stats"
                      className="data-[state=active]:bg-white data-[state=active]:text-[#17457f] data-[state=active]:shadow-sm"
                    >
                      {s.stats}
                    </TabsTrigger>
                  </TabsList>

                  {/* ---------- events ---------- */}
                  <TabsContent value="events" className="mt-3">
                    {visibleEvents.length === 0 ? (
                      <p className="py-6 text-center text-sm text-[#7d8ea3]">
                        {isFixture ? s.notStarted : s.noStats}
                      </p>
                    ) : (
                      <div className="space-y-1">
                        {visibleEvents.map((ev, i) => (
                          <div key={i} className="grid grid-cols-[1fr_52px_1fr] items-center">
                            {ev.teamSide === "home" ? (
                              <EventChip ev={ev} lang={lang} align="end" onOpenPlayer={onOpenPlayer} />
                            ) : (
                              <span />
                            )}
                            <div className="text-center text-[12px] font-bold tabular-nums text-[#5b6b80]">
                              {minuteLabel(ev)}
                            </div>
                            {ev.teamSide === "away" ? (
                              <EventChip ev={ev} lang={lang} align="start" onOpenPlayer={onOpenPlayer} />
                            ) : (
                              <span />
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </TabsContent>

                  {/* ---------- lineups ---------- */}
                  <TabsContent value="lineups" className="mt-3">
                    <div className="grid gap-4 md:grid-cols-2">
                      <LineupColumn team={currentDetail.lineups.home} lang={lang} onOpenPlayer={onOpenPlayer} />
                      <LineupColumn team={currentDetail.lineups.away} lang={lang} onOpenPlayer={onOpenPlayer} />
                    </div>
                  </TabsContent>

                  {/* ---------- stats ---------- */}
                  <TabsContent value="stats" className="mt-3">
                    {currentDetail.stats.length === 0 ? (
                      <p className="py-6 text-center text-sm text-[#7d8ea3]">{s.noStats}</p>
                    ) : (
                      <div className="space-y-3">
                        {currentDetail.stats.map((st) => {
                          const hv = Number(st.homeValue);
                          const av = Number(st.awayValue);
                          const valid = !isNaN(hv) && !isNaN(av);
                          const total = valid ? hv + av : 0;
                          const homePct = valid && total > 0 ? (hv / total) * 100 : 50;
                          const isPct = statPercent(st.statType, st.homeValue);
                          return (
                            <div key={st.statType}>
                              <div className="mb-1 flex items-center justify-between text-[12px]">
                                <span className="w-12 font-extrabold tabular-nums text-[#14263a]">
                                  {isPct && valid ? `${hv}%` : st.homeValue}
                                </span>
                                <span className="font-semibold text-[#5b6b80]">
                                  {statLabel(st.statType, lang)}
                                </span>
                                <span className="w-12 text-end font-extrabold tabular-nums text-[#14263a]">
                                  {isPct && valid ? `${av}%` : st.awayValue}
                                </span>
                              </div>
                              <div className="flex h-1.5 gap-0.5">
                                <div className="flex flex-1 justify-end overflow-hidden rounded bg-[#e4ebf4]">
                                  <div
                                    className="h-full rounded-s bg-[#17457f]"
                                    style={{ width: `${homePct}%` }}
                                  />
                                </div>
                                <div className="flex flex-1 overflow-hidden rounded bg-[#e4ebf4]">
                                  <div
                                    className="h-full rounded-e bg-[#4a7ebe]"
                                    style={{ width: `${100 - homePct}%` }}
                                  />
                                </div>
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    )}
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
// clickable team (crest + name) used in the dialog header
// ---------------------------------------------------------------------------
function TeamButton({
  team,
  lang,
  onOpenTeam,
}: {
  team?: MatchDetail["homeTeam"];
  lang: Lang;
  onOpenTeam?: (teamId: string) => void;
}) {
  const enabled = !!(onOpenTeam && team?.id);
  if (!enabled) {
    return (
      <>
        <Crest url={team?.crestUrl} size={34} />
        <span className="text-center text-[13px] font-bold leading-tight">
          {nameOf(team || {}, lang)}
        </span>
      </>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onOpenTeam?.(team!.id)}
      title={nameOf(team || {}, lang)}
      className="flex flex-col items-center gap-1 rounded-md p-1 transition-colors hover:bg-white/15 focus:outline-none"
    >
      <Crest url={team?.crestUrl} size={34} />
      <span className="text-center text-[13px] font-bold leading-tight underline-offset-2 hover:underline">
        {nameOf(team || {}, lang)}
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// event chip (icon + player + related player + VAR outcome)
// ---------------------------------------------------------------------------
function EventChip({
  ev,
  lang,
  align,
  onOpenPlayer,
}: {
  ev: MatchEvent;
  lang: Lang;
  align: "start" | "end";
  onOpenPlayer?: (playerId: string) => void;
}) {
  const s = t(lang);
  const player = nameOf(ev.player || {}, lang);
  const related = nameOf(ev.relatedPlayer || {}, lang);
  const hasRelated = ev.relatedPlayer && ev.relatedPlayer.id;

  const isVar = ev.eventType.startsWith("VAR");
  // A review ending with NO_GOAL / NO_PENALTY or decision=CANCELLED means
  // the goal/penalty was DISALLOWED (e.g. offside). The provider emits no
  // goal event for it at all - only this VAR event.
  const varCancelled =
    (ev.decision || "").toUpperCase() === "CANCELLED" ||
    (ev.outcome || "").toUpperCase().startsWith("NO_");
  const missed =
    ev.eventType === "GOAL_PENALTY_MISS" || ev.eventType === "GOAL_PENALTY_SHOOTOUT_MISS";
  const scored =
    ev.eventType === "GOAL" ||
    ev.eventType === "GOAL_OWN" ||
    ev.eventType === "GOAL_PENALTY" ||
    ev.eventType === "GOAL_PENALTY_SHOOTOUT";

  let varLabel: string | null = null;
  if (isVar) {
    if (ev.eventType === "VAR_GOAL_AWARDED")
      varLabel = varCancelled ? s.varGoalCancelled : s.varGoalConfirmed;
    else if (ev.eventType === "VAR_PENALTY_AWARDED")
      varLabel = varCancelled ? s.varPenaltyCancelled : s.varPenaltyAwarded;
    else if (ev.eventType === "VAR_PENALTY_NOT_AWARDED") varLabel = s.varPenaltyNotAwarded;
    else varLabel = s.varDecision;
  }

  let icon: React.ReactNode = null;
  if (isVar) {
    icon = <VarIcon cancelled={varCancelled} />;
  } else if (scored || missed) {
    icon = (
      <BallIcon className={`h-4 w-4 shrink-0 ${missed ? "text-[#7d8ea3]" : "text-[#14263a]"}`} />
    );
  } else if (ev.eventType === "CARD_YELLOW") {
    icon = <CardIcon color="yellow" />;
  } else if (ev.eventType === "CARD_RED" || ev.eventType === "CARD_SECOND_YELLOW") {
    icon = <CardIcon color="red" />;
  } else if (ev.eventType === "SUBSTITUTION") {
    icon = <SubIcon className="h-4 w-4 shrink-0 text-[#4a6b96]" />;
  } else {
    icon = <span className="h-2 w-2 shrink-0 rounded-full bg-[#7d8ea3]" />;
  }

  const iconTone = isVar ? (varCancelled ? "text-[#b3392f]" : "text-[#4a6b96]") : "";
  const chipTone = isVar && varCancelled ? "border-[#e8c4be] bg-[#fdf2f0]" : "border-[#dbe4ef] bg-[#f6f9fd]";

  const label = (
    <EventLabel
      ev={ev}
      player={player}
      related={hasRelated ? related : null}
      lang={lang}
      s={s}
      align={align}
      varLabel={varLabel}
      varCancelled={varCancelled}
      missed={missed}
      scored={scored}
      onOpenPlayer={onOpenPlayer}
    />
  );

  return (
    <div
      className={`flex max-w-full items-center gap-1.5 rounded border px-2 py-1 text-[12px] ${chipTone} ${
        align === "end" ? "justify-self-end" : "justify-self-start"
      }`}
    >
      {align === "end" ? (
        <>
          <span className="min-w-0">{label}</span>
          <span className={iconTone}>{icon}</span>
        </>
      ) : (
        <>
          <span className={iconTone}>{icon}</span>
          <span className="min-w-0">{label}</span>
        </>
      )}
    </div>
  );
}

function EventLabel({
  ev,
  player,
  related,
  lang,
  s,
  align,
  varLabel,
  varCancelled,
  missed,
  scored,
  onOpenPlayer,
}: {
  ev: MatchEvent;
  player: string;
  related: string | null;
  lang: Lang;
  s: ReturnType<typeof t>;
  align: "start" | "end";
  varLabel: string | null;
  varCancelled: boolean;
  missed: boolean;
  scored: boolean;
  onOpenPlayer?: (playerId: string) => void;
}) {
  const rtl = lang === "ar";
  // direction-safe: keep chips LTR-ordered internally but text uses page lang
  return (
    <span
      className={`flex flex-wrap items-center gap-x-1 ${align === "end" ? "justify-end" : "justify-start"}`}
      dir={rtl ? "rtl" : "ltr"}
    >
      {/* main person(s): substitutions show the incoming + outgoing pair */}
      {ev.eventType === "SUBSTITUTION" && related ? (
        <span className="inline-flex items-center gap-x-1.5">
          <PlayerName
            name={related}
            id={ev.relatedPlayer?.id || null}
            className="font-semibold text-[#1d7a1d]"
            prefix={"\u2191 "}
            onOpenPlayer={onOpenPlayer}
          />
          <span className="text-[#93a1b3]">·</span>
          <PlayerName
            name={player}
            id={ev.player?.id || null}
            className="font-semibold text-[#b3392f]"
            prefix={"\u2193 "}
            onOpenPlayer={onOpenPlayer}
          />
        </span>
      ) : (
        <PlayerName
          name={player}
          id={ev.player?.id || null}
          className={`font-semibold ${varLabel && varCancelled ? "text-[#8a2f28]" : "text-[#1c2b3a]"}`}
          onOpenPlayer={onOpenPlayer}
        />
      )}

      {/* VAR review outcome (e.g. disallowed goal) */}
      {varLabel && (
        <span className={`font-semibold ${varCancelled ? "text-[#b3392f]" : "text-[#4a6b96]"}`}>
          {varLabel}
        </span>
      )}

      {/* missed penalty / own goal annotations */}
      {!varLabel && missed && (
        <span className="font-semibold text-[#b3392f]">({s.missedPenalty})</span>
      )}
      {!varLabel && ev.eventType === "GOAL_OWN" && (
        <span className="text-[#5b6b80]">({s.ownGoal})</span>
      )}

      {related && ev.eventType !== "SUBSTITUTION" && !varLabel && !missed && (
        <span className="text-[#5b6b80]">
          ({s.assist}: {related})
        </span>
      )}

      {/* running score only for goals that actually counted */}
      {scored && ev.homeScoreAfter !== null && ev.homeScoreAfter !== undefined && (
        <span className="inline-flex items-center gap-1 font-extrabold tabular-nums text-[#17457f]">
          <span>{ev.homeScoreAfter}</span>
          <span>-</span>
          <span>{ev.awayScoreAfter}</span>
        </span>
      )}
    </span>
  );
}

// ---------------------------------------------------------------------------
// clickable player name (events + lineups)
// ---------------------------------------------------------------------------
function PlayerName({
  name,
  id,
  className,
  prefix,
  onOpenPlayer,
}: {
  name: string;
  id?: string | null;
  className?: string;
  prefix?: string;
  onOpenPlayer?: (playerId: string) => void;
}) {
  if (onOpenPlayer && id) {
    return (
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation();
          onOpenPlayer(id);
        }}
        title={name}
        className={`rounded text-start underline-offset-2 hover:underline focus:outline-none ${className || ""}`}
      >
        {prefix}
        {name}
      </button>
    );
  }
  return (
    <span className={className}>
      {prefix}
      {name}
    </span>
  );
}

// ---------------------------------------------------------------------------
// lineup column
// ---------------------------------------------------------------------------
function LineupColumn({
  team,
  lang,
  onOpenPlayer,
}: {
  team?: LineupTeam;
  lang: Lang;
  onOpenPlayer?: (playerId: string) => void;
}) {
  const s = t(lang);
  if (!team || team.entries.length === 0) {
    return (
      <div className="rounded-md border border-[#dbe4ef] bg-[#f6f9fd] p-4 text-center text-sm text-[#7d8ea3]">
        {s.lineupNotAnnounced}
      </div>
    );
  }

  const starters = team.entries.filter((e) => e.isStarter);
  const subs = team.entries.filter((e) => !e.isStarter);

  const PlayerLine = ({
    entry,
    dim,
  }: {
    entry: (typeof team.entries)[number];
    dim?: boolean;
  }) => (
    <li className={`flex items-center gap-2 py-1 text-[12.5px] ${dim ? "text-[#5b6b80]" : "text-[#1c2b3a]"}`}>
      <span className="w-6 shrink-0 text-end font-bold tabular-nums text-[#4a6b96]">
        {entry.shirtNumber ?? ""}
      </span>
      <span className={`min-w-0 flex-1 truncate font-medium ${dim ? "" : "font-semibold"}`}>
        <PlayerName
          name={nameOf(entry.person, lang)}
          id={entry.person?.id || null}
          onOpenPlayer={onOpenPlayer}
        />
        {entry.isCaptain && (
          <span className="ms-1 rounded bg-[#e8eff9] px-1 text-[10px] font-bold text-[#17457f]">
            {s.captain}
          </span>
        )}
      </span>
      {entry.rating !== null && entry.rating !== undefined && (
        <span className="shrink-0 rounded bg-[#eef3fa] px-1.5 py-0.5 text-[10.5px] font-bold tabular-nums text-[#33455e]">
          {entry.rating}
        </span>
      )}
    </li>
  );

  return (
    <div className="rounded-md border border-[#dbe4ef] bg-white">
      <div className="flex items-center justify-between border-b border-[#dbe4ef] bg-[#eef3fa] px-3 py-1.5">
        <span className="text-[12px] font-bold text-[#17457f]">
          {team.formation || ""}
        </span>
        <span className="text-[11.5px] font-semibold text-[#4a5a70]">
          {s.manager}: {nameOf(team.manager || {}, lang)}
        </span>
      </div>
      <ul className="px-3 py-1.5">
        {starters.map((e, i) => (
          <PlayerLine key={i} entry={e} />
        ))}
      </ul>
      <div className="border-t border-[#dbe4ef] bg-[#f6f9fd] px-3 py-1">
        <span className="text-[11px] font-bold text-[#5b6b80]">{s.substitutes}</span>
      </div>
      <ul className="px-3 py-1.5">
        {subs.map((e, i) => (
          <PlayerLine key={i} entry={e} dim />
        ))}
      </ul>
    </div>
  );
}
