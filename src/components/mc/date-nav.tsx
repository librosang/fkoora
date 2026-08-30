"use client";

import { ChevronLeft, ChevronRight } from "lucide-react";
import type { Lang } from "@/lib/goal/types";
import type { DayType } from "@/lib/goal/types";
import {
  formatDayLong,
  shiftDate,
  t,
} from "@/lib/i18n";

interface DateNavProps {
  date: string; // effective date being viewed
  today: string; // user's local today
  dayType: DayType;
  lang: Lang;
  onChange: (date: string) => void;
}

export function DateNav({ date, today, dayType, lang, onChange }: DateNavProps) {
  const s = t(lang);
  const rtl = lang === "ar";
  const isToday = date === today;

  const title =
    dayType === "today"
      ? s.todayTitle
      : dayType === "past"
        ? `${s.resultsTitle} ${formatDayLong(date, lang)}`
        : `${s.fixturesTitle} ${formatDayLong(date, lang)}`;

  const prev = shiftDate(date, -1);
  const next = shiftDate(date, 1);

  return (
    <nav
      aria-label={s.pickDate}
      className="rounded-md border border-[#c3cedd] bg-white shadow-sm"
    >
      {/* title bar */}
      <div className="flex items-center gap-2 border-b border-[#d7e0ec] bg-gradient-to-b from-[#f2f6fb] to-[#e6eef8] px-3 py-2">
        <span className="h-4 w-1 rounded bg-[#17457f]" aria-hidden="true" />
        <h2 className="truncate text-[15px] font-bold text-[#17457f]">{title}</h2>
        <span className="ms-auto shrink-0 rounded border border-[#b9c8dd] bg-white px-1.5 py-0.5 text-[11px] font-semibold text-[#4a5a70]">
          {dayType === "past" ? s.resultsShort : dayType === "today" ? s.liveNow : s.fixturesShort}
        </span>
      </div>

      {/* controls */}
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <button
          type="button"
          onClick={() => onChange(prev)}
          aria-label={s.yesterday}
          className="flex h-8 w-8 items-center justify-center rounded border border-[#b9c8dd] bg-[#f2f6fb] text-[#17457f] transition-colors hover:bg-[#e0eaf6] active:scale-95"
        >
          {rtl ? <ChevronRight className="h-4 w-4" /> : <ChevronLeft className="h-4 w-4" />}
        </button>

        <input
          type="date"
          value={date}
          min="2020-01-01"
          max="2030-12-31"
          onChange={(e) => e.target.value && onChange(e.target.value)}
          aria-label={s.pickDate}
          className="h-8 rounded border border-[#b9c8dd] bg-white px-2 text-[13px] text-[#1c2b3a] focus:border-[#17457f] focus:outline-none"
        />

        <button
          type="button"
          onClick={() => onChange(next)}
          aria-label={s.tomorrow}
          className="flex h-8 w-8 items-center justify-center rounded border border-[#b9c8dd] bg-[#f2f6fb] text-[#17457f] transition-colors hover:bg-[#e0eaf6] active:scale-95"
        >
          {rtl ? <ChevronLeft className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
        </button>

        <div className="mx-1 hidden h-6 w-px bg-[#d7e0ec] sm:block" aria-hidden="true" />

        <div className="flex items-center gap-1">
          <QuickBtn active={date === shiftDate(today, -1)} onClick={() => onChange(shiftDate(today, -1))}>
            {s.yesterday}
          </QuickBtn>
          <QuickBtn active={isToday} onClick={() => onChange(today)}>
            {s.today}
          </QuickBtn>
          <QuickBtn active={date === shiftDate(today, 1)} onClick={() => onChange(shiftDate(today, 1))}>
            {s.tomorrow}
          </QuickBtn>
        </div>
      </div>
    </nav>
  );
}

function QuickBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`h-8 rounded border px-3 text-[12px] font-semibold transition-colors active:scale-95 ${
        active
          ? "border-[#17457f] bg-[#17457f] text-white"
          : "border-[#b9c8dd] bg-[#f2f6fb] text-[#17457f] hover:bg-[#e0eaf6]"
      }`}
    >
      {children}
    </button>
  );
}
