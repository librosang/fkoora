"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { Loader2, RefreshCw, X } from "lucide-react";
import type { Lang, MatchRow, PlayerDetail } from "@/lib/goal/types";
import { formatDateTime, nameOf, statusDisplay, t } from "@/lib/i18n";
import { Crest } from "./crest";

/**
 * Player drill-down dialog: photo + bilingual names, bio card, career history
 * table and last appearances. Read-only: served from the players /
 * player_career_entries / lineups tables filled by the scraper walks.
 */
export function PlayerDialog({
  playerId,
  lang,
  onClose,
  onOpenTeam,
  onOpenMatch,
}: {
  playerId: string | null;
  lang: Lang;
  onClose: () => void;
  /** optional: click the current club / a career club -> team dialog */
  onOpenTeam?: (teamId: string) => void;
  /** optional: click a recent match -> match dialog (a stub row is built
   *  from the appearance; the match dialog fetches the full detail itself) */
  onOpenMatch?: (m: MatchRow) => void;
}) {
  const s = t(lang);
  const [detail, setDetail] = useState<PlayerDetail | null>(null);
  const [error, setError] = useState(false);

  const load = useCallback(async () => {
    if (!playerId) return;
    setError(false);
    setDetail(null);
    try {
      const res = await fetch(`/api/player/${playerId}`);
      if (!res.ok) throw new Error("failed");
      setDetail(await res.json());
    } catch {
      setError(true);
    }
  }, [playerId]);

  useEffect(() => {
    if (!playerId) return;
    setDetail(null);
    setError(false);
    load();
  }, [playerId, load]);

  const p = detail?.player;
  const rtl = lang === "ar";

  const openAppearance = (a: PlayerDetail["appearances"][number]) => {
    if (!onOpenMatch) return;
    onOpenMatch({
      matchId: a.matchId,
      kickoffUtc: a.kickoffUtc,
      status: a.status,
      homeTeam: a.homeTeam,
      awayTeam: a.awayTeam,
      competition: {
        id: "",
        nameEn: a.competitionNameEn,
        nameAr: a.competitionNameAr,
      },
      homeScore: a.homeScore,
      awayScore: a.awayScore,
      homeRedCards: 0,
      awayRedCards: 0,
    });
  };
  const positionLabel = (pos: string | null) => {
    if (!pos) return null;
    const map: Record<string, [string, string]> = {
      GOALKEEPER: ["Goalkeeper", "حارس مرمى"],
      DEFENDER: ["Defender", "مدافع"],
      MIDFIELDER: ["Midfielder", "وسط"],
      FORWARD: ["Forward", "مهاجم"],
    };
    const hit = map[pos.toUpperCase()];
    return hit ? (rtl ? hit[1] : hit[0]) : pos;
  };

  const born = p?.birthDate
    ? `${p.birthDate}${p.age ? ` (${p.age} ${s.ageShort})` : ""}`
    : p?.age
      ? `${p.age} ${s.ageShort}`
      : null;

  return (
    <Dialog open={!!playerId} onOpenChange={(v) => !v && onClose()}>
      <DialogContent
        aria-describedby={undefined}
        dir={rtl ? "rtl" : "ltr"}
        showCloseButton={false}
        className="max-h-[92dvh] grid-cols-[minmax(0,1fr)] gap-0 overflow-y-auto rounded-lg border border-[#b9c8dd] p-0 sm:max-w-xl"
      >
        {playerId && (
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
                <Crest url={p?.imageUrl} size={44} className="rounded-full bg-white/10" />
                <span className="min-w-0">
                  <span className="block truncate">
                    {p ? nameOf(p, lang) || s.playerProfile : "..."}
                  </span>
                  <span className="flex flex-wrap items-center gap-1.5 text-[11px] font-semibold text-white/75">
                    {p?.position && (
                      <span className="rounded bg-white/15 px-1.5 py-0.5">
                        {positionLabel(p.position)}
                      </span>
                    )}
                    {p?.shirtNumber != null && (
                      <span className="rounded bg-white/15 px-1.5 py-0.5 tabular-nums">
                        #{p.shirtNumber}
                      </span>
                    )}
                    {detail?.currentClub && (
                      <button
                        type="button"
                        onClick={() =>
                          detail.currentClub?.id && onOpenTeam?.(detail.currentClub.id)
                        }
                        className="rounded bg-white/15 px-1.5 py-0.5 transition-colors hover:bg-white/30"
                      >
                        {nameOf(detail.currentClub, lang)}
                      </button>
                    )}
                  </span>
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

              {detail && p && (
                <div className="space-y-4">
                  {!p.profileFetched && (
                    <p className="rounded border border-[#e8d9b8] bg-[#fdf8ec] px-3 py-2 text-[12px] text-[#7a5d1e]">
                      {s.playerNoProfile}
                    </p>
                  )}

                  {/* bio card */}
                  <div className="grid grid-cols-2 gap-x-3 gap-y-1.5 rounded-md border border-[#dbe4ef] bg-[#f6f9fd] p-3 text-[12.5px] sm:grid-cols-3">
                    <BioCell label={s.nationalityLabel}>
                      {rtl ? p.nationalityAr || p.nationalityEn : p.nationalityEn || p.nationalityAr}
                    </BioCell>
                    <BioCell label={s.bornLabel}>{born}</BioCell>
                    <BioCell label={s.heightLabel}>
                      {p.heightCm ? `${p.heightCm} cm` : null}
                    </BioCell>
                    <BioCell label={s.weightLabel}>
                      {p.weightKg ? `${p.weightKg} kg` : null}
                    </BioCell>
                    <BioCell label={s.birthplaceLabel}>
                      {rtl
                        ? p.placeOfBirthAr || p.placeOfBirthEn
                        : p.placeOfBirthEn || p.placeOfBirthAr}
                    </BioCell>
                    {p.fullNameEn && (
                      <BioCell label={rtl ? "الاسم الكامل" : "Full name"}>
                        {rtl ? p.fullNameAr || p.fullNameEn : p.fullNameEn || p.fullNameAr}
                      </BioCell>
                    )}
                  </div>

                  {/* career history */}
                  {detail.career.length > 0 && (
                    <div className="overflow-hidden rounded-md border border-[#dbe4ef]">
                      <div className="border-b border-[#dbe4ef] bg-[#eef3fa] px-3 py-1.5 text-[12px] font-bold text-[#17457f]">
                        {s.playerCareer}
                      </div>
                      <table className="w-full border-collapse text-[12px]">
                        <thead>
                          <tr className="border-b border-[#e2e9f2] text-[10.5px] font-semibold text-[#5b6b80]">
                            <th className="px-1.5 py-1 text-start">{s.seasonCol}</th>
                            <th className="px-1.5 py-1 text-start">{s.clubCol}</th>
                            <th className="px-1.5 py-1 text-center">{s.appsCol}</th>
                            <th className="px-1.5 py-1 text-center">{s.goalsCol}</th>
                            <th className="px-1.5 py-1 text-center">{s.assistsCol}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {detail.career.map((c, i) => (
                            <tr key={i} className="border-b border-[#eef2f8] last:border-b-0">
                              <td className="px-1.5 py-1 tabular-nums text-[#33455e]">
                                {c.seasonName || "-"}
                                {c.isLoan && (
                                  <span className="ms-1 rounded bg-[#e8eff9] px-1 text-[9.5px] font-bold text-[#17457f]">
                                    {lang === "ar" ? "إعارة" : "loan"}
                                  </span>
                                )}
                              </td>
                              <td className="px-1.5 py-1 text-[#1c2b3a]">
                                {c.teamId && onOpenTeam ? (
                                  <button
                                    type="button"
                                    onClick={() => c.teamId && onOpenTeam(c.teamId)}
                                    className="font-semibold text-[#17457f] underline-offset-2 hover:underline"
                                  >
                                    {rtl ? c.teamNameAr || c.teamNameEn : c.teamNameEn || c.teamNameAr}
                                  </button>
                                ) : (
                                  (rtl ? c.teamNameAr || c.teamNameEn : c.teamNameEn || c.teamNameAr) || "-"
                                )}
                              </td>
                              <td className="px-1.5 py-1 text-center tabular-nums">{c.appearances ?? "-"}</td>
                              <td className="px-1.5 py-1 text-center font-bold tabular-nums text-[#17457f]">
                                {c.goals ?? "-"}
                              </td>
                              <td className="px-1.5 py-1 text-center tabular-nums">{c.assists ?? "-"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {/* recent appearances */}
                  {detail.appearances.length > 0 && (
                    <div className="overflow-hidden rounded-md border border-[#dbe4ef]">
                      <div className="border-b border-[#dbe4ef] bg-[#eef3fa] px-3 py-1.5 text-[12px] font-bold text-[#17457f]">
                        {s.playerRecent}
                      </div>
                      <div role="list">
                        {detail.appearances.map((a) => {
                          const st = statusDisplay(
                            { status: a.status, kickoffUtc: a.kickoffUtc },
                            lang,
                          );
                          const hasScore = a.homeScore !== null && a.awayScore !== null;
                          return (
                            <button
                              key={a.matchId}
                              type="button"
                              role="listitem"
                              onClick={() => openAppearance(a)}
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
                              <span className="min-w-0 flex-1 truncate text-[#1c2b3a]">
                                {nameOf(a.homeTeam, lang)}{" "}
                                <span className="text-[#93a1b3]">{s.vs}</span>{" "}
                                {nameOf(a.awayTeam, lang)}
                              </span>
                              <span className="shrink-0 truncate text-[10.5px] text-[#7d8ea3]">
                                {rtl
                                  ? a.competitionNameAr || a.competitionNameEn
                                  : a.competitionNameEn || a.competitionNameAr}
                              </span>
                              {a.rating !== null && (
                                <span className="shrink-0 rounded bg-[#eef3fa] px-1.5 py-0.5 text-[10.5px] font-bold tabular-nums text-[#33455e]">
                                  {a.rating}
                                </span>
                              )}
                              {hasScore ? (
                                <span className="shrink-0 text-[13px] font-extrabold tabular-nums text-[#17457f]">
                                  {a.homeScore} - {a.awayScore}
                                </span>
                              ) : (
                                <span className="shrink-0 text-[11px] font-semibold text-[#a5b1c0]">
                                  {formatDateTime(a.kickoffUtc, lang).split(",")[0]}
                                </span>
                              )}
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {p.profileFetched && detail.career.length === 0 && detail.appearances.length === 0 && (
                    <p className="py-4 text-center text-sm text-[#7d8ea3]">{s.noStats}</p>
                  )}
                </div>
              )}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function BioCell({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0">
      <div className="text-[10.5px] font-semibold uppercase tracking-wide text-[#7d8ea3]">
        {label}
      </div>
      <div className="truncate font-semibold text-[#1c2b3a]">{children || "-"}</div>
    </div>
  );
}
