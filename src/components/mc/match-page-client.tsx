"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2, Trophy } from "lucide-react";
import type { Lang, MatchDetail, MatchRow } from "@/lib/goal/types";
import { compLabel, formatDateTime, nameOf, t } from "@/lib/i18n";
import {
  matchDescription,
  matchTitle,
  matchUrlPair,
} from "@/lib/seo";
import { MatchDialog } from "./match-dialog";
import { TeamDialog } from "./team-dialog";
import { PlayerDialog } from "./player-dialog";
import { Crest } from "./crest";

/** Build the MatchRow the dialog needs from a MatchDetail payload. */
function rowFromDetail(d: MatchDetail): MatchRow {
  return {
    matchId: d.matchId,
    kickoffUtc: d.kickoffUtc,
    status: d.status,
    period: d.period ?? null,
    homeTeam: d.homeTeam,
    awayTeam: d.awayTeam,
    competition: d.competition,
    homeScore: d.homeScore,
    awayScore: d.awayScore,
    homeRedCards: 0,
    awayRedCards: 0,
    roundName: d.roundName ?? null,
    venueNameEn: d.venueNameEn ?? null,
    venueNameAr: d.venueNameAr ?? null,
  };
}

interface MatchPageClientProps {
  matchId: string;
  /** SSR-fetched detail; null when the backend was slow/unreachable */
  initialDetail: MatchDetail | null;
  initialLang: Lang;
}

export function MatchPageClient({
  matchId,
  initialDetail,
  initialLang,
}: MatchPageClientProps) {
  const router = useRouter();
  const [lang, setLang] = useState<Lang>(initialLang);
  const [detail, setDetail] = useState<MatchDetail | null>(initialDetail);
  const [failed, setFailed] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  // team / player drill-downs (match summary header, dialog lineups/events)
  const [teamId, setTeamId] = useState<string | null>(null);
  const [playerId, setPlayerId] = useState<string | null>(null);
  // a stored match opened from the team/player dialogs (full MatchDialog)
  const [dialogMatch, setDialogMatch] = useState<MatchRow | null>(null);

  // keep <html lang/dir> + document.title in sync (SSR already rendered the
  // correct values for the URL's language - the URL itself carries it)
  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
    if (detail) document.title = matchTitle(detail, lang);
  }, [lang, detail]);

  /**
   * Switch language: the URL itself must follow (each language has its own
   * slug URL), plus every crawler-facing tag that names it. A replaceState
   * (not push) - switching language is not a history entry.
   */
  const switchLang = (next: Lang) => {
    setLang(next);
    if (!detail) return;
    const pair = matchUrlPair(matchId, detail);
    const path = next === "en" ? pair.en : pair.ar;
    try {
      window.history.replaceState({ mcMatch: matchId }, "", path);
    } catch {
      /* history unavailable - content still switches */
    }
    // sync the crawler-facing tags to the new language's URL/wording
    document.title = matchTitle(detail, next);
    document
      .querySelector('meta[name="description"]')
      ?.setAttribute("content", matchDescription(detail, next));
    document
      .querySelector('link[rel="canonical"]')
      ?.setAttribute("href", path);
    document
      .querySelector('meta[property="og:url"]')
      ?.setAttribute("content", path);
    document
      .querySelector('meta[property="og:title"]')
      ?.setAttribute("content", matchTitle(detail, next));
    document.querySelectorAll('link[rel="alternate"]').forEach((el) => {
      const hl = el.getAttribute("hreflang");
      if (hl === "ar") el.setAttribute("href", pair.ar);
      else if (hl === "en") el.setAttribute("href", pair.en);
      else if (hl === "x-default") el.setAttribute("href", pair.ar);
    });
  };

  // client-side fallback when SSR could not get the detail (slow backend) -
  // same endpoint the dialog uses, so the retry logic is identical
  useEffect(() => {
    if (detail) return;
    let alive = true;
    fetch(`/api/match/${encodeURIComponent(matchId)}`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error("failed"))))
      .then((json: MatchDetail) => {
        if (alive) setDetail(json);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [detail, matchId]);

  const row = useMemo(() => (detail ? rowFromDetail(detail) : null), [detail]);
  const rtl = lang === "ar";
  const strings = t(lang);

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
            title={strings.appTitle}
            className="flex items-center gap-2"
          >
            <span className="flex h-9 w-9 items-center justify-center rounded-full border-2 border-white/60 bg-white/10">
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.6">
                <circle cx="12" cy="12" r="9.5" />
                <path d="M12 8.2l3.6 2.6-1.4 4.2H9.8L8.4 10.8 12 8.2z" fill="currentColor" stroke="none" />
              </svg>
            </span>
            <span className="leading-tight">
              <span className="block text-[17px] font-extrabold tracking-wide">{strings.appTitle}</span>
              <span className="block text-[11px] text-white/70">{strings.appSubtitle}</span>
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

      {/* ======= main: SEO-visible match summary ======= */}
      <main className="mx-auto w-full max-w-4xl flex-1 px-2 py-3 sm:px-3">
        {!detail && !failed && (
          <div className="flex items-center justify-center gap-2 rounded-md border border-[#c3cedd] bg-white px-4 py-12 shadow-sm">
            <Loader2 className="h-5 w-5 animate-spin text-[#17457f]" />
            <span className="text-sm font-semibold text-[#5b6b80]">{strings.loading}</span>
          </div>
        )}

        {failed && !detail && (
          <div className="flex flex-col items-center gap-3 rounded-md border border-[#e5b6b2] bg-[#fdf1f0] px-4 py-12 shadow-sm">
            <p className="text-sm font-semibold text-[#b3392f]">{strings.loadError}</p>
            <button
              type="button"
              onClick={() => router.push("/")}
              className="flex items-center gap-1.5 rounded border border-[#17457f] bg-[#17457f] px-3 py-1.5 text-[13px] font-semibold text-white hover:bg-[#123a70]"
            >
              {strings.todayTitle}
            </button>
          </div>
        )}

        {detail && (
          <MatchSummaryCard
            detail={detail}
            lang={lang}
            onOpenDetails={() => setDialogOpen(true)}
            onBack={() => router.push("/")}
            onOpenTeam={setTeamId}
          />
        )}
      </main>

      {/* ======= footer ======= */}
      <footer className="mt-auto border-t border-[#c3cedd] bg-white/80 py-2.5 backdrop-blur">
        <div className="mx-auto flex w-full max-w-4xl flex-wrap items-center justify-center gap-x-3 gap-y-1 px-3 text-[11px] text-[#7d8ea3]">
          <span>{strings.footer}</span>
        </div>
      </footer>

      {/* ======= full detail dialog (events / lineups / stats) ======= */}
      {row && (
        <MatchDialog
          match={row}
          lang={lang}
          initialDetail={detail}
          openOverride={dialogOpen}
          onClose={() => setDialogOpen(false)}
          onOpenTeam={setTeamId}
          onOpenPlayer={setPlayerId}
        />
      )}

      {/* ======= team + player drill-down dialogs ======= */}
      <TeamDialog
        teamId={teamId}
        lang={lang}
        onClose={() => setTeamId(null)}
        onOpenMatch={(m) => {
          setDialogOpen(false);
          setDialogMatch(m);
        }}
      />
      <PlayerDialog
        playerId={playerId}
        lang={lang}
        onClose={() => setPlayerId(null)}
        onOpenTeam={setTeamId}
        onOpenMatch={(m) => {
          setDialogOpen(false);
          setDialogMatch(m);
        }}
      />
    </div>
  );
}

/**
 * Server-rendered (client component, but SSR'd on first paint) match summary:
 * real content for crawlers + a landing view for direct visitors; the button
 * opens the full events/lineups/stats dialog.
 */
function MatchSummaryCard({
  detail,
  lang,
  onOpenDetails,
  onBack,
  onOpenTeam,
}: {
  detail: MatchDetail;
  lang: Lang;
  onOpenDetails: () => void;
  onBack: () => void;
  onOpenTeam?: (teamId: string) => void;
}) {
  const s = t(lang);
  const hasScore =
    detail.homeScore !== null && detail.awayScore !== null && detail.status !== "FIXTURE";
  const venue =
    lang === "ar"
      ? detail.venueNameAr || detail.venueNameEn
      : detail.venueNameEn || detail.venueNameAr;
  const when = formatDateTime(detail.kickoffUtc, lang);

  const statusLabel =
    detail.status === "LIVE"
      ? detail.period || s.liveNow
      : detail.status === "RESULT"
        ? s.ftLabel
        : detail.status === "AET"
          ? s.aetShort
          : detail.status === "PEN"
            ? s.pensShort
            : detail.status === "CANCELLED"
              ? s.cancelled
              : detail.status === "POSTPONED"
                ? s.postponed
                : s.notStarted;

  // goals (incl. own goals + penalties) as plain text - crawler food
  const goalEvents = detail.events.filter((ev) =>
    [
      "GOAL",
      "GOAL_OWN",
      "GOAL_PENALTY",
      "GOAL_PENALTY_SHOOTOUT",
    ].includes(ev.eventType),
  );

  return (
    <article className="overflow-hidden rounded-md border border-[#c3cedd] bg-white shadow-sm">
      {/* header */}
      <div className="bg-gradient-to-b from-[#1d4f92] to-[#123a70] px-4 pb-4 pt-4 text-white">
        <p className="flex items-center gap-2 text-[13px] font-semibold">
          <Trophy className="h-4 w-4" />
          {compLabel(detail.competition, lang)}
          {detail.roundName && (
            <span className="rounded bg-white/15 px-1.5 py-0.5 text-[11px] font-medium">
              {detail.roundName}
            </span>
          )}
        </p>

        <h1 className="mt-3 grid grid-cols-[1fr_auto_1fr] items-center gap-2 text-[15px] font-extrabold leading-snug">
          <button
            type="button"
            onClick={() => detail.homeTeam.id && onOpenTeam?.(detail.homeTeam.id)}
            disabled={!onOpenTeam}
            className={`flex flex-col items-center gap-1 rounded-md p-1 transition-colors ${onOpenTeam ? "hover:bg-white/15" : ""}`}
          >
            <Crest url={detail.homeTeam.crestUrl} size={38} />
            <span className="text-center underline-offset-2 hover:underline">{nameOf(detail.homeTeam, lang)}</span>
          </button>
          <span className="flex flex-col items-center gap-1">
            {hasScore ? (
              <span className="flex items-center gap-2 text-3xl tabular-nums tracking-wider">
                <span>{detail.homeScore}</span>
                <span className="text-2xl font-bold text-white/60">-</span>
                <span>{detail.awayScore}</span>
              </span>
            ) : (
              <span className="text-lg text-white/80">{s.vs}</span>
            )}
            <span className="rounded-full bg-white/15 px-2 py-0.5 text-[11px] font-semibold">
              {detail.status === "LIVE" && (
                <span className="me-1 inline-block h-2 w-2 animate-pulse rounded-full bg-[#ff6b6b] align-middle" />
              )}
              {statusLabel}
            </span>
          </span>
          <button
            type="button"
            onClick={() => detail.awayTeam.id && onOpenTeam?.(detail.awayTeam.id)}
            disabled={!onOpenTeam}
            className={`flex flex-col items-center gap-1 rounded-md p-1 transition-colors ${onOpenTeam ? "hover:bg-white/15" : ""}`}
          >
            <Crest url={detail.awayTeam.crestUrl} size={38} />
            <span className="text-center underline-offset-2 hover:underline">{nameOf(detail.awayTeam, lang)}</span>
          </button>
        </h1>

        {(when || venue || detail.referee) && (
          <p className="mt-3 flex flex-wrap items-center justify-center gap-x-4 gap-y-1 border-t border-white/20 pt-2 text-[11.5px] text-white/80">
            {when && <span>{when}</span>}
            {venue && (
              <span>
                {s.venue}: {venue}
              </span>
            )}
            {detail.referee && (
              <span>
                {s.referee}: {detail.referee}
              </span>
            )}
          </p>
        )}
      </div>

      {/* body: goals timeline + CTA to the full dialog */}
      <div className="p-3 sm:p-4">
        {goalEvents.length > 0 ? (
          <>
            <h2 className="mb-2 text-[13px] font-bold text-[#17457f]">{s.goal}</h2>
            <ul className="space-y-1.5">
              {goalEvents.map((ev, i) => (
                <li
                  key={i}
                  className="flex items-center gap-2 rounded border border-[#dbe4ef] bg-[#f6f9fd] px-2.5 py-1.5 text-[13px]"
                >
                  <span className="w-10 shrink-0 text-end font-bold tabular-nums text-[#4a6b96]">
                    {ev.minute ?? "—"}'
                  </span>
                  <span className="font-semibold text-[#1c2b3a]">
                    {nameOf(ev.player, lang)}
                    {ev.eventType === "GOAL_OWN" && (
                      <span className="ms-1 text-[11px] text-[#5b6b80]">({s.ownGoal})</span>
                    )}
                    {ev.eventType === "GOAL_PENALTY" && (
                      <span className="ms-1 text-[11px] text-[#5b6b80]">({s.pens})</span>
                    )}
                  </span>
                  <span className="ms-auto shrink-0 text-[11px] font-semibold text-[#5b6b80]">
                    {ev.teamSide === "home"
                      ? nameOf(detail.homeTeam, lang)
                      : nameOf(detail.awayTeam, lang)}
                    {ev.homeScoreAfter !== null && ev.homeScoreAfter !== undefined && (
                      <span className="ms-1 font-extrabold tabular-nums text-[#17457f]">
                        {ev.homeScoreAfter}-{ev.awayScoreAfter}
                      </span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <p className="py-4 text-center text-[13px] text-[#7d8ea3]">
            {detail.status === "FIXTURE" ? s.notStarted : s.noStats}
          </p>
        )}

        <button
          type="button"
          onClick={onOpenDetails}
          className="mt-4 flex w-full items-center justify-center gap-1.5 rounded border border-[#17457f] bg-[#17457f] px-3 py-2 text-[13px] font-semibold text-white transition-colors hover:bg-[#123a70]"
        >
          {s.events} · {s.lineups} · {s.stats}
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
