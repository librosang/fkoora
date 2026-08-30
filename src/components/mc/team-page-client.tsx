"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";
import type {
  Lang,
  MatchRow,
  SquadPlayer,
  TeamInfo,
} from "@/lib/goal/types";
import { nameOf, t } from "@/lib/i18n";
import {
  matchDescription,
  matchTitle,
  matchUrlFor,
  playerDescription,
  playerTitle,
  playerUrlFor,
  teamDescription,
  teamTitle,
  teamUrlFor,
  teamUrlPair,
} from "@/lib/seo";
import { TeamDialog } from "./team-dialog";
import { PlayerDialog, type PlayerDialogTarget } from "./player-dialog";
import { MatchDialog } from "./match-dialog";
import { Crest } from "./crest";

interface TeamPageClientProps {
  teamId: string;
  /** SSR-fetched team info; null when the backend was slow/unreachable */
  initialInfo: TeamInfo | null;
  initialLang: Lang;
}

/**
 * Team page: server-rendered team header + fixtures/results/squad summary
 * (crawler food), with the full team dialog (all matches + squad) as a
 * client-side enhancement. Mirrors the match/competition pages.
 */
export function TeamPageClient({
  teamId,
  initialInfo,
  initialLang,
}: TeamPageClientProps) {
  const router = useRouter();
  const [lang, setLang] = useState<Lang>(initialLang);
  const [info, setInfo] = useState<TeamInfo | null>(initialInfo);
  const [failed, setFailed] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  // a match opened from within this team's data (dialog + URL push)
  const [dialogMatch, setDialogMatch] = useState<MatchRow | null>(null);
  const pushedMatchUrl = useRef(false);
  // a player opened from the squad list
  const [dialogPlayer, setDialogPlayer] = useState<PlayerDialogTarget | null>(null);
  const pushedPlayerUrl = useRef(false);

  const s = t(lang);
  const rtl = lang === "ar";

  // keep <html lang/dir> + document.title in sync (SSR already rendered the
  // correct values for the URL's language - the URL itself carries it)
  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
    if (info) document.title = teamTitle(info.team, lang);
  }, [lang, info]);

  // client-side fallback when SSR could not get the info (slow backend)
  useEffect(() => {
    if (info) return;
    let alive = true;
    fetch(`/api/team/${encodeURIComponent(teamId)}`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error("failed"))))
      .then((json: TeamInfo) => {
        if (alive) setInfo(json);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [info, teamId]);

  /** apply the match page's SEO meta while its URL is showing */
  const applyMatchMeta = useCallback((m: MatchRow, lang: Lang) => {
    document.title = matchTitle(m, lang);
    document
      .querySelector('meta[name="description"]')
      ?.setAttribute("content", matchDescription(m, lang));
  }, []);

  /** restore this team page's SEO meta */
  const restoreTeamMeta = useCallback(
    (lang: Lang) => {
      if (!info) return;
      document.title = teamTitle(info.team, lang);
      document
        .querySelector('meta[name="description"]')
        ?.setAttribute("content", teamDescription(info.team, lang));
    },
    [info],
  );

  /** open a match from the team's list: dialog + slug URL + match meta */
  const openMatch = useCallback(
    (m: MatchRow) => {
      setDialogMatch(m);
      try {
        window.history.pushState(
          { mcMatch: m.matchId },
          "",
          matchUrlFor(m.matchId, m, lang),
        );
        pushedMatchUrl.current = true;
      } catch {
        /* history unavailable - the dialog still opens */
      }
      applyMatchMeta(m, lang);
    },
    [lang, applyMatchMeta],
  );

  const closeMatch = useCallback(() => {
    setDialogMatch(null);
    restoreTeamMeta(lang);
    if (pushedMatchUrl.current) {
      pushedMatchUrl.current = false;
      try {
        window.history.back();
      } catch {
        /* ignore */
      }
    }
  }, [lang, restoreTeamMeta]);

  /** open a player from the squad: dialog + slug URL + player meta. Accepts
   *  the plain PlayerDialogTarget so the match dialog (lineup entries) can
   *  reuse it; SquadPlayer rows satisfy it structurally. */
  const openPlayer = useCallback(
    (p: PlayerDialogTarget) => {
      setDialogPlayer(p);
      try {
        window.history.pushState(
          { mcPlayer: p.id },
          "",
          playerUrlFor(p.id, p, lang),
        );
        pushedPlayerUrl.current = true;
      } catch {
        /* history unavailable - the dialog still opens */
      }
      document.title = playerTitle(p, lang);
      document
        .querySelector('meta[name="description"]')
        ?.setAttribute("content", playerDescription({ player: p }, lang));
    },
    [lang],
  );

  const closePlayer = useCallback(() => {
    setDialogPlayer(null);
    restoreTeamMeta(lang);
    if (pushedPlayerUrl.current) {
      pushedPlayerUrl.current = false;
      try {
        window.history.back();
      } catch {
        /* ignore */
      }
    }
  }, [lang, restoreTeamMeta]);

  // browser BACK from a pushed /match or /player URL: close the dialog
  useEffect(() => {
    const onPopState = () => {
      const path = window.location.pathname;
      if (!path.startsWith("/match/") && pushedMatchUrl.current) {
        pushedMatchUrl.current = false;
        setDialogMatch(null);
        restoreTeamMeta(lang);
      }
      if (!path.startsWith("/player/") && pushedPlayerUrl.current) {
        pushedPlayerUrl.current = false;
        setDialogPlayer(null);
        restoreTeamMeta(lang);
      }
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [lang, restoreTeamMeta]);

  /**
   * Switch language: the URL follows (each language has its own slug URL),
   * plus every crawler-facing tag that names it.
   */
  const switchLang = (next: Lang) => {
    setLang(next);
    if (!info) return;
    const pair = teamUrlPair(teamId, info.team);
    const path = next === "en" ? pair.en : pair.ar;
    try {
      window.history.replaceState({ mcTeam: teamId }, "", path);
    } catch {
      /* history unavailable - content still switches */
    }
    document.title = teamTitle(info.team, next);
    document
      .querySelector('meta[name="description"]')
      ?.setAttribute("content", teamDescription(info.team, next));
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

  const header = (
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
  );

  return (
    <div
      dir={rtl ? "rtl" : "ltr"}
      lang={lang}
      className="font-app flex min-h-screen flex-col bg-[#e9edf2] text-[#1c2b3a]"
    >
      {header}

      {/* ======= main: SEO-visible team summary ======= */}
      <main className="mx-auto w-full max-w-4xl flex-1 px-2 py-3 sm:px-3">
        {!info && !failed && (
          <div className="flex items-center justify-center gap-2 rounded-md border border-[#c3cedd] bg-white px-4 py-12 shadow-sm">
            <Loader2 className="h-5 w-5 animate-spin text-[#17457f]" />
            <span className="text-sm font-semibold text-[#5b6b80]">{s.loading}</span>
          </div>
        )}

        {failed && !info && (
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

        {info && (
          <TeamSummaryCard
            info={info}
            lang={lang}
            onOpenDetails={() => setDialogOpen(true)}
            onOpenMatch={openMatch}
            onOpenPlayer={openPlayer}
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

      {/* ======= full team dialog (all matches + squad) ======= */}
      {info && (
        <TeamDialog
          team={info.team}
          lang={lang}
          onClose={() => setDialogOpen(false)}
          onOpenMatch={openMatch}
          onOpenPlayer={openPlayer}
          initialInfo={info}
          openOverride={dialogOpen}
        />
      )}

      {/* ======= match dialog (opened from a team match) ======= */}
      {dialogMatch && (
        <MatchDialog
          match={dialogMatch}
          lang={lang}
          onClose={closeMatch}
          onOpenTeam={(team) => {
            // the clicked team (often the opponent) gets its own team page
            router.push(teamUrlFor(team.id, team, lang));
          }}
          onOpenPlayer={openPlayer}
        />
      )}

      {/* ======= player dialog (opened from the squad) ======= */}
      {dialogPlayer && (
        <PlayerDialog
          player={dialogPlayer}
          lang={lang}
          onClose={closePlayer}
          onOpenTeam={(team) => {
            // the player's club gets its own team page
            if (team.id) router.push(teamUrlFor(team.id, team, lang));
          }}
          elevated
        />
      )}
    </div>
  );
}

/**
 * Server-rendered team summary: header + next fixtures + recent results +
 * squad list as real content for crawlers; the button opens the full dialog.
 */
function TeamSummaryCard({
  info,
  lang,
  onOpenDetails,
  onOpenMatch,
  onOpenPlayer,
  onBack,
}: {
  info: TeamInfo;
  lang: Lang;
  onOpenDetails: () => void;
  onOpenMatch: (m: MatchRow) => void;
  onOpenPlayer: (p: SquadPlayer) => void;
  onBack: () => void;
}) {
  const s = t(lang);
  const upcoming = info.upcomingMatches ?? [];
  const recent = info.recentMatches ?? [];

  return (
    <article className="overflow-hidden rounded-md border border-[#c3cedd] bg-white shadow-sm">
      {/* header */}
      <div className="bg-gradient-to-b from-[#1d4f92] to-[#123a70] px-4 pb-4 pt-4 text-white">
        <h1 className="flex items-center gap-2.5 text-[16px] font-extrabold">
          <Crest url={info.team.crestUrl} size={34} />
          {nameOf(info.team, lang)}
          {info.team.code && (
            <span className="rounded bg-white/15 px-1.5 py-0.5 text-[11px] font-semibold">
              {info.team.code}
            </span>
          )}
        </h1>
      </div>

      <div className="space-y-4 p-3 sm:p-4">
        {/* upcoming fixtures (crawler-visible links to match pages) */}
        {upcoming.length > 0 && (
          <section>
            <h2 className="mb-1.5 text-[13px] font-bold text-[#17457f]">{s.upcomingFixtures}</h2>
            <ul className="space-y-1">
              {upcoming.slice(0, 5).map((m) => (
                <TeamPageMatchLine key={m.matchId} m={m} lang={lang} onOpen={onOpenMatch} />
              ))}
            </ul>
          </section>
        )}

        {/* recent results */}
        {recent.length > 0 && (
          <section>
            <h2 className="mb-1.5 text-[13px] font-bold text-[#17457f]">{s.recentResults}</h2>
            <ul className="space-y-1">
              {recent.slice(0, 5).map((m) => (
                <TeamPageMatchLine key={m.matchId} m={m} lang={lang} onOpen={onOpenMatch} />
              ))}
            </ul>
          </section>
        )}

        {/* squad (crawler-visible links to player pages) */}
        {info.squad.length > 0 && (
          <section>
            <h2 className="mb-1.5 text-[13px] font-bold text-[#17457f]">{s.squadTab}</h2>
            <div className="flex flex-wrap gap-1.5">
              {info.squad.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => onOpenPlayer(p)}
                  title={s.playerInfo}
                  className="flex items-center gap-1.5 rounded-full border border-[#c3cedd] bg-[#f6f9fd] px-2.5 py-1 text-[12px] font-semibold text-[#1c2b3a] transition-colors hover:border-[#17457f] hover:text-[#17457f]"
                >
                  {p.shirtNumber != null && (
                    <span className="tabular-nums text-[#4a6b96]">{p.shirtNumber}</span>
                  )}
                  {nameOf(p, lang)}
                </button>
              ))}
            </div>
          </section>
        )}

        {upcoming.length === 0 && recent.length === 0 && info.squad.length === 0 && (
          <p className="py-4 text-center text-[13px] text-[#7d8ea3]">{s.noTeamMatches}</p>
        )}

        <button
          type="button"
          onClick={onOpenDetails}
          className="flex w-full items-center justify-center gap-1.5 rounded border border-[#17457f] bg-[#17457f] px-3 py-2 text-[13px] font-semibold text-white transition-colors hover:bg-[#123a70]"
        >
          {s.matchesTab} · {s.squadTab}
        </button>

        <button
          type="button"
          onClick={onBack}
          className="w-full rounded border border-[#b9c8dd] bg-white px-3 py-2 text-[12.5px] font-semibold text-[#33455e] transition-colors hover:bg-[#e8f1fb]"
        >
          ← {s.todayTitle}
        </button>
      </div>
    </article>
  );
}

/** One match line of the team page summary: a real <a href> to the match page
 *  (internal link for crawlers), intercepted on click to open the dialog. */
function TeamPageMatchLine({
  m,
  lang,
  onOpen,
}: {
  m: MatchRow;
  lang: Lang;
  onOpen: (m: MatchRow) => void;
}) {
  const s = t(lang);
  const hasScore = m.homeScore !== null && m.awayScore !== null;
  return (
    <li>
      <a
        href={matchUrlFor(m.matchId, m, lang)}
        onClick={(e) => {
          e.preventDefault();
          onOpen(m);
        }}
        className="flex items-center gap-2 rounded border border-[#dbe4ef] bg-[#f6f9fd] px-2.5 py-1.5 text-[13px] transition-colors hover:bg-[#e8f1fb]"
      >
        <span className="min-w-0 flex-1 truncate font-semibold text-[#1c2b3a]">
          {nameOf(m.homeTeam, lang)} {s.vs} {nameOf(m.awayTeam, lang)}
        </span>
        {hasScore ? (
          <span className="shrink-0 text-[13.5px] font-extrabold tabular-nums text-[#17457f]">
            {m.homeScore} - {m.awayScore}
          </span>
        ) : (
          <span className="shrink-0 text-[12px] font-semibold text-[#7d8ea3]">
            {matchShortTime(m.kickoffUtc, lang)}
          </span>
        )}
      </a>
    </li>
  );
}

function matchShortTime(iso: string | null, lang: Lang): string {
  if (!iso) return "--:--";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "--:--";
  return new Intl.DateTimeFormat(lang === "ar" ? "ar-MA-u-nu-latn" : "en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
}
