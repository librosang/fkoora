"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { Loader2, RefreshCw, X } from "lucide-react";
import type { Lang, MatchRow, TeamDetail } from "@/lib/goal/types";
import { compLabel, formatDateTime, nameOf, statusDisplay, t } from "@/lib/i18n";
import { Crest } from "./crest";

/**
 * Team drill-down dialog: crest + bilingual names, the competitions where the
 * team currently appears in a table, its last results and upcoming fixtures.
 * Read-only and fast: the backend answers straight from PostgreSQL.
 */
export function TeamDialog({
  teamId,
  lang,
  onClose,
  onOpenMatch,
}: {
  teamId: string | null;
  lang: Lang;
  onClose: () => void;
  /** optional: clicking a stored match opens the match dialog */
  onOpenMatch?: (m: MatchRow) => void;
}) {
  const s = t(lang);
  const [detail, setDetail] = useState<TeamDetail | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    if (!teamId) return;
    setError(false);
    setDetail(null);
    try {
      const res = await fetch(`/api/team/${teamId}`);
      if (!res.ok) throw new Error("failed");
      setDetail(await res.json());
    } catch {
      setError(true);
    }
  }, [teamId]);

  useEffect(() => {
    if (!teamId) return;
    setDetail(null);
    setError(false);
    load();
  }, [teamId, load]);

  const team = detail?.team;
  const showScore = (m: MatchRow) => m.homeScore !== null && m.awayScore !== null;

  return (
    <Dialog open={!!teamId} onOpenChange={(v) => !v && onClose()}>
      <DialogContent
        aria-describedby={undefined}
        dir={lang === "ar" ? "rtl" : "ltr"}
        showCloseButton={false}
        className="max-h-[92dvh] grid-cols-[minmax(0,1fr)] gap-0 overflow-y-auto rounded-lg border border-[#b9c8dd] p-0 sm:max-w-xl"
      >
        {teamId && (
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
              <DialogTitle className="flex items-center gap-3 text-[15px] font-bold">
                <Crest url={team?.crestUrl} size={38} />
                <span className="min-w-0">
                  <span className="block truncate">{nameOf(team || {}, lang) || "..."}</span>
                  {team?.code && (
                    <span className="text-[11px] font-semibold text-white/70">
                      {team.code}
                      {team.shortNameEn ? ` · ${team.shortNameEn}` : ""}
                    </span>
                  )}
                </span>
              </DialogTitle>
            </div>

            {/* body */}
            <div className="p-3 sm:p-4" aria-busy={!detail && !error}>
              {!detail && !error && (
                <div className="flex items-center justify-center gap-2 py-10 text-[#5b6b80]">
                  <Loader2 className="h-5 w-5 animate-spin" />
                  <span className="text-sm">{s.loading}</span>
                </div>
              )}

              {error && !detail && (
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

              {detail && (
                <div className="space-y-4">
                  {/* standings the team currently appears in */}
                  {detail.standings.map((g, gi) => (
                    <TeamStandingsMini key={gi} group={g} lang={lang} />
                  ))}

                  <MatchSection
                    title={s.teamResults}
                    matches={detail.results}
                    lang={lang}
                    teamId={teamId}
                    onOpenMatch={onOpenMatch}
                    showScore={showScore}
                  />
                  <MatchSection
                    title={s.teamFixtures}
                    matches={detail.fixtures}
                    lang={lang}
                    teamId={teamId}
                    onOpenMatch={onOpenMatch}
                    showScore={showScore}
                  />
                </div>
              )}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Compact table of one competition, highlighting the team's own row. */
function TeamStandingsMini({
  group,
  lang,
}: {
  group: TeamDetail["standings"][number];
  lang: Lang;
}) {
  const s = t(lang);
  return (
    <div className="overflow-hidden rounded-md border border-[#dbe4ef]">
      <div className="border-b border-[#dbe4ef] bg-[#eef3fa] px-3 py-1.5 text-[12px] font-bold text-[#17457f]">
        {compLabel(group.competition, lang)}
        {group.seasonName && (
          <span className="ms-2 font-semibold text-[#5b6b80]">
            {s.seasonLabel} {group.seasonName}
          </span>
        )}
      </div>
      <table className="w-full border-collapse text-[12px]">
        <thead>
          <tr className="border-b border-[#e2e9f2] text-[10.5px] font-semibold text-[#5b6b80]">
            <th className="px-1.5 py-1 text-center">{s.posCol}</th>
            <th className="px-1.5 py-1 text-start">{lang === "ar" ? "الفريق" : "Team"}</th>
            <th className="px-1.5 py-1 text-center">{s.playedCol}</th>
            <th className="px-1.5 py-1 text-center">{s.winCol}</th>
            <th className="px-1.5 py-1 text-center">{s.drawCol}</th>
            <th className="px-1.5 py-1 text-center">{s.loseCol}</th>
            <th className="px-1.5 py-1 text-center">{s.gdCol}</th>
            <th className="px-1.5 py-1 text-center">{s.pointsCol}</th>
          </tr>
        </thead>
        <tbody>
          {group.rows.map((r, i) => (
            <tr
              key={r.teamId || i}
              className={`border-b border-[#eef2f8] last:border-b-0 ${
                r.mine ? "bg-[#e8f1fb] font-bold" : ""
              }`}
            >
              <td className="px-1.5 py-1 text-center tabular-nums text-[#33455e]">{r.position}</td>
              <td className="truncate px-1.5 py-1 text-[#1c2b3a]">
                {lang === "ar" ? r.teamNameAr || r.teamNameEn : r.teamNameEn || r.teamNameAr}
              </td>
              <td className="px-1.5 py-1 text-center tabular-nums">{r.played ?? "-"}</td>
              <td className="px-1.5 py-1 text-center tabular-nums">{r.win ?? "-"}</td>
              <td className="px-1.5 py-1 text-center tabular-nums">{r.draw ?? "-"}</td>
              <td className="px-1.5 py-1 text-center tabular-nums">{r.lose ?? "-"}</td>
              <td className="px-1.5 py-1 text-center tabular-nums">{r.goalDiff ?? "-"}</td>
              <td className="px-1.5 py-1 text-center font-extrabold tabular-nums text-[#17457f]">
                {r.points ?? "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One labeled list of matches (results or fixtures). */
function MatchSection({
  title,
  matches,
  lang,
  teamId,
  onOpenMatch,
  showScore,
}: {
  title: string;
  matches: MatchRow[];
  lang: Lang;
  teamId: string;
  onOpenMatch?: (m: MatchRow) => void;
  showScore: (m: MatchRow) => boolean;
}) {
  const s = t(lang);
  if (!matches.length) return null;
  return (
    <div className="overflow-hidden rounded-md border border-[#dbe4ef]">
      <div className="border-b border-[#dbe4ef] bg-[#eef3fa] px-3 py-1.5 text-[12px] font-bold text-[#17457f]">
        {title}
      </div>
      <div role="list">
        {matches.map((m) => {
          const st = statusDisplay(m, lang);
          const mine = m.homeTeam.id === teamId ? m.homeTeam : m.awayTeam;
          const opp = m.homeTeam.id === teamId ? m.awayTeam : m.homeTeam;
          const hasScore = showScore(m);
          const homeGoals = m.homeTeam.id === teamId ? m.homeScore : m.awayScore;
          const awayGoals = m.homeTeam.id === teamId ? m.awayScore : m.homeScore;
          const won = hasScore && homeGoals !== null && awayGoals !== null && homeGoals > awayGoals;
          const lost = hasScore && homeGoals !== null && awayGoals !== null && homeGoals < awayGoals;
          return (
            <button
              key={m.matchId}
              type="button"
              role="listitem"
              onClick={() => onOpenMatch?.(m)}
              disabled={!onOpenMatch}
              className={`flex w-full items-center gap-2 border-b border-[#eef2f8] px-2.5 py-1.5 text-start text-[12.5px] transition-colors last:border-b-0 ${
                onOpenMatch ? "hover:bg-[#e8f1fb]" : ""
              }`}
            >
              <span
                className={`w-[46px] shrink-0 text-[11px] font-bold tabular-nums ${
                  st.kind === "live" ? "text-[#d31f26]" : "text-[#5b6b80]"
                }`}
              >
                {st.main}
              </span>
              <Crest url={mine.crestUrl} size={17} />
              <span className={`min-w-0 flex-1 truncate font-semibold ${won ? "text-[#1d7a1d]" : lost ? "text-[#b3392f]" : "text-[#1c2b3a]"}`}>
                {nameOf(mine, lang)} <span className="text-[#93a1b3]">{s.vs}</span> {nameOf(opp, lang)}
              </span>
              <span className="shrink-0 truncate text-[10.5px] text-[#7d8ea3]">
                {compLabel(m.competition, lang)}
              </span>
              {hasScore ? (
                <span className="shrink-0 text-[13px] font-extrabold tabular-nums text-[#17457f]">
                  {homeGoals} - {awayGoals}
                </span>
              ) : (
                <span className="shrink-0 text-[12px] font-semibold text-[#a5b1c0]">
                  {formatDateTime(m.kickoffUtc, lang).split(",")[0]}
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
