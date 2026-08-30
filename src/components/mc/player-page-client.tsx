"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import type { Lang, PlayerDetail, TeamRef } from "@/lib/goal/types";
import {
  birthDateLabel,
  heightLabel,
  nameOf,
  positionLabel,
  t,
  weightLabel,
} from "@/lib/i18n";
import {
  playerDescription,
  playerTitle,
  playerUrlPair,
  teamUrlFor,
} from "@/lib/seo";
import { PlayerDialog } from "./player-dialog";
import { Crest } from "./crest";

interface PlayerPageClientProps {
  playerId: string;
  /** SSR-fetched detail; null when the backend was slow/unreachable */
  initialDetail: PlayerDetail | null;
  initialLang: Lang;
}

/**
 * Player page: server-rendered bio + career history (crawler food), with the
 * full player dialog as a client-side enhancement. Mirrors the match and
 * competition pages.
 */
export function PlayerPageClient({
  playerId,
  initialDetail,
  initialLang,
}: PlayerPageClientProps) {
  const router = useRouter();
  const [lang, setLang] = useState<Lang>(initialLang);
  const [detail, setDetail] = useState<PlayerDetail | null>(initialDetail);
  const [failed, setFailed] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);

  const s = t(lang);
  const rtl = lang === "ar";

  // keep <html lang/dir> + document.title in sync
  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
    if (detail) {
      document.title = playerTitle(detail.player, lang);
    }
  }, [lang, detail]);

  // client-side fallback when SSR could not get the detail (slow backend)
  useEffect(() => {
    if (detail) return;
    let alive = true;
    fetch(`/api/player/${encodeURIComponent(playerId)}`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error("failed"))))
      .then((json: PlayerDetail) => {
        if (alive) setDetail(json);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [detail, playerId]);

  /** the player's club gets its own team page (hard navigation) */
  const openTeam = (team: TeamRef) => {
    if (team.id) router.push(teamUrlFor(team.id, team, lang));
  };

  /**
   * Switch language: the URL follows (each language has its own slug URL),
   * plus every crawler-facing tag that names it.
   */
  const switchLang = (next: Lang) => {
    setLang(next);
    if (!detail) return;
    const pair = playerUrlPair(playerId, detail.player);
    const path = next === "en" ? pair.en : pair.ar;
    try {
      window.history.replaceState({ mcPlayer: playerId }, "", path);
    } catch {
      /* history unavailable - content still switches */
    }
    document.title = playerTitle(detail.player, next);
    document
      .querySelector('meta[name="description"]')
      ?.setAttribute(
        "content",
        playerDescription(
          { player: detail.player, clubName: clubNameOf(detail.currentClub, next) },
          next,
        ),
      );
    document
      .querySelector('link[rel="canonical"]')
      ?.setAttribute("href", path);
    document
      .querySelector('meta[property="og:url"]')
      ?.setAttribute("content", path);
    document.querySelectorAll('link[rel="alternate"]').forEach((el) => {
      const hl = el.getAttribute("hreflang");
      if (hl === "ar") el.setAttribute("href", pair.ar);
      else if (hl === "en") el.setAttribute("href", pair.en);
      else if (hl === "x-default") el.setAttribute("href", pair.ar);
    });
  };

  return (
    <div
      dir={rtl ? "rtl" : "ltr"}
      lang={lang}
      className="font-app flex min-h-screen flex-col bg-[#e9edf2] text-[#1c2b3a]"
    >
      {/* ======= header (brand + home link + language toggle) ======= */}
      <header className="bg-gradient-to-b from-[#1d4f92] to-[#123a70] text-white shadow-md">
        <div className="mx-auto flex w-full max-w-4xl items-center gap-3 px-3 py-3">
          <button
            type="button"
            onClick={() => router.push("/")}
            title={s.appTitle}
            className="flex items-center gap-2"
          >
            <span className="flex h-9 w-9 items-center justify-center rounded-full border-2 border-white/60 bg-white/10">
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.6">
                <circle cx="12" cy="12" r="9.5" />
                <path d="M12 8.2l3.6 2.6-1.4 4.2H9.8L8.4 10.8 12 8.2z" fill="currentColor" stroke="none" />
              </svg>
            </span>
            <span className="leading-tight">
              <span className="block text-[17px] font-extrabold tracking-wide">{s.appTitle}</span>
              <span className="block text-[11px] text-white/70">{s.appSubtitle}</span>
            </span>
          </button>

          <div className="ms-auto flex items-center gap-2">
            <div className="flex overflow-hidden rounded border border-white/40" role="group" aria-label="Language">
              <button
                type="button"
                onClick={() => switchLang("ar")}
                className={`px-3 py-1 text-[12px] font-bold transition-colors ${
                  lang === "ar" ? "bg-white text-[#17457f]" : "text-white/80 hover:bg-white/10"
                }`}
              >
                عربي
              </button>
              <button
                type="button"
                onClick={() => switchLang("en")}
                className={`px-3 py-1 text-[12px] font-bold transition-colors ${
                  lang === "en" ? "bg-white text-[#17457f]" : "text-white/80 hover:bg-white/10"
                }`}
              >
                EN
              </button>
            </div>
          </div>
        </div>
      </header>

      {/* ======= main: SEO-visible player summary ======= */}
      <main className="mx-auto w-full max-w-4xl flex-1 px-2 py-3 sm:px-3">
        {!detail && !failed && (
          <div className="flex items-center justify-center gap-2 rounded-md border border-[#c3cedd] bg-white px-4 py-12 shadow-sm">
            <Loader2 className="h-5 w-5 animate-spin text-[#17457f]" />
            <span className="text-sm font-semibold text-[#5b6b80]">{s.loading}</span>
          </div>
        )}

        {failed && !detail && (
          <div className="flex flex-col items-center gap-3 rounded-md border border-[#e5b6b2] bg-[#fdf1f0] px-4 py-12 shadow-sm">
            <p className="text-sm font-semibold text-[#b3392f]">{s.loadError}</p>
            <button
              type="button"
              onClick={() => router.push("/")}
              className="flex items-center gap-1.5 rounded border border-[#17457f] bg-[#17457f] px-3 py-1.5 text-[13px] font-semibold text-white hover:bg-[#123a70]"
            >
              {s.todayTitle}
            </button>
          </div>
        )}

        {detail && (
          <PlayerSummaryCard
            detail={detail}
            lang={lang}
            onOpenDetails={() => setDialogOpen(true)}
            onOpenTeam={openTeam}
            onBack={() => router.push("/")}
          />
        )}
      </main>

      {/* ======= footer ======= */}
      <footer className="mt-auto border-t border-[#c3cedd] bg-white/80 py-2.5 backdrop-blur">
        <div className="mx-auto flex w-full max-w-4xl flex-wrap items-center justify-center gap-x-3 gap-y-1 px-3 text-[11px] text-[#7d8ea3]">
          <span>{s.footer}</span>
        </div>
      </footer>

      {/* ======= full player dialog (bio + career) ======= */}
      {detail && (
        <PlayerDialog
          player={detail.player}
          lang={lang}
          onClose={() => setDialogOpen(false)}
          onOpenTeam={openTeam}
          initialDetail={detail}
          openOverride={dialogOpen}
        />
      )}
    </div>
  );
}

function clubNameOf(club: TeamRef | null, lang: Lang): string | null {
  if (!club) return null;
  return nameOf(club, lang) || null;
}

/**
 * Server-rendered (client component, but SSR'd on first paint) player
 * summary: bio + career table as real content for crawlers; the button opens
 * the full player dialog.
 */
function PlayerSummaryCard({
  detail,
  lang,
  onOpenDetails,
  onOpenTeam,
  onBack,
}: {
  detail: PlayerDetail;
  lang: Lang;
  onOpenDetails: () => void;
  onOpenTeam: (team: TeamRef) => void;
  onBack: () => void;
}) {
  const s = t(lang);
  const p = detail.player;
  const nationality =
    lang === "ar" ? p.nationalityAr || p.nationalityEn : p.nationalityEn || p.nationalityAr;
  const birthPlace =
    lang === "ar" ? p.placeOfBirthAr || p.placeOfBirthEn : p.placeOfBirthEn || p.placeOfBirthAr;
  const birthDate = birthDateLabel(p.birthDate, lang);

  const cells: { label: string; value: string | null }[] = [
    { label: s.position, value: positionLabel(p.position, lang) },
    { label: s.age, value: p.age != null ? `${p.age} ${s.yearsOld}` : null },
    { label: s.birthDate, value: birthDate },
    { label: s.nationality, value: nationality },
    { label: s.birthPlace, value: birthPlace },
    { label: s.height, value: heightLabel(p.heightCm, lang) },
    { label: s.weight, value: weightLabel(p.weightKg, lang) },
    { label: s.shirtNumber, value: p.shirtNumber != null ? `#${p.shirtNumber}` : null },
  ];
  const filled = cells.filter((c) => c.value);

  return (
    <article className="overflow-hidden rounded-md border border-[#c3cedd] bg-white shadow-sm">
      {/* header */}
      <div className="bg-gradient-to-b from-[#1d4f92] to-[#123a70] px-4 pb-4 pt-4 text-white">
        <h1 className="flex items-center gap-3 leading-snug">
          <Crest url={p.imageUrl} size={44} className="rounded-full bg-white/10" />
          <span className="flex min-w-0 flex-col gap-1">
            <span className="truncate text-[16px] font-extrabold">{nameOf(p, lang)}</span>
            <span className="flex flex-wrap items-center gap-1.5">
              {positionLabel(p.position, lang) && (
                <span className="rounded bg-white/15 px-1.5 py-0.5 text-[11px] font-semibold">
                  {positionLabel(p.position, lang)}
                </span>
              )}
              {p.shirtNumber != null && (
                <span className="rounded bg-white/15 px-1.5 py-0.5 text-[11px] font-semibold tabular-nums">
                  #{p.shirtNumber}
                </span>
              )}
            </span>
          </span>
        </h1>

        {/* current club - internal link to the team page (crawler-visible) */}
        {detail.currentClub && detail.currentClub.id && (
          <button
            type="button"
            onClick={() => onOpenTeam(detail.currentClub!)}
            className="mt-2.5 flex max-w-full items-center gap-1.5 rounded-full bg-white/15 px-2 py-1 text-[11.5px] font-semibold transition-colors hover:bg-white/25"
          >
            <Crest url={detail.currentClub.crestUrl} size={15} />
            <span className="truncate">{nameOf(detail.currentClub, lang)}</span>
          </button>
        )}
      </div>

      {/* body: bio grid + career table */}
      <div className="p-3 sm:p-4">
        {filled.length > 0 && (
          <div className="mb-4 grid grid-cols-2 gap-px overflow-hidden rounded-md border border-[#dbe4ef] bg-[#e2e9f2] sm:grid-cols-4">
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
        )}

        {detail.career.length > 0 ? (
          <>
            <h2 className="mb-2 text-[13px] font-bold text-[#17457f]">{s.career}</h2>
            <div className="overflow-x-auto rounded-md border border-[#dbe4ef]">
              <table className="w-full min-w-[420px] border-collapse text-[12px]">
                <thead>
                  <tr className="bg-[#eef3fa] text-[10.5px] font-bold text-[#4a5a70]">
                    <th className="px-2 py-1.5 text-start">{s.season}</th>
                    <th className="px-2 py-1.5 text-start">{s.clubCol}</th>
                    <th className="w-10 px-1 py-1.5 text-center">{s.appsCol}</th>
                    <th className="w-10 px-1 py-1.5 text-center">{s.goalsCol}</th>
                    <th className="hidden w-10 px-1 py-1.5 text-center sm:table-cell">{s.assistsCol}</th>
                    <th className="hidden w-16 px-1 py-1.5 text-center md:table-cell">{s.minutesCol}</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.career.map((e, i) => (
                    <tr
                      key={i}
                      className={`border-t border-[#e2e9f2] ${i % 2 === 1 ? "bg-[#f6f9fd]" : "bg-white"}`}
                    >
                      <td className="whitespace-nowrap px-2 py-1.5 font-semibold tabular-nums text-[#33455e]">
                        {e.seasonName || "—"}
                      </td>
                      <td className="px-2 py-1.5">
                        <span className="flex min-w-0 items-center gap-1.5">
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
                  ))}
                </tbody>
              </table>
            </div>
          </>
        ) : (
          <p className="py-4 text-center text-[13px] text-[#7d8ea3]">{s.noCareer}</p>
        )}

        <button
          type="button"
          onClick={onOpenDetails}
          className="mt-4 flex w-full items-center justify-center gap-1.5 rounded border border-[#17457f] bg-[#17457f] px-3 py-2 text-[13px] font-semibold text-white transition-colors hover:bg-[#123a70]"
        >
          {s.playerInfo}
        </button>

        <button
          type="button"
          onClick={onBack}
          className="mt-2 w-full rounded border border-[#b9c8dd] bg-white px-3 py-2 text-[12.5px] font-semibold text-[#33455e] transition-colors hover:bg-[#e8f1fb]"
        >
          ← {s.todayTitle}
        </button>
      </div>
    </article>
  );
}
