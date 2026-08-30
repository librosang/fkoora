"use client";

import { useState } from "react";
import { ChevronDown, Trophy } from "lucide-react";
import type { CompetitionGroup, CompetitionRef, Lang, MatchRow, TeamRef } from "@/lib/goal/types";
import { compLabel, nameOf, statusDisplay, t } from "@/lib/i18n";
import { compUrlFor, matchUrlFor } from "@/lib/seo";
import { RedCardChips } from "./icons";
import { Crest } from "./crest";

interface MatchListProps {
  groups: CompetitionGroup[];
  lang: Lang;
  onOpen: (m: MatchRow) => void;
  onOpenCompetition: (c: CompetitionRef) => void;
}

export function MatchList({ groups, lang, onOpen, onOpenCompetition }: MatchListProps) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const s = t(lang);

  const toggle = (id: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="space-y-3">
      {groups.map((g) => {
        const isCollapsed = collapsed.has(g.competition.id);
        return (
          <section
            key={g.competition.id}
            aria-label={compLabel(g.competition, lang)}
            className="overflow-hidden rounded-md border border-[#c3cedd] bg-white shadow-sm"
          >
            {/* competition bar - light-blue gradient strip.
                NOTE: two sibling buttons (collapse toggle + competition page
                button) instead of a nested button - nested <button> is invalid
                HTML and breaks click handling in Safari. */}
            <div className="flex items-stretch border-b border-[#c3cedd] bg-gradient-to-b from-[#e8eff9] to-[#d3e1f2]">
              <button
                type="button"
                onClick={() => toggle(g.competition.id)}
                aria-expanded={!isCollapsed}
                className="flex min-w-0 flex-1 items-center gap-2 px-3 py-2 text-start transition-colors hover:from-[#dfe9f7] hover:to-[#c8d9ee]"
              >
                <Crest url={g.competition.imageUrl} size={18} />
                <span className="truncate text-[13px] font-bold text-[#17457f]">
                  {compLabel(g.competition, lang)}
                </span>
                <span className="ms-auto flex shrink-0 items-center gap-2">
                  <span className="rounded-full border border-[#b9c8dd] bg-white/80 px-2 py-0.5 text-[11px] font-semibold text-[#4a5a70]">
                    {g.matches.length}
                  </span>
                  <ChevronDown
                    className={`h-4 w-4 text-[#4a5a70] transition-transform ${isCollapsed ? "-rotate-90" : ""}`}
                  />
                </span>
              </button>
              {/* competition page link: a real <a href> so crawlers can
                  discover every /competition/<id>/<slug> page straight from
                  the SSR'd listing; the click is intercepted for the dialog +
                  URL push (same UX as before) */}
              <a
                href={compUrlFor(g.competition.id, g.competition, lang)}
                onClick={(e) => {
                  e.preventDefault();
                  onOpenCompetition(g.competition);
                }}
                title={s.compInfo}
                aria-label={`${s.compInfo}: ${compLabel(g.competition, lang)}`}
                className="flex w-10 shrink-0 items-center justify-center border-s border-[#c3cedd] text-[#4a5a70] transition-colors hover:bg-[#c8d9ee] hover:text-[#17457f]"
              >
                <Trophy className="h-4 w-4" />
              </a>
            </div>

            {!isCollapsed && (
              <div role="list">
                {g.matches.map((m, i) => (
                  <MatchRowView
                    key={m.matchId}
                    m={m}
                    lang={lang}
                    zebra={i % 2 === 1}
                    onOpen={onOpen}
                  />
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}

function MatchRowView({
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
  const hasAgg =
    hasScore &&
    m.homeAggScore !== null &&
    m.awayAggScore !== null &&
    (m.homeAggScore !== m.homeScore || m.awayAggScore !== m.awayScore);
  const homeWin = hasScore && (m.homeScore ?? 0) > (m.awayScore ?? 0);
  const awayWin = hasScore && (m.awayScore ?? 0) > (m.homeScore ?? 0);
  const live = st.kind === "live";

  return (
    <a
      href={matchUrlFor(m.matchId, m, lang)}
      role="listitem"
      onClick={(e) => {
        // intercept for the dialog + URL push; crawlers without JS follow
        // the href to the server-rendered match page
        e.preventDefault();
        onOpen(m);
      }}
      className={`block w-full border-b border-[#e2e9f2] px-2 py-2 text-start transition-colors last:border-b-0 hover:bg-[#e8f1fb] ${
        zebra ? "bg-[#f6f9fd]" : "bg-white"
      }`}
    >
      {/* ============ MOBILE: stacked team lines ============ */}
      <div className="flex w-full items-stretch gap-2 sm:hidden">
        <div className="flex w-[48px] shrink-0 flex-col items-center justify-center gap-0.5">
          <StatusCell st={st} />
        </div>
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          <TeamLineMobile
            team={m.homeTeam}
            lang={lang}
            score={hasScore ? m.homeScore : null}
            win={homeWin}
            redCards={m.homeRedCards}
            live={live}
          />
          <TeamLineMobile
            team={m.awayTeam}
            lang={lang}
            score={hasScore ? m.awayScore : null}
            win={awayWin}
            redCards={m.awayRedCards}
            live={live}
          />
          {hasAgg && (
            <div className="self-end text-[10px] leading-none tabular-nums text-[#7d8ea3]">
              ({m.homeAggScore} - {m.awayAggScore})
            </div>
          )}
        </div>
      </div>

      {/* ============ DESKTOP: classic horizontal row (time | home | score | away) ============ */}
      <div className="hidden w-full grid-cols-[58px_minmax(0,1fr)_78px_minmax(0,1fr)] items-center gap-1 sm:grid">
        <StatusCell st={st} />

        {/* home team: name hugs the score, crest on the outer side of it */}
        <div className="flex min-w-0 items-center justify-end gap-1.5">
          <RedCardChips n={m.homeRedCards} />
          <span
            className={`truncate text-[14px] ${
              homeWin ? "font-bold text-[#14263a]" : "font-medium text-[#1c2b3a]"
            }`}
          >
            {nameOf(m.homeTeam, lang)}
          </span>
          <Crest url={m.homeTeam.crestUrl} size={20} />
        </div>

        {/* score */}
        <div className="flex flex-col items-center gap-0.5">
          {hasScore ? (
            <span
              className={`text-[15px] font-extrabold tabular-nums leading-tight ${
                live ? "text-[#d31f26]" : "text-[#14263a]"
              }`}
            >
              {m.homeScore} - {m.awayScore}
            </span>
          ) : (
            <span className="text-[13px] font-semibold text-[#a5b1c0]">-</span>
          )}
          {hasAgg && (
            <span className="text-[10px] tabular-nums leading-none text-[#7d8ea3]">
              ({m.homeAggScore} - {m.awayAggScore})
            </span>
          )}
        </div>

        {/* away team: crest on the outer side of the score, name follows */}
        <div className="flex min-w-0 items-center gap-1.5">
          <Crest url={m.awayTeam.crestUrl} size={20} />
          <span
            className={`truncate text-[14px] ${
              awayWin ? "font-bold text-[#14263a]" : "font-medium text-[#1c2b3a]"
            }`}
          >
            {nameOf(m.awayTeam, lang)}
          </span>
          <RedCardChips n={m.awayRedCards} />
        </div>
      </div>
    </a>
  );
}

/** Status / kickoff cell shared by the mobile + desktop layouts. */
function StatusCell({ st }: { st: ReturnType<typeof statusDisplay> }) {
  return (
    <>
      {st.kind === "live" ? (
        <span className="flex items-center gap-1 text-[12px] font-extrabold tabular-nums text-[#d31f26]">
          <span className="relative flex h-2 w-2 shrink-0">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#d31f26] opacity-60" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-[#d31f26]" />
          </span>
          {st.main}
        </span>
      ) : st.kind === "done" ? (
        <span className="text-[11px] font-semibold text-[#5b6b80]">{st.main}</span>
      ) : st.kind === "cancelled" ? (
        <span className="text-[11px] font-semibold text-[#98a3b3]">{st.main}</span>
      ) : (
        <span className="text-[13px] font-bold tabular-nums text-[#33455e]">{st.main}</span>
      )}
    </>
  );
}

/** One team line of the mobile stacked layout: crest + name + score. */
function TeamLineMobile({
  team,
  lang,
  score,
  win,
  redCards,
  live,
}: {
  team: TeamRef;
  lang: Lang;
  score: number | null;
  win: boolean;
  redCards: number;
  live: boolean;
}) {
  return (
    <div className="flex min-w-0 items-center gap-1.5">
      <Crest url={team.crestUrl} size={19} />
      <span
        className={`min-w-0 truncate text-[14px] ${
          win ? "font-bold text-[#14263a]" : "font-medium text-[#1c2b3a]"
        }`}
      >
        {nameOf(team, lang)}
      </span>
      <RedCardChips n={redCards} />
      {score !== null && (
        <span
          className={`ms-auto shrink-0 text-[15px] font-extrabold tabular-nums leading-none ${
            live ? "text-[#d31f26]" : win ? "text-[#14263a]" : "text-[#5b6b80]"
          }`}
        >
          {score}
        </span>
      )}
    </div>
  );
}
