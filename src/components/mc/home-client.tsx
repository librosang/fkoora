"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, Search } from "lucide-react";
import type {
  CompetitionRef,
  Lang,
  ListingResponse,
  MatchRow,
  TeamRef,
} from "@/lib/goal/types";
import { localToday, t } from "@/lib/i18n";
import {
  competitionDescription,
  competitionTitle,
  compUrlFor,
  matchDescription,
  matchTitle,
  matchUrlFor,
  pageDescription,
  pageTitle,
  playerDescription,
  playerTitle,
  playerUrlFor,
  teamDescription,
  teamTitle,
  teamUrlFor,
} from "@/lib/seo";
import { DateNav } from "@/components/mc/date-nav";
import { MatchList } from "@/components/mc/match-list";
import { MatchDialog } from "@/components/mc/match-dialog";
import { CompetitionDialog } from "@/components/mc/competition-dialog";
import { TeamDialog } from "@/components/mc/team-dialog";
import { PlayerDialog, type PlayerDialogTarget } from "@/components/mc/player-dialog";
import { LiveMatchStream, applyMatchDelta, type LiveStreamStatus } from "@/lib/goal/sse";
import type { MatchUpdatedDelta, LiveEvent } from "@/lib/goal/types";

// ---------------------------------------------------------------------------
// auto-refresh cadence: the listing polls at 60s ONLY while live matches are
// running (or one has just kicked off); otherwise it idles at 30 minutes -
// but always refreshes right after the next kickoff so the app flips to
// live mode the moment a match starts.
// ---------------------------------------------------------------------------
/** live matches running (or one that just kicked off) -> poll every minute */
const LIVE_POLL_MS = 60_000;
/** nothing live -> poll at most every 30 minutes */
const IDLE_POLL_MS = 30 * 60_000;
/** after a kickoff, wait a bit before refreshing (scrape latency on the backend) */
const KICKOFF_BUFFER_MS = 90_000;
/** a FIXTURE whose kickoff passed this recently is "about to go live" (kickoff
 * delays + backend scrape lag) - keep the fast cadence until it flips */
const KICKOFF_WATCH_MS = 15 * 60_000;
/** retry cadence after a failed load - the app heals itself (no button) */
const ERROR_RETRY_MS = 30_000;
/** while the SSE stream is delivering live updates the periodic HTTP poll is
 *  only a safety net: 5 minutes instead of every minute */
const SSE_SAFETY_POLL_MS = 5 * 60_000;

interface HomeClientProps {
  /** day listing fetched on the server (SEO) - null when the backend was slow/unreachable */
  initialData: ListingResponse | null;
  /** the date this HTML was rendered for (from ?date= or server-side "today") */
  initialDate: string;
  /** true when the URL explicitly carried ?date= (keep that date), false when it was defaulted */
  initialDateFromUrl: boolean;
  /** initial UI language (?lang=en or ar default) */
  initialLang: Lang;
  /** initial "major competitions only" filter (?major=0 -> false) */
  initialMajor: boolean;
}

export function HomeClient({
  initialData,
  initialDate,
  initialDateFromUrl,
  initialLang,
  initialMajor,
}: HomeClientProps) {
  const [lang, setLang] = useState<Lang>(initialLang);
  const [today, setToday] = useState<string | null>(null);
  const [date, setDate] = useState<string | null>(initialDate);
  const [major, setMajor] = useState(initialMajor);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<MatchRow | null>(null);
  const [compSelected, setCompSelected] = useState<CompetitionRef | null>(null);
  const [teamSelected, setTeamSelected] = useState<TeamRef | null>(null);
  const [playerSelected, setPlayerSelected] = useState<PlayerDialogTarget | null>(null);
  // which of the team/player dialogs stacks on top when both are open
  // (team opened FROM the player dialog -> "team"; player FROM team -> "player")
  const [topDialog, setTopDialog] = useState<"team" | "player" | null>(null);

  // SSR data seeds the list so crawlers (and users) get real content in the
  // first paint; the client then re-fetches to align with the local timezone
  const [data, setData] = useState<ListingResponse | null>(initialData);
  const [loading, setLoading] = useState(false);
  // true while a SILENT background refresh (60s auto-refresh) is in flight -
  // the current data stays on screen and scores update in place. Any other
  // load (date change, filter change, first load, manual refresh) is
  // "replacing": the old data is hidden and the skeleton shows instead.
  const [silent, setSilent] = useState(false);
  const [error, setError] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // mirrors `loading` without re-render churn, so the auto-refresh timer can
  // skip firing while another load is already in flight
  const loadingRef = useRef(false);
  // remembers whether the SSR listing is still on screen for the CURRENT
  // query - the first client fetch then runs SILENTLY (content stays visible)
  const ssrFresh = useRef(!!initialData);
  // whether the user has navigated dates manually (URL sync only after that)
  const userNavigated = useRef(false);
  // SSE connection state ("connecting" | "open" | "failed") - when the live
  // stream works, the periodic HTTP poll relaxes to a 5-minute safety net;
  // when it fails, the 60s live polling takes over automatically
  const [sseStatus, setSseStatus] = useState<LiveStreamStatus>("connecting");
  // per-match data_version guard: an out-of-order SSE event (older version
  // than one already applied) is dropped instead of overwriting fresh state
  const versionRef = useRef<Record<string, number>>({});
  // true while the browser URL points at a /match/<id> page we pushed from
  // this listing (closing the dialog pops back with history.back())
  const pushedMatchUrl = useRef(false);
  // same for /competition/<id> pages (the competition dialog pushes its URL)
  const pushedCompUrl = useRef(false);
  // same for /team/<id> and /player/<id> pages (team/player dialogs)
  const pushedTeamUrl = useRef(false);
  const pushedPlayerUrl = useRef(false);

  // mount: read persisted prefs + local "today" (avoids SSR/CSR mismatch)
  useEffect(() => {
    const local = localToday();
    setToday(local);
    try {
      const savedLang = localStorage.getItem("mc-lang");
      if (savedLang === "en" || savedLang === "ar") setLang(savedLang);
      const savedMajor = localStorage.getItem("mc-major");
      if (savedMajor !== null) setMajor(savedMajor === "1");
    } catch {
      /* localStorage unavailable */
    }
    // no ?date= in the URL and the user's local day differs from the
    // server-rendered (UTC) day -> switch to the local day, like the app
    // did before SSR existed
    if (!initialDateFromUrl && local && local !== initialDate) {
      ssrFresh.current = false;
      setDate(local);
    }
  }, [initialDate, initialDateFromUrl]);

  useEffect(() => {
    try {
      localStorage.setItem("mc-lang", lang);
      localStorage.setItem("mc-major", major ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [lang, major]);

  // keep <html lang/dir> + document.title in sync with the view (SEO + a11y;
  // the server already renders the correct title/lang for crawlers)
  useEffect(() => {
    document.documentElement.lang = lang;
    document.documentElement.dir = lang === "ar" ? "rtl" : "ltr";
  }, [lang]);

  useEffect(() => {
    if (date && today) document.title = pageTitle(date, today, lang);
  }, [date, today, lang]);

  /** apply the match page's SEO meta while its URL is showing (the SSR
   *  version at /match/<id> serves the same strings to crawlers) */
  const applyMatchMeta = useCallback(
    (m: MatchRow, lang: Lang) => {
      document.title = matchTitle(m, lang);
      document
        .querySelector('meta[name="description"]')
        ?.setAttribute("content", matchDescription(m, lang));
    },
    [],
  );

  /** restore the day listing's SEO meta after leaving a match/competition URL */
  const restoreDayMeta = useCallback((d: string, lang: Lang) => {
    const t0 = localToday() || d;
    document.title = pageTitle(d, t0, lang);
    document
      .querySelector('meta[name="description"]')
      ?.setAttribute("content", pageDescription(d, t0, lang));
  }, []);

  /** apply the competition page's SEO meta while its URL is showing (the SSR
   *  version at /competition/<id>/<slug> serves the same strings to crawlers) */
  const applyCompMeta = useCallback((c: CompetitionRef, lang: Lang) => {
    document.title = competitionTitle({ competition: c }, lang);
    document
      .querySelector('meta[name="description"]')
      ?.setAttribute("content", competitionDescription({ competition: c }, lang));
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

  /** open a match: dialog + shareable/crawlable slug URL + match SEO meta.
   *  The URL uses the CURRENT language's slug (Arabic slug for ar, English
   *  slug for en) - identical to the server canonical.
   *  When the team dialog is open on top, the clicked match TAKES ITS PLACE:
   *  the team dialog closes and its URL entry is REPLACED (same history
   * depth, no async back()/push() dance). */
  const openMatch = useCallback(
    (m: MatchRow) => {
      const fromTeamDialog = !!teamSelected;
      if (fromTeamDialog) {
        setTeamSelected(null);
        setTopDialog(null);
        // the /team/<id> entry is consumed by the replaceState below
        pushedTeamUrl.current = false;
      }
      setSelected(m);
      try {
        // soft navigation: the listing stays mounted and the dialog opens on
        // top, but the URL (and everything a crawler sees when it later fetches
        // this URL server-side) becomes the match's own slug page
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
    [applyMatchMeta, lang, teamSelected],
  );

  /** open a competition: dialog + shareable/crawlable slug URL + its meta
   *  (exactly the same treatment matches get) */
  const openCompetition = useCallback(
    (c: CompetitionRef) => {
      setCompSelected(c);
      try {
        window.history.pushState(
          { mcCompetition: c.id },
          "",
          compUrlFor(c.id, c, lang),
        );
        pushedCompUrl.current = true;
      } catch {
        /* history unavailable - the dialog still opens, just no URL change */
      }
      applyCompMeta(c, lang);
    },
    [applyCompMeta, lang],
  );

  /** open a team: dialog + shareable/crawlable slug URL + its meta (the same
   *  treatment matches and competitions get). Opened from the match dialog
   *  header, standings rows, the player dialog's club chip, ...
   *  When opened FROM the player dialog, the team dialog TAKES THE PLAYER'S
   *  PLACE (replaceState, same as a match clicked inside the team dialog):
   *  re-elevating an already-open dialog breaks Radix's Escape layer order
   *  (one Escape would close both at once). */
  const openTeam = useCallback(
    (team: TeamRef) => {
      if (!team?.id) return;
      const fromPlayerDialog = !!playerSelected;
      if (fromPlayerDialog) {
        setPlayerSelected(null);
        setTopDialog(null);
        // the /player/<id> entry is consumed by the replaceState below
        pushedPlayerUrl.current = false;
      }
      setTeamSelected(team);
      try {
        const url = teamUrlFor(team.id, team, lang);
        if (fromPlayerDialog) {
          window.history.replaceState({ mcTeam: team.id }, "", url);
        } else {
          window.history.pushState({ mcTeam: team.id }, "", url);
        }
        pushedTeamUrl.current = true;
      } catch {
        /* history unavailable - the dialog still opens, just no URL change */
      }
      applyTeamMeta(team, lang);
    },
    [applyTeamMeta, lang, playerSelected],
  );

  /** open a player: dialog + slug URL + its meta. Opened from lineups, the
   *  team dialog's squad list, ... */
  const openPlayer = useCallback(
    (p: PlayerDialogTarget) => {
      if (!p?.id) return;
      setPlayerSelected(p);
      // player opened FROM the team dialog -> player stacks on top
      if (teamSelected) setTopDialog("player");
      try {
        window.history.pushState(
          { mcPlayer: p.id },
          "",
          playerUrlFor(p.id, p, lang),
        );
        pushedPlayerUrl.current = true;
      } catch {
        /* history unavailable - the dialog still opens, just no URL change */
      }
      applyPlayerMeta(p, lang);
    },
    [applyPlayerMeta, lang, teamSelected],
  );

  /** close the match dialog: pop back to the listing URL + restore its meta.
   *  Whatever dialog is still open underneath gets ITS meta restored (team /
   *  player dialogs sit on top of the match dialog, so in practice the match
   *  is topmost when this fires - the chain is defensive). */
  const closeMatch = useCallback(() => {
    setSelected(null);
    if (playerSelected) {
      applyPlayerMeta(playerSelected, lang);
    } else if (teamSelected) {
      applyTeamMeta(teamSelected, lang);
    } else if (compSelected) {
      applyCompMeta(compSelected, lang);
    } else {
      restoreDayMeta(date || today || initialDate, lang);
    }
    if (pushedMatchUrl.current) {
      pushedMatchUrl.current = false;
      try {
        window.history.back();
      } catch {
        /* ignore */
      }
    }
  }, [date, today, lang, initialDate, compSelected, playerSelected, teamSelected, applyCompMeta, applyPlayerMeta, applyTeamMeta, restoreDayMeta]);

  /** close the competition dialog: pop back to the listing URL + restore meta */
  const closeCompetition = useCallback(() => {
    setCompSelected(null);
    restoreDayMeta(date || today || initialDate, lang);
    if (pushedCompUrl.current) {
      pushedCompUrl.current = false;
      try {
        window.history.back();
      } catch {
        /* ignore */
      }
    }
  }, [date, today, lang, initialDate, restoreDayMeta]);

  /** close the team dialog: pop back to the previous URL + restore the meta
   *  of whatever is open underneath (player / match / competition / listing) */
  const closeTeam = useCallback(() => {
    setTeamSelected(null);
    setTopDialog((top) => (top === "team" ? null : top));
    if (playerSelected) {
      applyPlayerMeta(playerSelected, lang);
    } else if (selected) {
      applyMatchMeta(selected, lang);
    } else if (compSelected) {
      applyCompMeta(compSelected, lang);
    } else {
      restoreDayMeta(date || today || initialDate, lang);
    }
    if (pushedTeamUrl.current) {
      pushedTeamUrl.current = false;
      try {
        window.history.back();
      } catch {
        /* ignore */
      }
    }
  }, [date, today, lang, initialDate, selected, compSelected, playerSelected, applyMatchMeta, applyCompMeta, applyPlayerMeta, restoreDayMeta]);

  /** close the player dialog: pop back + restore the underlying meta */
  const closePlayer = useCallback(() => {
    setPlayerSelected(null);
    setTopDialog((top) => (top === "player" ? null : top));
    if (teamSelected) {
      applyTeamMeta(teamSelected, lang);
    } else if (selected) {
      applyMatchMeta(selected, lang);
    } else if (compSelected) {
      applyCompMeta(compSelected, lang);
    } else {
      restoreDayMeta(date || today || initialDate, lang);
    }
    if (pushedPlayerUrl.current) {
      pushedPlayerUrl.current = false;
      try {
        window.history.back();
      } catch {
        /* ignore */
      }
    }
  }, [date, today, lang, initialDate, selected, compSelected, teamSelected, applyMatchMeta, applyCompMeta, applyTeamMeta, restoreDayMeta]);

  /**
   * Switch the UI language AND keep everything a crawler sees consistent:
   * the TOPMOST open dialog moves to its new-language URL (replaceState) and
   * its meta switches to the new language's strings.
   */
  const switchLang = useCallback(
    (next: Lang) => {
      setLang(next);
      if (playerSelected) {
        try {
          window.history.replaceState(
            { mcPlayer: playerSelected.id },
            "",
            playerUrlFor(playerSelected.id, playerSelected, next),
          );
        } catch {
          /* ignore */
        }
        applyPlayerMeta(playerSelected, next);
      } else if (teamSelected) {
        try {
          window.history.replaceState(
            { mcTeam: teamSelected.id },
            "",
            teamUrlFor(teamSelected.id, teamSelected, next),
          );
        } catch {
          /* ignore */
        }
        applyTeamMeta(teamSelected, next);
      } else if (selected) {
        try {
          window.history.replaceState(
            { mcMatch: selected.matchId },
            "",
            matchUrlFor(selected.matchId, selected, next),
          );
        } catch {
          /* ignore */
        }
        applyMatchMeta(selected, next);
      } else if (compSelected) {
        try {
          window.history.replaceState(
            { mcCompetition: compSelected.id },
            "",
            compUrlFor(compSelected.id, compSelected, next),
          );
        } catch {
          /* ignore */
        }
        applyCompMeta(compSelected, next);
      }
    },
    [selected, compSelected, teamSelected, playerSelected, applyMatchMeta, applyCompMeta, applyTeamMeta, applyPlayerMeta],
  );

  // browser BACK from a pushed /match|/competition|/team|/player URL: close
  // the corresponding dialog + restore the right meta (pushState does not
  // trigger a popstate on its own)
  useEffect(() => {
    const onPopState = () => {
      const path = window.location.pathname;
      const onMatchUrl = path.startsWith("/match/");
      const onCompUrl = path.startsWith("/competition/");
      const onTeamUrl = path.startsWith("/team/");
      const onPlayerUrl = path.startsWith("/player/");
      if (!onMatchUrl && pushedMatchUrl.current) {
        pushedMatchUrl.current = false;
        setSelected(null);
      }
      if (!onCompUrl && pushedCompUrl.current) {
        pushedCompUrl.current = false;
        setCompSelected(null);
      }
      if (!onTeamUrl && pushedTeamUrl.current) {
        pushedTeamUrl.current = false;
        setTeamSelected(null);
      }
      if (!onPlayerUrl && pushedPlayerUrl.current) {
        pushedPlayerUrl.current = false;
        setPlayerSelected(null);
      }
      // restore the meta of whatever is now on screen: the topmost dialog
      // still open (player / team / match / competition) or the day listing
      if (onPlayerUrl && playerSelected) {
        applyPlayerMeta(playerSelected, lang);
      } else if (onTeamUrl && teamSelected) {
        applyTeamMeta(teamSelected, lang);
      } else if (onMatchUrl && selected) {
        applyMatchMeta(selected, lang);
      } else if (onCompUrl && compSelected) {
        applyCompMeta(compSelected, lang);
      } else if (!onMatchUrl) {
        restoreDayMeta(date || today || initialDate, lang);
      }
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [date, today, lang, initialDate, selected, compSelected, teamSelected, playerSelected, applyMatchMeta, applyCompMeta, applyTeamMeta, applyPlayerMeta, restoreDayMeta]);

  // shareable/crawlable URLs: reflect the selected day in ?date= (no reload,
  // no re-render - pure history state so crawlers and users get distinct URLs)
  useEffect(() => {
    if (!userNavigated.current || !date || !today) return;
    try {
      const url = new URL(window.location.href);
      if (date === today) url.searchParams.delete("date");
      else url.searchParams.set("date", date);
      window.history.replaceState(null, "", url.toString());
    } catch {
      /* ignore */
    }
  }, [date, today]);

  const effectiveDate: string = date || today || initialDate;

  const load = useCallback(
    async (silent = false) => {
      if (!effectiveDate || !today) return;
      abortRef.current?.abort();
      const ac = new AbortController();
      abortRef.current = ac;
      // a REPLACING load changes the query (date/filter/first load): drop the
      // old data now so a failure shows the error box instead of flashing the
      // previous day's matches under the new date
      if (!silent) setData(null);
      loadingRef.current = true;
      setLoading(true);
      setSilent(silent);
      setError(false);
      try {
        // tz: minutes EAST of UTC (JS getTimezoneOffset is inverted) - keeps
        // listing days aligned with the user's LOCAL calendar
        const tz = -new Date().getTimezoneOffset();
        const res = await fetch(
          `/api/matches?date=${effectiveDate}&today=${today}&major=${major ? 1 : 0}&tz=${tz}`,
          { signal: ac.signal },
        );
        if (!res.ok) throw new Error("failed");
        const json: ListingResponse = await res.json();
        setData(json);
        setUpdatedAt(new Date());
      } catch (e) {
        if ((e as Error).name !== "AbortError") setError(true);
      } finally {
        if (!ac.signal.aborted) {
          loadingRef.current = false;
          setLoading(false);
          setSilent(false);
        }
      }
    },
    [effectiveDate, today, major],
  );

  useEffect(() => {
    // SSR content still valid for this exact query -> validate it silently in
    // the background (keeps the server-rendered list on screen, refreshes in
    // place); otherwise it's a fresh/replacing load
    load(ssrFresh.current);
    ssrFresh.current = false;
  }, [load]);

  // auto-refresh (only for the live "today" view); SILENT so the current list
  // stays on screen and scores update in place. The cadence adapts to what is
  // happening on the pitch (see autoRefreshMs below).
  const isToday = data?.dayType === "today" || effectiveDate === today;

  const hasLive = useMemo(
    () => (data?.groups || []).some((g) => g.matches.some((m) => m.status === "LIVE")),
    [data],
  );

  // earliest upcoming kickoff + a "should already be live" flag (kickoff
  // passed less than KICKOFF_WATCH_MS ago but the status is still FIXTURE)
  const { nextKickoffMs, startPending } = useMemo(() => {
    let next: number | null = null;
    let pending = false;
    const now = Date.now();
    for (const g of data?.groups || []) {
      for (const m of g.matches) {
        if (m.status !== "FIXTURE" || !m.kickoffUtc) continue;
        const t = Date.parse(m.kickoffUtc);
        if (Number.isNaN(t)) continue;
        if (t > now) {
          if (next === null || t < next) next = t;
        } else if (now - t <= KICKOFF_WATCH_MS) {
          pending = true;
        }
      }
    }
    return { nextKickoffMs: next, startPending: pending };
  }, [data]);

  // delay until the next silent auto-refresh:
  //  - live match running / one just kicked off / nothing loaded yet -> 60s
  //  - otherwise 30 minutes, but never past the next kickoff: refresh right
  //  after it so the app switches to live mode the moment a match starts
  //  (a FAILED load is not handled here - the error effect below retries it)
  const autoRefreshMs = useMemo(() => {
    if (!data || hasLive || startPending) {
      // SSE is delivering live updates -> the HTTP poll is only a safety
      // net; SSE failed -> the 60s poll IS the live fallback
      return sseStatus === "open" ? SSE_SAFETY_POLL_MS : LIVE_POLL_MS;
    }
    if (nextKickoffMs !== null) {
      const until = nextKickoffMs - Date.now();
      if (until < IDLE_POLL_MS - KICKOFF_BUFFER_MS) return until + KICKOFF_BUFFER_MS;
    }
    return IDLE_POLL_MS;
  }, [data, hasLive, startPending, nextKickoffMs, sseStatus]);

  useEffect(() => {
    if (!isToday || error) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {
      timer = setTimeout(() => {
        schedule(); // re-arm; the delay is re-derived whenever fresh data lands
        if (document.visibilityState === "visible" && !loadingRef.current) load(true);
      }, autoRefreshMs);
    };
    schedule();
    // coming back to the tab: refresh at once - after a long idle the data on
    // screen may be up to half an hour old
    const onVisible = () => {
      if (document.visibilityState === "visible" && !loadingRef.current) load(true);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [isToday, autoRefreshMs, load, error]);

  // ---- LIVE MATCH STREAM (SSE) -----------------------------------------------
  // On the "today" view, one EventSource delivers match.updated deltas and
  // ONLY the affected match row is patched - the rest of the page state is
  // untouched and no re-fetch happens. When the stream fails repeatedly the
  // existing periodic HTTP refresh above simply stays at its 60s live
  // cadence (the fallback path), and the status line tells the user.
  useEffect(() => {
    if (!isToday || !data) return;
    const stream = new LiveMatchStream({
      onEvent: (event: LiveEvent) => {
        if (event.type !== "match.updated") return; // snapshot/event handled via HTTP + next delta
        const delta = event as MatchUpdatedDelta;
        const mid = delta.matchId;
        const version = delta.version ?? 0;
        // out-of-order protection: ignore anything older than what we applied
        if ((versionRef.current[mid] ?? 0) > version) return;
        versionRef.current[mid] = version;
        setData((prev) => {
          if (!prev) return prev;
          let touched = false;
          const groups = prev.groups.map((g) => {
            const idx = g.matches.findIndex((m) => m.matchId === mid);
            if (idx < 0) return g;
            touched = true;
            const matches = [...g.matches];
            matches[idx] = applyMatchDelta(matches[idx], delta.match);
            return { ...g, matches };
          });
          return touched ? { ...prev, groups } : prev;
        });
        setUpdatedAt(new Date());
      },
      onStatusChange: (status) => setSseStatus(status),
    });
    stream.connect();
    return () => stream.close();
    // reconnect when the day listing itself is replaced (date change, filter
    // change) - data is re-fetched through HTTP first
  }, [isToday, data?.date, data?.totalMatches === 0]);

  // a failed load heals itself - there is no manual refresh button: retry
  // every ERROR_RETRY_MS on any date (not just today) and at once when the
  // tab becomes visible again. Silent only while the current data is still
  // on screen (a failed background refresh); replacing otherwise (the error
  // box is showing, so the retry goes through the skeleton).
  useEffect(() => {
    if (!error) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {
      timer = setTimeout(() => {
        if (document.visibilityState === "visible" && !loadingRef.current) {
          load(!!data);
        } else {
          schedule(); // hidden tab or a load in flight - keep waiting
        }
      }, ERROR_RETRY_MS);
    };
    schedule();
    const onVisible = () => {
      if (document.visibilityState === "visible" && !loadingRef.current) load(!!data);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [error, load, data]);

  // this load REPLACES the view (different date / filter change): the old
  // data is hidden and the skeleton shows while waiting; the silent
  // auto-refresh keeps the current list on screen
  const replacing = loading && (!data || !silent);

  // client-side search filter (team or competition, EN or AR)
  const filteredGroups = useMemo(() => {
    if (!data) return [];
    const q = search.trim().toLowerCase();
    if (!q) return data.groups;
    return data.groups
      .map((g) => {
        const compHit =
          (g.competition.nameEn || "").toLowerCase().includes(q) ||
          (g.competition.nameAr || "").includes(search.trim()) ||
          (g.competition.areaNameEn || "").toLowerCase().includes(q) ||
          (g.competition.areaNameAr || "").includes(search.trim());
        const matches = g.matches.filter(
          (m) =>
            compHit ||
            (m.homeTeam.nameEn || "").toLowerCase().includes(q) ||
            (m.homeTeam.nameAr || "").includes(search.trim()) ||
            (m.awayTeam.nameEn || "").toLowerCase().includes(q) ||
            (m.awayTeam.nameAr || "").includes(search.trim()),
        );
        return { ...g, matches };
      })
      .filter((g) => g.matches.length > 0);
  }, [data, search]);

  const liveCount = useMemo(
    () =>
      replacing
        ? 0 // stale data hidden - don't show its live count either
        : (data?.groups || []).reduce(
            (n, g) => n + g.matches.filter((m) => m.status === "LIVE").length,
            0,
          ),
    [data, replacing],
  );

  const s = t(lang);
  const rtl = lang === "ar";
  const dayType =
    !replacing && data?.dayType
      ? data.dayType
      : effectiveDate === today
        ? "today"
        : effectiveDate && today && effectiveDate < today
          ? "past"
          : "future";

  // date navigation wrapper: marks the URL-sync effect as user-driven
  const changeDate = useCallback((d: string) => {
    userNavigated.current = true;
    setDate(d);
  }, []);

  return (
    <div
      dir={rtl ? "rtl" : "ltr"}
      lang={lang}
      className="font-app flex min-h-screen flex-col bg-[#e9edf2] text-[#1c2b3a]"
    >
      {/* ======= header ======= */}
      <header className="bg-gradient-to-b from-[#1d4f92] to-[#123a70] text-white shadow-md">
        <div className="mx-auto flex w-full max-w-4xl items-center gap-3 px-3 py-3">
          {/* classic text logo */}
          <div className="flex items-center gap-2">
            <span className="flex h-9 w-9 items-center justify-center rounded-full border-2 border-white/60 bg-white/10">
              <svg viewBox="0 0 24 24" className="h-5 w-5" fill="none" stroke="currentColor" strokeWidth="1.6">
                <circle cx="12" cy="12" r="9.5" />
                <path d="M12 8.2l3.6 2.6-1.4 4.2H9.8L8.4 10.8 12 8.2z" fill="currentColor" stroke="none" />
              </svg>
            </span>
            <div className="leading-tight">
              <h1 className="text-[17px] font-extrabold tracking-wide">{s.appTitle}</h1>
              <p className="text-[11px] text-white/70">{s.appSubtitle}</p>
            </div>
          </div>

          <div className="ms-auto flex items-center gap-2">
            {liveCount > 0 && (
              <span className="hidden items-center gap-1.5 rounded-full bg-[#d31f26] px-2.5 py-1 text-[11px] font-bold sm:flex">
                <span className="relative flex h-2 w-2">
                  <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-white opacity-70" />
                  <span className="relative inline-flex h-2 w-2 rounded-full bg-white" />
                </span>
                {liveCount} {s.liveCount}
              </span>
            )}
            {/* language toggle */}
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

      {/* ======= main ======= */}
      <main className="mx-auto w-full max-w-4xl flex-1 px-2 py-3 sm:px-3">
        <h2 className="sr-only">{s.resultsTitle}</h2>
        <div className="space-y-3">
          <DateNav
            date={effectiveDate}
            today={today || effectiveDate}
            dayType={dayType as "past" | "today" | "future"}
            lang={lang}
            onChange={changeDate}
          />

          {/* toolbar: search + major toggle (data refreshes itself -
              no refresh button, nothing for the user to spam) */}
          <div className="flex flex-wrap items-center gap-2 rounded-md border border-[#c3cedd] bg-white px-3 py-2 shadow-sm">
            <div className="relative min-w-[180px] flex-1">
              <Search className="absolute top-1/2 h-4 w-4 -translate-y-1/2 text-[#7d8ea3] ltr:left-2 rtl:right-2" />
              <input
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder={s.searchPlaceholder}
                aria-label={s.searchPlaceholder}
                className="h-8 w-full rounded border border-[#b9c8dd] bg-[#f6f9fd] ps-8 pe-2 text-[13px] text-[#1c2b3a] placeholder:text-[#93a1b3] focus:border-[#17457f] focus:bg-white focus:outline-none"
              />
            </div>

            <label
              className="flex cursor-pointer select-none items-center gap-1.5 text-[12px] font-semibold text-[#33455e]"
              title={s.majorOnlyHint}
            >
              <input
                type="checkbox"
                checked={major}
                onChange={(e) => setMajor(e.target.checked)}
                className="h-4 w-4 accent-[#17457f]"
              />
              {s.majorOnly}
            </label>
          </div>

          {/* content states: while a replacing load is in flight the OLD data
              stays hidden and the skeleton shows IN ITS PLACE; a silent
              background refresh keeps the current list visible */}
          {replacing && !error && <ListSkeleton label={s.loading} />}

          {error && (!data || replacing) && (
            <div className="flex flex-col items-center gap-2 rounded-md border border-[#e5b6b2] bg-[#fdf1f0] px-4 py-10">
              <p className="text-sm font-semibold text-[#b3392f]">{s.loadError}</p>
              {/* no retry button: the app retries by itself every 30s */}
              <p className="flex items-center gap-1.5 text-[12px] text-[#7d8ea3]">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                {s.autoRetry}
              </p>
            </div>
          )}

          {data && !replacing && (
            <>
              <div className="flex items-center justify-between px-1 text-[11.5px] text-[#5b6b80]">
                <span>
                  {data.totalMatches} {s.matchesCount}
                  {isToday && (
                    <span className="ms-2 text-[#d31f26]">
                      {sseStatus === "open" ? (
                        <>
                          {/* pulsing dot: live SSE updates are flowing */}
                          <span className="me-1 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[#d31f26] align-middle" />
                          {s.liveOn}
                        </>
                      ) : hasLive || startPending ? (
                        <>
                          {sseStatus === "failed" ? s.liveFallback : s.autoRefresh}
                        </>
                      ) : (
                        s.autoRefreshIdle
                      )}
                    </span>
                  )}
                </span>
                {/* passive refresh status: last update time + a tiny spinner
                    while a silent background refresh is in flight */}
                <span className="flex items-center gap-1.5">
                  {loading && silent && <Loader2 className="h-3 w-3 animate-spin" />}
                  {updatedAt && (
                    <span className="hidden sm:inline">
                      {s.lastUpdate}{" "}
                      {updatedAt.toLocaleTimeString(
                        lang === "ar" ? "ar-MA-u-nu-latn" : "en-GB",
                        { hour: "2-digit", minute: "2-digit" },
                      )}
                    </span>
                  )}
                </span>
              </div>

              {filteredGroups.length === 0 ? (
                <div className="rounded-md border border-[#c3cedd] bg-white px-4 py-12 text-center shadow-sm">
                  <p className="text-[15px] font-bold text-[#33455e]">{s.noMatches}</p>
                  <p className="mt-1 text-[12.5px] text-[#7d8ea3]">{s.noMatchesHint}</p>
                </div>
              ) : (
                <MatchList
                  groups={filteredGroups}
                  lang={lang}
                  onOpen={openMatch}
                  onOpenCompetition={openCompetition}
                />
              )}
            </>
          )}
        </div>
      </main>

      {/* ======= footer (sticky bottom) ======= */}
      <footer className="mt-auto border-t border-[#c3cedd] bg-white/80 py-2.5 backdrop-blur">
        <div className="mx-auto flex w-full max-w-4xl flex-wrap items-center justify-center gap-x-3 gap-y-1 px-3 text-[11px] text-[#7d8ea3]">
          <span>{s.footer}</span>
        </div>
      </footer>

      {/* ======= competition dialog (standings + rounds) ======= */}
      <CompetitionDialog
        competition={compSelected}
        lang={lang}
        onClose={closeCompetition}
        onOpenMatch={openMatch}
        onOpenTeam={openTeam}
      />

      {/* ======= match detail dialog ======= */}
      <MatchDialog
        match={selected}
        lang={lang}
        onClose={closeMatch}
        onOpenTeam={openTeam}
        onOpenPlayer={openPlayer}
      />

      {/* ======= team dialog (results/fixtures + squad) ======= */}
      <TeamDialog
        team={teamSelected}
        lang={lang}
        onClose={closeTeam}
        onOpenMatch={openMatch}
        onOpenPlayer={openPlayer}
        elevated={topDialog === "team"}
      />

      {/* ======= player dialog (bio + career) ======= */}
      <PlayerDialog
        player={playerSelected}
        lang={lang}
        onClose={closePlayer}
        onOpenTeam={openTeam}
        elevated={topDialog === "player"}
      />
    </div>
  );
}

function ListSkeleton({ label }: { label?: string }) {
  return (
    <div className="space-y-3" aria-busy="true" role="status" aria-label={label || "loading"}>
      {label && (
        <div className="flex items-center justify-center gap-2 py-1 text-[12.5px] font-semibold text-[#5b6b80]">
          <Loader2 className="h-4 w-4 animate-spin" />
          {label}
        </div>
      )}
      {[0, 1, 2].map((i) => (
        <div key={i} className="overflow-hidden rounded-md border border-[#c3cedd] bg-white shadow-sm">
          <div className="h-10 animate-pulse bg-gradient-to-b from-[#e8eff9] to-[#d3e1f2]" />
          {[0, 1, 2, 3].map((j) => (
            <div key={j} className="h-11 animate-pulse border-b border-[#e2e9f2] bg-[#f6f9fd]" />
          ))}
        </div>
      ))}
    </div>
  );
}
