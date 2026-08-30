"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { Loader2, RefreshCw, ShieldQuestion, X } from "lucide-react";
import type { Lang, PlayerDetail, TeamRef } from "@/lib/goal/types";
import {
  birthDateLabel,
  heightLabel,
  nameOf,
  positionLabel,
  t,
  weightLabel,
} from "@/lib/i18n";
import { Crest } from "./crest";

/** What the dialog needs to identify the player being opened. */
export interface PlayerDialogTarget {
  id: string;
  nameEn?: string | null;
  nameAr?: string | null;
}

interface PlayerDialogProps {
  player: PlayerDialogTarget | null;
  lang: Lang;
  onClose: () => void;
  /** open the team dialog for the player's current club */
  onOpenTeam: (t: TeamRef) => void;
  /** SSR-provided detail for `player` (player page): seeds the state and
   *  skips the first client fetch entirely */
  initialDetail?: PlayerDetail | null;
  /** when defined, the dialog's open state is fully controlled by the parent
   *  (player page summary card); undefined = self-managed (listing behavior) */
  openOverride?: boolean;
  /** true when this dialog must stack ABOVE another dialog already open */
  elevated?: boolean;
}

/**
 * Player dialog: photo + bio card (position, club, age, height, ...) and the
 * full career timeline per season. Opened from lineups, squads and event chips.
 */
export function PlayerDialog({
  player,
  lang,
  onClose,
  onOpenTeam,
  initialDetail,
  openOverride,
  elevated,
}: PlayerDialogProps) {
  const s = t(lang);
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<PlayerDetail | null>(null);
  const [error, setError] = useState(false);
  const [errorFor, setErrorFor] = useState<string | null>(null);

  const playerId = player?.id ?? null;

  // Only use the detail when it belongs to the player currently open - the
  // state briefly still holds the PREVIOUS player's detail after switching.
  const currentDetail =
    detail && detail.player.id === playerId ? detail : null;
  const currentError = error && errorFor === playerId;

  const loadEpoch = useRef(0);

  const load = useCallback(async () => {
    if (!playerId) return;
    setError(false);
    const epoch = ++loadEpoch.current;
    try {
      let res = await fetch(`/api/player/${encodeURIComponent(playerId)}`);
      // A 404 can mean the player is known but their profile pages have not
      // been fetched yet: the read-only backend records the gap and the
      // scraper worker fills it within seconds - retry before erroring.
      for (let attempt = 0; attempt < 2 && res.status === 404; attempt++) {
        await new Promise((r) => setTimeout(r, 3000));
        if (epoch !== loadEpoch.current) return; // player changed meanwhile
        res = await fetch(`/api/player/${encodeURIComponent(playerId)}`);
      }
      if (!res.ok) throw new Error("failed");
      if (epoch !== loadEpoch.current) return;
      const data: PlayerDetail = await res.json();
      setDetail(data);
      // Stub row (name from lineups, no bio/career yet): the worker is
      // fetching the profile right now - quietly re-fetch once so the
      // career history appears without reopening the dialog.
      if (!data.profileFetched) {
        await new Promise((r) => setTimeout(r, 6000));
        if (epoch !== loadEpoch.current) return;
        const again = await fetch(`/api/player/${encodeURIComponent(playerId)}`);
        if (again.ok && epoch === loadEpoch.current) {
          setDetail(await again.json());
        }
      }
    } catch {
      if (epoch === loadEpoch.current) {
        setError(true);
        setErrorFor(playerId);
      }
    }
  }, [playerId]);

  // data effect: seed from SSR detail when it belongs to this player, else fetch
  useEffect(() => {
    if (!player) {
      loadEpoch.current++; // stop any in-flight retry loop
      return;
    }
    if (initialDetail && initialDetail.player.id === player.id) {
      setDetail(initialDetail);
      return;
    }
    setDetail(null);
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [player, load, initialDetail]);

  // open-state effect: skipped when the parent controls the dialog
  useEffect(() => {
    if (openOverride !== undefined) return;
    setOpen(!!player);
  }, [openOverride, player]);

  const isOpen = openOverride !== undefined ? openOverride : open;

  // the SSR page client passes the seed through a ref-less prop chain; the
  // header can render from the passed `player` even before the detail lands
  const bio = currentDetail?.player || null;

  return (
    <Dialog open={isOpen} onOpenChange={(v) => !v && onClose()}>
      <DialogContent
        aria-describedby={undefined}
        dir={lang === "ar" ? "rtl" : "ltr"}
        showCloseButton={false}
        overlayClassName={elevated ? "z-[55]" : undefined}
        className={`max-h-[92dvh] grid-cols-[minmax(0,1fr)] gap-0 overflow-y-auto rounded-lg border border-[#b9c8dd] p-0 sm:max-w-2xl${elevated ? " z-[60]" : ""}`}
      >
        {player && (
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

              <DialogTitle className="flex items-center gap-3 pe-10 leading-snug">
                <Crest url={bio?.imageUrl} size={44} className="rounded-full bg-white/10" />
                <span className="flex min-w-0 flex-col gap-1">
                  <span className="truncate text-[15px] font-bold">
                    {nameOf(bio || player, lang)}
                  </span>
                  <span className="flex flex-wrap items-center gap-1.5">
                    {positionLabel(bio?.position, lang) && (
                      <span className="rounded bg-white/15 px-1.5 py-0.5 text-[11px] font-semibold">
                        {positionLabel(bio?.position, lang)}
                      </span>
                    )}
                    {bio?.shirtNumber != null && (
                      <span className="rounded bg-white/15 px-1.5 py-0.5 text-[11px] font-semibold tabular-nums">
                        #{bio.shirtNumber}
                      </span>
                    )}
                  </span>
                </span>
              </DialogTitle>

              {/* current club chip - clickable, opens the team dialog */}
              {currentDetail?.currentClub && (
                <button
                  type="button"
                  onClick={() => currentDetail.currentClub && onOpenTeam(currentDetail.currentClub)}
                  className="mt-2.5 flex max-w-full items-center gap-1.5 rounded-full bg-white/15 px-2 py-1 text-[11.5px] font-semibold transition-colors hover:bg-white/25 focus:outline-none"
                >
                  <Crest url={currentDetail.currentClub.crestUrl} size={15} />
                  <span className="truncate">
                    {nameOf(currentDetail.currentClub, lang)}
                  </span>
                </button>
              )}
            </div>

            {/* body: loading/error show IN PLACE - never the previous player's
                content (currentDetail is keyed to the player id) */}
            <div className="p-3 sm:p-4" aria-busy={!currentDetail && !currentError}>
              {!currentDetail && !currentError && (
                <div className="flex items-center justify-center gap-2 py-10 text-[#5b6b80]">
                  <Loader2 className="h-5 w-5 animate-spin" />
                  <span className="text-sm">{s.loading}</span>
                </div>
              )}

              {currentError && !currentDetail && (
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
                <div className="space-y-4">
                  <BioGrid detail={currentDetail} lang={lang} />
                  <CareerTable detail={currentDetail} lang={lang} />
                </div>
              )}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// bio grid
// ---------------------------------------------------------------------------
function BioGrid({ detail, lang }: { detail: PlayerDetail; lang: Lang }) {
  const s = t(lang);
  const p = detail.player;

  const nationality =
    lang === "ar" ? p.nationalityAr || p.nationalityEn : p.nationalityEn || p.nationalityAr;
  const birthPlace =
    lang === "ar" ? p.placeOfBirthAr || p.placeOfBirthEn : p.placeOfBirthEn || p.placeOfBirthAr;
  const birthDate = birthDateLabel(p.birthDate, lang);

  const cells: { label: string; value: string | null }[] = [
    { label: s.currentClub, value: detail.currentClub ? nameOf(detail.currentClub, lang) : null },
    { label: s.position, value: positionLabel(p.position, lang) },
    {
      label: s.age,
      value: p.age != null ? `${p.age} ${s.yearsOld}` : null,
    },
    { label: s.birthDate, value: birthDate },
    { label: s.nationality, value: nationality },
    { label: s.birthPlace, value: birthPlace },
    { label: s.height, value: heightLabel(p.heightCm, lang) },
    { label: s.weight, value: weightLabel(p.weightKg, lang) },
  ];
  const filled = cells.filter((c) => c.value);
  if (filled.length === 0) return null;

  return (
    <div className="overflow-hidden rounded-md border border-[#dbe4ef] bg-white">
      <div className="grid grid-cols-2 gap-px bg-[#e2e9f2] sm:grid-cols-3">
        {filled.map((c) => (
          <div key={c.label} className="bg-white px-3 py-2">
            <p className="text-[10.5px] font-semibold uppercase tracking-wide text-[#7d8ea3]">
              {c.label}
            </p>
            <p className="mt-0.5 truncate text-[12.5px] font-semibold text-[#1c2b3a]" title={c.value ?? undefined}>
              {c.value}
            </p>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// career table
// ---------------------------------------------------------------------------
function CareerTable({ detail, lang }: { detail: PlayerDetail; lang: Lang }) {
  const s = t(lang);

  if (detail.career.length === 0) {
    return (
      <div className="rounded-md border border-[#dbe4ef] bg-[#f6f9fd] p-4 text-center">
        <ShieldQuestion className="mx-auto mb-1.5 h-5 w-5 text-[#93a1b3]" />
        <p className="text-sm text-[#7d8ea3]">{s.noCareer}</p>
      </div>
    );
  }

  return (
    <div className="overflow-hidden rounded-md border border-[#dbe4ef] bg-white">
      <div className="border-b border-[#dbe4ef] bg-[#eef3fa] px-3 py-1.5 text-[12px] font-bold text-[#17457f]">
        {s.career}
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[420px] border-collapse text-[12px]">
          <thead>
            <tr className="bg-[#f6f9fd] text-[10.5px] font-bold text-[#4a5a70]">
              <th className="px-2 py-1.5 text-start">{s.season}</th>
              <th className="px-2 py-1.5 text-start">{s.clubCol}</th>
              <th className="w-10 px-1 py-1.5 text-center">{s.appsCol}</th>
              <th className="w-10 px-1 py-1.5 text-center">{s.goalsCol}</th>
              <th className="hidden w-10 px-1 py-1.5 text-center sm:table-cell">{s.assistsCol}</th>
              <th className="hidden w-16 px-1 py-1.5 text-center md:table-cell">{s.minutesCol}</th>
            </tr>
          </thead>
          <tbody>
            {detail.career.map((e, i) => {
              const compName =
                lang === "ar"
                  ? e.competition.nameAr || e.competition.nameEn
                  : e.competition.nameEn || e.competition.nameAr;
              return (
                <tr
                  key={i}
                  className={`border-t border-[#e2e9f2] ${i % 2 === 1 ? "bg-[#f6f9fd]" : "bg-white"}`}
                >
                  <td className="whitespace-nowrap px-2 py-1.5 font-semibold tabular-nums text-[#33455e]">
                    {e.seasonName || "—"}
                  </td>
                  <td className="px-2 py-1.5">
                    <span className="flex min-w-0 items-center gap-1.5" title={compName || undefined}>
                      <Crest url={e.team.crestUrl} size={16} />
                      <span className="truncate font-semibold text-[#1c2b3a]">
                        {nameOf(e.team, lang)}
                      </span>
                      {e.isLoan && (
                        <span className="shrink-0 rounded bg-[#fdf3e0] px-1 text-[9.5px] font-bold text-[#9a6b13]">
                          {s.loan}
                        </span>
                      )}
                    </span>
                  </td>
                  <td className="px-1 py-1.5 text-center tabular-nums text-[#33455e]">
                    {e.appearances ?? "—"}
                  </td>
                  <td className="px-1 py-1.5 text-center font-bold tabular-nums text-[#14263a]">
                    {e.goals ?? "—"}
                  </td>
                  <td className="hidden px-1 py-1.5 text-center tabular-nums text-[#33455e] sm:table-cell">
                    {e.assists ?? "—"}
                  </td>
                  <td className="hidden px-1 py-1.5 text-center tabular-nums text-[#5b6b80] md:table-cell">
                    {e.minutesPlayed ?? "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
