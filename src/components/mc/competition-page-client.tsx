"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { CalendarDays, Loader2, Trophy } from "lucide-react";
import type {
  CompetitionInfo,
  CompetitionMatchesResponse,
  CompetitionRef,
  GamesetRef,
  Lang,
  MatchRow,
  StandingRow,
  StandingsMarker,
  StandingsTable,
  TeamRef,
} from "@/lib/goal/types";
import { compLabel, nameOf, statusDisplay, t } from "@/lib/i18n";
import {
  competitionDescription,
  competitionTitle,
  compUrlPair,
  matchTitle,
  matchDescription,
  matchUrlFor,
  playerDescription,
  playerTitle,
  playerUrlFor,
  teamDescription,
  teamTitle,
  teamUrlFor,
} from "@/lib/seo";
import { MatchDialog } from "./match-dialog";
import { TeamDialog } from "./team-dialog";
import { PlayerDialog, type PlayerDialogTarget } from "./player-dialog";
import { Crest } from "./crest";

interface CompetitionPageClientProps {
  competitionId: string;
  /** SSR-fetched info (standings + rounds); null when the backend was slow */
  initialInfo: CompetitionInfo | null;
  /** SSR-fetched matches of the active round (may be null) */
  initialRound: CompetitionMatchesResponse | null;
  initialLang: Lang;
}

/**
 * Competition page: standings table + every round's matches.
 *
 * The standings and the active round are SERVER-rendered (crawler food - a
 * real table with team names and a list of match links pointing at every
 * /match/<id>/<slug> page in the page's language). Switching rounds and
 * opening matches are client-side enhancements on top.
 */
export function CompetitionPageClient({
  competitionId,
  initialInfo,
  initialRound,
  initialLang,
}: CompetitionPageClientProps) {
  const router = useRouter();
  const [lang, setLang] = useState<Lang>(initialLang);
  const [info, setInfo] = useState<CompetitionInfo | null>(initialInfo);
  const [failed, setFailed] = useState(false);
  const [round, setRound] = useState<CompetitionMatchesResponse | null>(initialRound);
  const [roundLoading, setRoundLoading] = useState(false);
  const [roundError, setRoundError] = useState(false);
  const [dialogMatch, setDialogMatch] = useState<MatchRow | null>(null);
  // true while the browser URL points at a /match/<id> page pushed from this
  // page (closing the dialog pops back with history.back())
  const pushedMatchUrl = useRef(false);
  // a team opened from the standings rows / the match dialog (dialog + URL push)
  const [dialogTeam, setDialogTeam] = useState<TeamRef | null>(null);
  const pushedTeamUrl = useRef(false);
  // a player opened from lineups / the team dialog's squad (dialog + URL push)
  const [dialogPlayer, setDialogPlayer] = useState<PlayerDialogTarget | null>(null);
  const pushedPlayerUrl = useRef(false);
  // which dialog sits ON TOP when both the team and player dialogs are open
  // ("team" = team opened from the player's club chip, "player" = player
  // opened from the team's squad list); null = no stacking
  const [topDialog, setTopDialog] = useState<"team" | "player" | null>(null);

  const s = t(lang);
  const rtl = lang === "ar";

  // keep <html lang/dir> + document.title in sync (SSR already rendered the
  // correct values for the URL's language - the URL itself carries it)
  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
    if (info) document.title = competitionTitle({ competition: info.competition, seasonName: info.season?.name ?? null }, lang);
  }, [lang, info]);

  // client-side fallback when SSR could not get the info (slow backend)
  useEffect(() => {
    if (info) return;
    let alive = true;
    fetch(`/api/competition/${encodeURIComponent(competitionId)}`)
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error("failed"))))
      .then((json: CompetitionInfo) => {
        if (alive) setInfo(json);
      })
      .catch(() => {
        if (alive) setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [info, competitionId]);

  // when the info arrives late (client fallback), load its active round too
  useEffect(() => {
    if (!info || initialRound || round) return;
    const gameset = activeGameset(info);
    if (!gameset) return;
    selectRound(gameset);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [info]);

  /** fetch one round's matches (client-side round switcher) */
  const selectRound = useCallback(
    async (g: GamesetRef) => {
      setRoundLoading(true);
      setRoundError(false);
      try {
        const qs = g.gameSetTypeId
          ? `?gameset=${encodeURIComponent(g.gameSetTypeId)}`
          : "";
        const res = await fetch(
          `/api/competition/${encodeURIComponent(competitionId)}/matches${qs}`,
        );
        if (!res.ok) throw new Error("failed");
        const json: CompetitionMatchesResponse = await res.json();
        setRound(json);
      } catch {
        setRoundError(true);
      } finally {
        setRoundLoading(false);
      }
    },
    [competitionId],
  );

  /** apply the match page's SEO meta while its URL is showing */
  const applyMatchMeta = useCallback((m: MatchRow, lang: Lang) => {
    document.title = matchTitle(m, lang);
    document
      .querySelector('meta[name="description"]')
      ?.setAttribute("content", matchDescription(m, lang));
  }, []);

  /** apply the team page's SEO meta while its URL is showing */
  const applyTeamMeta = useCallback((team: TeamRef, lang: Lang) => {
    document.title = teamTitle(team, lang);
    document
      .querySelector('meta[name="description"]')
      ?.setAttribute("content", teamDescription(team, lang));
  }, []);

  /** apply the player page's SEO meta while its URL is showing */
  const applyPlayerMeta = useCallback((p: PlayerDialogTarget, lang: Lang) => {
    document.title = playerTitle(p, lang);
    document
      .querySelector('meta[name="description"]')
      ?.setAttribute("content", playerDescription({ player: p }, lang));
  }, []);

  /** restore this competition page's SEO meta */
  const restoreCompMeta = useCallback(
    (lang: Lang) => {
      if (!info) return;
      document.title = competitionTitle(
        { competition: info.competition, seasonName: info.season?.name ?? null },
        lang,
      );
      document
        .querySelector('meta[name="description"]')
        ?.setAttribute(
          "content",
          competitionDescription(
            { competition: info.competition, seasonName: info.season?.name ?? null },
            lang,
          ),
        );
    },
    [info],
  );

  /** open a match from the round list / the team dialog: dialog + slug URL +
   *  match meta. When the team dialog is open on top, the clicked match TAKES
   *  ITS PLACE: the team dialog closes and its URL entry is REPLACED (same
   *  history depth, no async back()/push() dance). */
  const openMatch = useCallback(
    (m: MatchRow) => {
      const fromTeamDialog = !!dialogTeam;
      if (fromTeamDialog) {
        setDialogTeam(null);
        setTopDialog((top) => (top === "team" ? null : top));
        // the /team/<id> entry is consumed by the replaceState below
        pushedTeamUrl.current = false;
      }
      setDialogMatch(m);
      try {
        const url = matchUrlFor(m.matchId, m, lang);
        if (fromTeamDialog) {
          window.history.replaceState({ mcMatch: m.matchId }, "", url);
        } else {
          window.history.pushState({ mcMatch: m.matchId }, "", url);
        }
        pushedMatchUrl.current = true;
      } catch {
        /* history unavailable - the dialog still opens, just no URL change */
      }
      applyMatchMeta(m, lang);
    },
    [lang, applyMatchMeta, dialogTeam],
  );

  const closeMatch = useCallback(() => {
    setDialogMatch(null);
    // whatever dialog is still open underneath gets ITS meta restored
    if (dialogPlayer) {
      applyPlayerMeta(dialogPlayer, lang);
    } else if (dialogTeam) {
      applyTeamMeta(dialogTeam, lang);
    } else {
      restoreCompMeta(lang);
    }
    if (pushedMatchUrl.current) {
      pushedMatchUrl.current = false;
      try {
        window.history.back();
      } catch {
        /* ignore */
      }
    }
  }, [lang, restoreCompMeta, dialogPlayer, dialogTeam, applyPlayerMeta, applyTeamMeta]);

  /** open a team from the standings rows / the match dialog header / the
   *  player dialog's club chip: dialog + slug URL + team meta.
   *  When opened FROM the player dialog, the team dialog TAKES THE PLAYER'S
   *  PLACE (replaceState, same as a match clicked inside the team dialog):
   *  re-elevating an already-open dialog breaks Radix's Escape layer order
   *  (one Escape would close both at once). */
  const openTeam = useCallback(
    (team: TeamRef) => {
      if (!team?.id) return;
      const fromPlayerDialog = !!dialogPlayer;
      if (fromPlayerDialog) {
        setDialogPlayer(null);
        setTopDialog(null);
        // the /player/<id> entry is consumed by the replaceState below
        pushedPlayerUrl.current = false;
      }
      setDialogTeam(team);
      try {
        const url = teamUrlFor(team.id, team, lang);
        if (fromPlayerDialog) {
          window.history.replaceState({ mcTeam: team.id }, "", url);
        } else {
          window.history.pushState({ mcTeam: team.id }, "", url);
        }
        pushedTeamUrl.current = true;
      } catch {
        /* history unavailable - the dialog still opens */
      }
      applyTeamMeta(team, lang);
    },
    [applyTeamMeta, lang, dialogPlayer],
  );

  const closeTeam = useCallback(() => {
    setDialogTeam(null);
    setTopDialog((top) => (top === "team" ? null : top));
    // restore the meta of whatever is open underneath
    if (dialogPlayer) {
      applyPlayerMeta(dialogPlayer, lang);
    } else if (dialogMatch) {
      applyMatchMeta(dialogMatch, lang);
    } else {
      restoreCompMeta(lang);
    }
    if (pushedTeamUrl.current) {
      pushedTeamUrl.current = false;
      try {
        window.history.back();
      } catch {
        /* ignore */
      }
    }
  }, [lang, restoreCompMeta, dialogPlayer, dialogMatch, applyPlayerMeta, applyMatchMeta]);

  /** open a player (lineups / the team dialog's squad): dialog + slug URL +
   *  player meta */
  const openPlayer = useCallback(
    (p: PlayerDialogTarget) => {
      if (!p?.id) return;
      setDialogPlayer(p);
      // player opened FROM the team dialog -> player stacks on top
      if (dialogTeam) setTopDialog("player");
      try {
        window.history.pushState({ mcPlayer: p.id }, "", playerUrlFor(p.id, p, lang));
        pushedPlayerUrl.current = true;
      } catch {
        /* history unavailable - the dialog still opens */
      }
      applyPlayerMeta(p, lang);
    },
    [applyPlayerMeta, lang, dialogTeam],
  );

  const closePlayer = useCallback(() => {
    setDialogPlayer(null);
    setTopDialog((top) => (top === "player" ? null : top));
    // restore the meta of whatever is open underneath
    if (dialogTeam) {
      applyTeamMeta(dialogTeam, lang);
    } else if (dialogMatch) {
      applyMatchMeta(dialogMatch, lang);
    } else {
      restoreCompMeta(lang);
    }
    if (pushedPlayerUrl.current) {
      pushedPlayerUrl.current = false;
      try {
        window.history.back();
      } catch {
        /* ignore */
      }
    }
  }, [lang, restoreCompMeta, dialogTeam, dialogMatch, applyTeamMeta, applyMatchMeta]);

  // browser BACK from a pushed /match, /team or /player URL: close the
  // dialog + restore (each dialog only closes when the URL left ITS path)
  useEffect(() => {
    const onPopState = () => {
      const path = window.location.pathname;
      if (!path.startsWith("/match/") && pushedMatchUrl.current) {
        pushedMatchUrl.current = false;
        setDialogMatch(null);
        restoreCompMeta(lang);
      }
      if (!path.startsWith("/team/") && pushedTeamUrl.current) {
        pushedTeamUrl.current = false;
        setDialogTeam(null);
        setTopDialog((top) => (top === "team" ? null : top));
        restoreCompMeta(lang);
      }
      if (!path.startsWith("/player/") && pushedPlayerUrl.current) {
        pushedPlayerUrl.current = false;
        setDialogPlayer(null);
        setTopDialog((top) => (top === "player" ? null : top));
        restoreCompMeta(lang);
      }
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [lang, restoreCompMeta]);

  /**
   * Switch language: the URL follows (each language has its own slug URL),
   * plus every crawler-facing tag that names it.
   */
  const switchLang = (next: Lang) => {
    setLang(next);
    if (!info) return;
    const pair = compUrlPair(competitionId, info.competition);
    const path = next === "en" ? pair.en : pair.ar;
    try {
      window.history.replaceState({ mcCompetition: competitionId }, "", path);
    } catch {
      /* history unavailable - content still switches */
    }
    document.title = competitionTitle(
      { competition: info.competition, seasonName: info.season?.name ?? null },
      next,
    );
    document
      .querySelector('meta[name="description"]')
      ?.setAttribute(
        "content",
        competitionDescription(
          { competition: info.competition, seasonName: info.season?.name ?? null },
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

  const comp: CompetitionRef | null = info?.competition ?? null;
  const standings = info?.standings ?? null;
  const gamesets = info?.gamesets ?? [];
  const activeGs = round?.gameset ?? activeGameset(info);

  const gamesetLabel = useCallback(
    (g: GamesetRef | null) => (g ? nameOf(g, lang) : null),
    [lang],
  );

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

      {/* ======= main: standings + rounds ======= */}
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
          <div className="space-y-3">
            {/* competition header */}
            <div className="overflow-hidden rounded-md border border-[#c3cedd] bg-white shadow-sm">
              <div className="bg-gradient-to-b from-[#1d4f92] to-[#123a70] px-4 py-3 text-white">
                <h1 className="flex items-center gap-2 text-[16px] font-extrabold">
                  <Trophy className="h-5 w-5" />
                  {compLabel(comp, lang)}
                </h1>
                {info.season?.name && (
                  <p className="mt-1 flex items-center gap-1.5 text-[12px] text-white/75">
                    <CalendarDays className="h-3.5 w-3.5" />
                    {s.seasonLabel}: {info.season.name}
                  </p>
                )}
              </div>
            </div>

            {/* standings table (server-rendered, crawler-visible; team names
                are real links to the team pages - crawler food) */}
            {standings && standings.tables.length > 0 ? (
              <StandingsSection
                tables={standings.tables}
                markers={standings.markers}
                lang={lang}
                onOpenTeam={openTeam}
              />
            ) : (
              <div className="rounded-md border border-[#c3cedd] bg-white px-4 py-8 text-center shadow-sm">
                <p className="text-[13px] font-semibold text-[#7d8ea3]">{s.noStandings}</p>
              </div>
            )}

            {/* round switcher + matches */}
            <section className="overflow-hidden rounded-md border border-[#c3cedd] bg-white shadow-sm">
              <div className="flex flex-wrap items-center gap-2 border-b border-[#c3cedd] bg-gradient-to-b from-[#e8eff9] to-[#d3e1f2] px-3 py-2">
                <h2 className="text-[13px] font-bold text-[#17457f]">{s.roundsTab}</h2>
                {gamesets.length > 0 && (
                  <div className="ms-auto flex max-w-full flex-wrap items-center gap-1">
                    {gamesets.map((g) => {
                      const isActive = activeGs?.gameSetTypeId === g.gameSetTypeId;
                      return (
                        <button
                          key={g.gameSetTypeId}
                          type="button"
                          onClick={() => selectRound(g)}
                          disabled={roundLoading}
                          className={`rounded border px-2 py-0.5 text-[11.5px] font-semibold transition-colors ${
                            isActive
                              ? "border-[#17457f] bg-[#17457f] text-white"
                              : "border-[#b9c8dd] bg-white text-[#33455e] hover:bg-[#e8f1fb]"
                          }`}
                        >
                          {nameOf(g, lang)}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>

              <div className="p-2 sm:p-3">
                {roundLoading && (
                  <div className="flex items-center justify-center gap-2 py-6 text-[13px] font-semibold text-[#5b6b80]">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    {s.loading}
                  </div>
                )}

                {!roundLoading && roundError && (
                  <p className="py-6 text-center text-[13px] font-semibold text-[#b3392f]">
                    {s.loadError}
                  </p>
                )}

                {!roundLoading && !roundError && (!round || round.matches.length === 0) && (
                  <p className="py-6 text-center text-[13px] text-[#7d8ea3]">
                    {s.noMatchesInRound}
                  </p>
                )}

                {!roundLoading && !roundError && round && round.matches.length > 0 && (
                  <div role="list" className="space-y-1">
                    {round.matches.map((m) => (
                      <RoundMatchRow
                        key={m.matchId}
                        m={m}
                        lang={lang}
                        onOpen={openMatch}
                      />
                    ))}
                  </div>
                )}
              </div>
            </section>

            <button
              type="button"
              onClick={() => router.push("/")}
              className="w-full rounded border border-[#b9c8dd] bg-white px-3 py-2 text-[12.5px] font-semibold text-[#33455e] transition-colors hover:bg-[#e8f1fb]"
            >
              ← {s.todayTitle}
            </button>
          </div>
        )}
      </main>

      {/* ======= footer ======= */}
      <footer className="mt-auto border-t border-[#c3cedd] bg-white/80 py-2.5 backdrop-blur">
        <div className="mx-auto flex w-full max-w-4xl flex-wrap items-center justify-center gap-x-3 gap-y-1 px-3 text-[11px] text-[#7d8ea3]">
          <span>{s.footer}</span>
        </div>
      </footer>

      {/* ======= match dialog (opened from a round match) ======= */}
      {dialogMatch && (
        <MatchDialog
          match={dialogMatch}
          lang={lang}
          onClose={closeMatch}
          onOpenTeam={openTeam}
          onOpenPlayer={openPlayer}
        />
      )}

      {/* ======= team dialog (opened from the standings / a match) ======= */}
      <TeamDialog
        team={dialogTeam}
        lang={lang}
        onClose={closeTeam}
        onOpenMatch={openMatch}
        onOpenPlayer={openPlayer}
        elevated={topDialog === "team"}
      />

      {/* ======= player dialog (opened from lineups / the squad) ======= */}
      <PlayerDialog
        player={dialogPlayer}
        lang={lang}
        onClose={closePlayer}
        onOpenTeam={openTeam}
        elevated={topDialog === "player"}
      />
    </div>
  );
}

/** the round shown by default: the active one, else the first */
function activeGameset(info: CompetitionInfo | null): GamesetRef | null {
  if (!info || info.gamesets.length === 0) return null;
  return info.gamesets.find((g) => g.isActive) || info.gamesets[0];
}

/** Zone colors, consistent with the competition dialog. */
function markerColor(marker: StandingsMarker | undefined): string | null {
  if (!marker) return null;
  if (marker.type === "RELEGATION") return "#b3392f";
  if (marker.type === "PROMOTION") return "#1d7a1d";
  return "#1d4f92";
}

/** Standings table - server-rendered on first paint, real crawler content.
 *  Team names are real <a href> links to the team pages (internal links for
 *  crawlers), intercepted on click to open the team dialog. */
function StandingsSection({
  tables,
  markers,
  lang,
  onOpenTeam,
}: {
  tables: StandingsTable[];
  markers: StandingsMarker[];
  lang: Lang;
  onOpenTeam: (team: TeamRef) => void;
}) {
  const s = t(lang);
  const markerById = useMemo(
    () => Object.fromEntries(markers.map((m) => [m.id, m])),
    [markers],
  );

  return (
    <section className="overflow-hidden rounded-md border border-[#c3cedd] bg-white shadow-sm">
      <div className="border-b border-[#c3cedd] bg-gradient-to-b from-[#e8eff9] to-[#d3e1f2] px-3 py-2">
        <h2 className="flex items-center gap-2 text-[13px] font-bold text-[#17457f]">
          <Trophy className="h-4 w-4" />
          {s.tableTab}
        </h2>
      </div>
      <div className="space-y-4 p-2 sm:p-3">
        {tables.map((table, ti) => (
          <div key={ti}>
            {table.name && tables.length > 1 && (
              <p className="mb-1.5 text-[12px] font-bold text-[#33455e]">{table.name}</p>
            )}
            <div className="overflow-x-auto">
              <table className="w-full min-w-[340px] border-collapse text-[12.5px]">
                <thead>
                  <tr className="border-b border-[#c3cedd] text-[11px] font-semibold text-[#5b6b80]">
                    <th className="px-1.5 py-1.5 text-center">{s.posCol}</th>
                    <th className="px-1.5 py-1.5 text-start">{lang === "ar" ? "الفريق" : "Team"}</th>
                    <th className="px-1.5 py-1.5 text-center">{s.playedCol}</th>
                    <th className="px-1.5 py-1.5 text-center">{s.winCol}</th>
                    <th className="px-1.5 py-1.5 text-center">{s.drawCol}</th>
                    <th className="px-1.5 py-1.5 text-center">{s.loseCol}</th>
                    <th className="px-1.5 py-1.5 text-center">{s.gdCol}</th>
                    <th className="px-1.5 py-1.5 text-center">{s.pointsCol}</th>
                  </tr>
                </thead>
                <tbody>
                  {table.rows.map((r) => (
                    <StandingTeamRow
                      key={r.team.id || r.position}
                      r={r}
                      zone={markerById[r.markers[0]]}
                      lang={lang}
                      onOpenTeam={onOpenTeam}
                    />
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
        {/* zone legend */}
        {markers.length > 0 && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-1 text-[11px] text-[#5b6b80]">
            {markers.map((m) => (
              <span key={m.id} className="flex items-center gap-1.5">
                <span
                  aria-hidden="true"
                  style={{ backgroundColor: markerColor(m) ?? "#93a1b3" }}
                  className="inline-block h-2.5 w-1 rounded-sm"
                />
                {nameOf(m, lang)}
              </span>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}

/**
 * One standings row. The team cell is a real <a href> to the team's own page
 * in the CURRENT language (internal link for crawlers), intercepted on click
 * to open the team dialog + push the URL - the same treatment round matches
 * get.
 */
function StandingTeamRow({
  r,
  zone,
  lang,
  onOpenTeam,
}: {
  r: StandingRow;
  zone: StandingsMarker | undefined;
  lang: Lang;
  onOpenTeam: (team: TeamRef) => void;
}) {
  const zoneColor = markerColor(zone);
  const teamHref = r.team.id ? teamUrlFor(r.team.id, r.team, lang) : undefined;

  return (
    <tr className="border-b border-[#e2e9f2] last:border-b-0 hover:bg-[#f6f9fd]">
      <td className="px-1.5 py-1.5 text-center">
        <span className="inline-flex items-center justify-center gap-1">
          {zoneColor && (
            <span
              aria-hidden="true"
              title={zone ? nameOf(zone, lang) : undefined}
              style={{ backgroundColor: zoneColor }}
              className="inline-block h-2.5 w-1 rounded-sm"
            />
          )}
          <span className="font-bold tabular-nums text-[#33455e]">{r.position}</span>
        </span>
      </td>
      <td className="px-1.5 py-1.5">
        <a
          href={teamHref}
          onClick={(e) => {
            if (!r.team.id) return; // no id -> nothing to open, follow nothing
            e.preventDefault();
            onOpenTeam(r.team);
          }}
          className="flex items-center gap-1.5 transition-colors hover:text-[#17457f]"
        >
          <Crest url={r.team.crestUrl} size={18} />
          <span className="font-semibold">{nameOf(r.team, lang)}</span>
        </a>
      </td>
      <td className="px-1.5 py-1.5 text-center tabular-nums">{r.played ?? "-"}</td>
      <td className="px-1.5 py-1.5 text-center tabular-nums">{r.win ?? "-"}</td>
      <td className="px-1.5 py-1.5 text-center tabular-nums">{r.draw ?? "-"}</td>
      <td className="px-1.5 py-1.5 text-center tabular-nums">{r.lose ?? "-"}</td>
      <td className="px-1.5 py-1.5 text-center tabular-nums">{r.goalDiff ?? "-"}</td>
      <td className="px-1.5 py-1.5 text-center font-extrabold tabular-nums text-[#17457f]">
        {r.points ?? "-"}
      </td>
    </tr>
  );
}

/**
 * One match of the shown round: a real <a href> to the match's own page in
 * the CURRENT language (internal link for crawlers), intercepted on click to
 * open the match dialog + push the URL.
 */
function RoundMatchRow({
  m,
  lang,
  onOpen,
}: {
  m: MatchRow;
  lang: Lang;
  onOpen: (m: MatchRow) => void;
}) {
  const s = t(lang);
  const st = statusDisplay(m, lang);
  const hasScore = m.homeScore !== null && m.awayScore !== null;

  return (
    <a
      href={matchUrlFor(m.matchId, m, lang)}
      role="listitem"
      onClick={(e) => {
        e.preventDefault();
        onOpen(m);
      }}
      className="flex items-center gap-2 rounded border border-[#dbe4ef] bg-[#f6f9fd] px-2.5 py-2 transition-colors hover:bg-[#e8f1fb]"
    >
      <span
        className={`w-[52px] shrink-0 text-[11.5px] font-bold tabular-nums ${
          st.kind === "live" ? "text-[#d31f26]" : "text-[#5b6b80]"
        }`}
      >
        {st.main}
      </span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-[13px] font-semibold text-[#1c2b3a]">
          {nameOf(m.homeTeam, lang)} {s.vs} {nameOf(m.awayTeam, lang)}
        </span>
        {m.roundName && (
          <span className="block truncate text-[11px] text-[#7d8ea3]">{m.roundName}</span>
        )}
      </span>
      {hasScore ? (
        <span className="shrink-0 text-[14px] font-extrabold tabular-nums text-[#17457f]">
          {m.homeScore} - {m.awayScore}
        </span>
      ) : (
        <span className="shrink-0 text-[13px] font-semibold text-[#a5b1c0]">-</span>
      )}
    </a>
  );
}
