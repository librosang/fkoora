/**
 * Bilingual UI strings + display helpers (client-safe).
 * Arabic uses Latin digits (ar-MA-u-nu-latn), the classic Arabic web convention.
 */
import type { Lang } from "./goal/types";

export interface Strings {
  appTitle: string;
  appSubtitle: string;
  resultsTitle: string;
  resultsShort: string;
  todayTitle: string;
  fixturesTitle: string;
  fixturesShort: string;
  yesterday: string;
  today: string;
  tomorrow: string;
  pickDate: string;
  majorOnly: string;
  majorOnlyHint: string;
  close: string;
  searchPlaceholder: string;
  liveCount: string;
  matchesCount: string;
  noMatches: string;
  noMatchesHint: string;
  loadError: string;
  retry: string;
  autoRetry: string;
  loading: string;
  refreshing: string;
  lastUpdate: string;
  autoRefresh: string;
  autoRefreshIdle: string;
  liveOn: string;
  liveFallback: string;
  events: string;
  lineups: string;
  stats: string;
  notStarted: string;
  lineupNotAnnounced: string;
  noStats: string;
  venue: string;
  referee: string;
  round: string;
  firstHalf: string;
  htLabel: string;
  ftLabel: string;
  ftShort: string;
  aetShort: string;
  pensShort: string;
  cancelled: string;
  postponed: string;
  live: string;
  liveNow: string;
  vs: string;
  pens: string;
  assist: string;
  missedPenalty: string;
  ownGoal: string;
  subIn: string;
  subOut: string;
  manager: string;
  captain: string;
  starters: string;
  substitutes: string;
  source: string;
  footer: string;
  goal: string;
  yellowCard: string;
  redCard: string;
  substitution: string;
  varGoalCancelled: string;
  varGoalConfirmed: string;
  varPenaltyCancelled: string;
  varPenaltyNotAwarded: string;
  varPenaltyAwarded: string;
  varDecision: string;
  // competition dialog
  tableTab: string;
  roundsTab: string;
  posCol: string;
  playedCol: string;
  winCol: string;
  drawCol: string;
  loseCol: string;
  gfCol: string;
  gaCol: string;
  gdCol: string;
  pointsCol: string;
  formCol: string;
  noStandings: string;
  selectRound: string;
  roundMatchesCount: string;
  noMatchesInRound: string;
  compInfo: string;
  winForm: string;
  drawForm: string;
  loseForm: string;
  seasonLabel: string;
  // team dialog / team page
  teamInfo: string;
  matchesTab: string;
  squadTab: string;
  recentResults: string;
  upcomingFixtures: string;
  noTeamMatches: string;
  noSquad: string;
  yearsOld: string;
  // player dialog / player page
  playerInfo: string;
  position: string;
  posGoalkeeper: string;
  posDefender: string;
  posMidfielder: string;
  posForward: string;
  posOther: string;
  age: string;
  height: string;
  weight: string;
  nationality: string;
  birthDate: string;
  birthPlace: string;
  shirtNumber: string;
  currentClub: string;
  career: string;
  season: string;
  appsCol: string;
  goalsCol: string;
  assistsCol: string;
  yellowCol: string;
  redCol: string;
  minutesCol: string;
  loan: string;
  noCareer: string;
  clubCol: string;
}

const AR: Strings = {
  appTitle: "فكوورة",
  appSubtitle: "نتائج ومواعيد المباريات",
  resultsTitle: "نتائج مباريات",
  resultsShort: "النتائج",
  todayTitle: "مباريات اليوم",
  fixturesTitle: "مواعيد مباريات",
  fixturesShort: "المواعيد",
  yesterday: "أمس",
  today: "اليوم",
  tomorrow: "غداً",
  pickDate: "اختر التاريخ",
  majorOnly: "الدوريات والبطولات الرئيسية فقط",
  majorOnlyHint:
    "يشمل: الدوريات الأوروبية الكبرى، دوري أبطال أوروبا والدوري الأوروبي ومؤتمر أوروبا، كأس العالم وأمم أوروبا وأفريقيا وآسيا وكأس العرب والخليج، كأس العالم للأندية، ليبرتادوريس وسود أمريكانا، الدوريات العربية الكبرى (السعودية، مصر، المغرب، الجزائر، تونس، قطر، الإمارات، العراق)، دوري أبطال أفريقيا والكونفدرالية، وأبرز الكؤوس المحلية",
  close: "إغلاق",
  searchPlaceholder: "ابحث عن فريق أو بطولة...",
  liveCount: "مباريات جارية",
  matchesCount: "مباراة",
  noMatches: "لا توجد مباريات في هذا التاريخ",
  noMatchesHint: "جرّب تاريخاً آخر أو أظهر كل البطولات",
  loadError: "تعذّر جلب البيانات، حاول مرة أخرى",
  retry: "إعادة المحاولة",
  autoRetry: "سيُعاد المحاولة تلقائياً...",
  loading: "جارٍ تحميل البيانات...",
  refreshing: "جارٍ التحديث...",
  lastUpdate: "آخر تحديث",
  autoRefresh: "تحديث تلقائي كل دقيقة",
  autoRefreshIdle: "تحديث تلقائي كل 30 دقيقة",
  liveOn: "بث مباشر",
  liveFallback: "تحديث تلقائي (تعذر البث)",
  events: "الأحداث",
  lineups: "التشكيلة",
  stats: "الإحصائيات",
  notStarted: "لم تبدأ المباراة بعد",
  lineupNotAnnounced: "لم تُعلن التشكيلة بعد",
  noStats: "لا توجد إحصائيات متاحة",
  venue: "الملعب",
  referee: "الحكم",
  round: "الدور",
  firstHalf: "الشوط الأول",
  htLabel: "استراحة",
  ftLabel: "انتهت المباراة",
  ftShort: "انتهت",
  aetShort: "بعد وقت إضافي",
  pensShort: "ركلات الترجيح",
  cancelled: "ملغاة",
  postponed: "مؤجلة",
  live: "جارية",
  liveNow: "مباشر",
  vs: "ضد",
  pens: "ركلات",
  assist: "تمريرة حاسمة",
  missedPenalty: "ركلة جزاء ضائعة",
  ownGoal: "هدف عكسي",
  subIn: "داخل",
  subOut: "خارج",
  manager: "المدرب",
  captain: "ق",
  starters: "التشكيلة الأساسية",
  substitutes: "البدلاء",
  source: "المصدر",
  footer: "فكوورة — نتائج ومواعيد المباريات في مكان واحد",
  goal: "هدف",
  yellowCard: "بطاقة صفراء",
  redCard: "بطاقة حمراء",
  substitution: "تبديل",
  varGoalCancelled: "هدف ملغي (الفار)",
  varGoalConfirmed: "هدف صحيح بعد مراجعة الفار",
  varPenaltyCancelled: "ركلة جزاء ملغاة (الفار)",
  varPenaltyNotAwarded: "لا ركلة جزاء (الفار)",
  varPenaltyAwarded: "ركلة جزاء بعد مراجعة الفار",
  varDecision: "قرار تقنية الفار",
  // competition dialog
  tableTab: "الترتيب",
  roundsTab: "الجولات والنتائج",
  posCol: "#",
  playedCol: "لعب",
  winCol: "فاز",
  drawCol: "تعادل",
  loseCol: "خسر",
  gfCol: "له",
  gaCol: "عليه",
  gdCol: "الفارق",
  pointsCol: "نقاط",
  formCol: "آخر المباريات",
  noStandings: "لا يتوفر ترتيب لهذه البطولة (كأس أو تصفيات)",
  selectRound: "اختر الجولة",
  roundMatchesCount: "مباراة",
  noMatchesInRound: "لا توجد مباريات في هذه الجولة",
  compInfo: "صفحة البطولة",
  winForm: "ف",
  drawForm: "ت",
  loseForm: "خ",
  seasonLabel: "الموسم",
  // team dialog / team page
  teamInfo: "صفحة الفريق",
  matchesTab: "المباريات",
  squadTab: "التشكيلة",
  recentResults: "آخر النتائج",
  upcomingFixtures: "المباريات القادمة",
  noTeamMatches: "لا توجد مباريات لهذا الفريق",
  noSquad: "لا تتوفر قائمة لاعبين لهذا الفريق بعد",
  yearsOld: "سنة",
  // player dialog / player page
  playerInfo: "صفحة اللاعب",
  position: "المركز",
  posGoalkeeper: "حارس",
  posDefender: "مدافع",
  posMidfielder: "وسط",
  posForward: "مهاجم",
  posOther: "لاعب",
  age: "العمر",
  height: "الطول",
  weight: "الوزن",
  nationality: "الجنسية",
  birthDate: "تاريخ الميلاد",
  birthPlace: "مكان الميلاد",
  shirtNumber: "الرقم",
  currentClub: "النادي الحالي",
  career: "المسيرة الاحترافية",
  season: "الموسم",
  appsCol: "مباريات",
  goalsCol: "أهداف",
  assistsCol: "صناعة",
  yellowCol: "صفراء",
  redCol: "حمراء",
  minutesCol: "دقائق",
  loan: "إعارة",
  noCareer: "لا يتوفر سجل مسيرة لهذا اللاعب بعد",
  clubCol: "النادي",
};

const EN: Strings = {
  appTitle: "Fkoora",
  appSubtitle: "Results, live scores & fixtures",
  resultsTitle: "Match results —",
  resultsShort: "Results",
  todayTitle: "Today's matches",
  fixturesTitle: "Match fixtures —",
  fixturesShort: "Fixtures",
  yesterday: "Yesterday",
  today: "Today",
  tomorrow: "Tomorrow",
  pickDate: "Pick a date",
  majorOnly: "Major leagues & cups only",
  majorOnlyHint:
    "Includes: the big-5 European leagues, UEFA Champions/Europa/Conference League, World Cup, Euro, AFCON, Asian Cup, Arab & Gulf Cup, Club World Cup, Libertadores & Sudamericana, the leading Arab leagues (Saudi Arabia, Egypt, Morocco, Algeria, Tunisia, Qatar, UAE, Iraq), CAF Champions & Confederation Cup, and the headline domestic cups",
  close: "Close",
  searchPlaceholder: "Search a team or competition...",
  liveCount: "live now",
  matchesCount: "matches",
  noMatches: "No matches found on this date",
  noMatchesHint: "Try another date or show all competitions",
  loadError: "Could not load the data, please retry",
  retry: "Retry",
  autoRetry: "Retrying automatically...",
  loading: "Loading data...",
  refreshing: "Refreshing...",
  lastUpdate: "Updated",
  autoRefresh: "Auto refresh every minute",
  autoRefreshIdle: "Auto refresh every 30 min",
  liveOn: "Live updates",
  liveFallback: "Auto refresh (live stream unavailable)",
  events: "Events",
  lineups: "Lineups",
  stats: "Stats",
  notStarted: "Match has not started yet",
  lineupNotAnnounced: "Lineup not announced yet",
  noStats: "No stats available",
  venue: "Venue",
  referee: "Referee",
  round: "Round",
  firstHalf: "First half",
  htLabel: "HT",
  ftLabel: "Full time",
  ftShort: "FT",
  aetShort: "AET",
  pensShort: "Penalties",
  cancelled: "Cancelled",
  postponed: "Postponed",
  live: "Live",
  liveNow: "LIVE",
  vs: "vs",
  pens: "pens",
  assist: "Assist",
  missedPenalty: "Penalty missed",
  ownGoal: "Own goal",
  subIn: "In",
  subOut: "Out",
  manager: "Manager",
  captain: "C",
  starters: "Starting XI",
  substitutes: "Substitutes",
  source: "Source",
  footer: "Fkoora — results, live scores & fixtures in one place",
  goal: "Goal",
  yellowCard: "Yellow card",
  redCard: "Red card",
  substitution: "Substitution",
  varGoalCancelled: "Goal cancelled (VAR)",
  varGoalConfirmed: "Goal confirmed by VAR",
  varPenaltyCancelled: "Penalty cancelled (VAR)",
  varPenaltyNotAwarded: "No penalty (VAR)",
  varPenaltyAwarded: "Penalty awarded after VAR review",
  varDecision: "VAR decision",
  // competition dialog
  tableTab: "Table",
  roundsTab: "Rounds & Results",
  posCol: "#",
  playedCol: "P",
  winCol: "W",
  drawCol: "D",
  loseCol: "L",
  gfCol: "GF",
  gaCol: "GA",
  gdCol: "GD",
  pointsCol: "Pts",
  formCol: "Form",
  noStandings: "No table for this competition (cup or qualifier)",
  selectRound: "Select round",
  roundMatchesCount: "matches",
  noMatchesInRound: "No matches in this round",
  compInfo: "Competition page",
  winForm: "W",
  drawForm: "D",
  loseForm: "L",
  seasonLabel: "Season",
  // team dialog / team page
  teamInfo: "Team page",
  matchesTab: "Matches",
  squadTab: "Squad",
  recentResults: "Recent Results",
  upcomingFixtures: "Upcoming Fixtures",
  noTeamMatches: "No matches found for this team",
  noSquad: "No squad list available for this team yet",
  yearsOld: "years old",
  // player dialog / player page
  playerInfo: "Player page",
  position: "Position",
  posGoalkeeper: "Goalkeeper",
  posDefender: "Defender",
  posMidfielder: "Midfielder",
  posForward: "Forward",
  posOther: "Player",
  age: "Age",
  height: "Height",
  weight: "Weight",
  nationality: "Nationality",
  birthDate: "Date of birth",
  birthPlace: "Place of birth",
  shirtNumber: "Shirt no.",
  currentClub: "Current club",
  career: "Career history",
  season: "Season",
  appsCol: "Apps",
  goalsCol: "Goals",
  assistsCol: "Assists",
  yellowCol: "Yellow",
  redCol: "Red",
  minutesCol: "Minutes",
  loan: "Loan",
  noCareer: "No career history available for this player yet",
  clubCol: "Club",
};

export const STRINGS: Record<Lang, Strings> = { ar: AR, en: EN };

export function t(lang: Lang): Strings {
  return STRINGS[lang];
}

/** Pick the best display name for the active language. */
export function nameOf(
  entity: { nameEn?: string | null; nameAr?: string | null } | null | undefined,
  lang: Lang,
): string {
  if (!entity) return "—";
  const pick = lang === "ar" ? entity.nameAr || entity.nameEn : entity.nameEn || entity.nameAr;
  return pick || "—";
}

/** "England — Premier League" / "إنجلترا — الدوري الإنجليزي الممتاز" */
export function compLabel(
  comp: { nameEn?: string | null; nameAr?: string | null; areaNameEn?: string | null; areaNameAr?: string | null } | null | undefined,
  lang: Lang,
): string {
  if (!comp) return "—";
  const name = nameOf(comp, lang);
  let area =
    lang === "ar"
      ? comp.areaNameAr && comp.areaNameAr !== "International"
        ? comp.areaNameAr
        : comp.areaNameEn
      : comp.areaNameEn;
  // provider leaves some area names untranslated - localize the common one
  if (area === "International") area = lang === "ar" ? "دولي" : "International";
  if (area && !name.includes(area)) return `${area} — ${name}`;
  return name;
}

// ---------------------------------------------------------------------------
// dates & times (user's local timezone)
// ---------------------------------------------------------------------------
function localeOf(lang: Lang): string {
  return lang === "ar" ? "ar-MA-u-nu-latn" : "en-GB";
}

export function formatTime(iso: string | null | undefined, lang: Lang): string {
  if (!iso) return "--:--";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "--:--";
  return new Intl.DateTimeFormat(localeOf(lang), {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
}

export function formatDayLong(dateIso: string, lang: Lang): string {
  const d = new Date(dateIso + "T12:00:00Z");
  if (isNaN(d.getTime())) return dateIso;
  return new Intl.DateTimeFormat(localeOf(lang), {
    weekday: "long",
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(d);
}

export function formatDateTime(iso: string | null | undefined, lang: Lang): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat(localeOf(lang), {
    weekday: "long",
    day: "numeric",
    month: "long",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(d);
}

export function localToday(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate(),
  ).padStart(2, "0")}`;
}

export function shiftDate(iso: string, days: number): string {
  const d = new Date(iso + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

// ---------------------------------------------------------------------------
// match status
// ---------------------------------------------------------------------------
export type StatusKind = "live" | "done" | "fixture" | "cancelled";

export interface StatusDisplay {
  main: string;
  sub?: string;
  kind: StatusKind;
}

/** Extract the live minute from a period string like "SECOND_HALF 85" / "FIRST_HALF 31+2". */
export function liveMinute(period: string | null | undefined): string | null {
  if (!period) return null;
  const m = period.match(/(\d+)(\+(\d+))?/);
  if (m) return m[3] ? `${m[1]}+${m[3]}'` : `${m[1]}'`;
  return null;
}

export function statusDisplay(
  m: { status: string; period?: string | null; kickoffUtc?: string | null },
  lang: Lang,
): StatusDisplay {
  const s = t(lang);
  switch (m.status) {
    case "LIVE": {
      const upper = (m.period || "").toUpperCase();
      if (upper.includes("HALF_TIME")) return { main: s.htLabel, sub: s.liveNow, kind: "live" };
      if (upper.includes("PENALTY")) return { main: s.pens, sub: s.liveNow, kind: "live" };
      const minute = liveMinute(m.period);
      return { main: minute || s.live, sub: s.liveNow, kind: "live" };
    }
    case "RESULT":
      return { main: s.ftShort, kind: "done" };
    case "AET":
      return { main: s.aetShort, kind: "done" };
    case "PEN":
      return { main: s.pensShort, kind: "done" };
    case "CANCELLED":
      return { main: s.cancelled, kind: "cancelled" };
    case "POSTPONED":
      return { main: s.postponed, kind: "cancelled" };
    default:
      return { main: formatTime(m.kickoffUtc, lang), kind: "fixture" };
  }
}

// ---------------------------------------------------------------------------
// stat labels (provider types -> bilingual labels)
// ---------------------------------------------------------------------------
const STAT_LABELS: Record<string, [string, string]> = {
  POSSESSION: ["الاستحواذ", "Possession"],
  EXPECTED_GOAL: ["الأهداف المتوقعة", "Expected goals"],
  SHOT_TOTAL: ["إجمالي التسديدات", "Total shots"],
  SHOT_ON_TARGET: ["تسديدات على المرمى", "Shots on target"],
  SHOT_OFF_TARGET: ["تسديدات خارج المرمى", "Shots off target"],
  BLOCKED_SHOT: ["تسديدات محظورة", "Blocked shots"],
  CORNER_TOTAL: ["الركنيات", "Corners"],
  FOUL_COMMITED: ["الأخطاء", "Fouls"],
  OFFSIDE_TOTAL: ["التسلل", "Offsides"],
  THROW_IN: ["رميات تماس", "Throw-ins"],
  GOALKEEPER_SAVE: ["تصديات الحارس", "Goalkeeper saves"],
  YELLOW_CARD: ["البطاقات الصفراء", "Yellow cards"],
  RED_CARD: ["البطاقات الحمراء", "Red cards"],
  PASS_ACCURACY: ["دقة التمرير", "Pass accuracy"],
  TOTAL_PASSES: ["إجمالي التمريرات", "Total passes"],
  ACCURATE_PASSES: ["تمريرات صحيحة", "Accurate passes"],
  CROSS: ["العرضيات", "Crosses"],
  BIG_CHANCE: ["الفرص المحققة", "Big chances"],
};

export function statLabel(statType: string, lang: Lang): string {
  const entry = STAT_LABELS[statType];
  if (entry) return lang === "ar" ? entry[0] : entry[1];
  return statType
    .toLowerCase()
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

export function statPercent(statType: string, value: number | string): boolean {
  return statType === "POSSESSION" || String(value).includes("%");
}

// ---------------------------------------------------------------------------
// player/team display helpers
// ---------------------------------------------------------------------------

/** Localized position label ("حارس" / "Goalkeeper") for the provider enum. */
export function positionLabel(
  position: string | null | undefined,
  lang: Lang,
): string | null {
  if (!position) return null;
  const s = t(lang);
  switch (position.toUpperCase()) {
    case "GOALKEEPER":
      return s.posGoalkeeper;
    case "DEFENDER":
      return s.posDefender;
    case "MIDFIELDER":
      return s.posMidfielder;
    case "FORWARD":
      return s.posForward;
    default:
      return s.posOther;
  }
}

/** "1.82 م" / "1.82 m" (null when unknown). */
export function heightLabel(cm: number | null | undefined, lang: Lang): string | null {
  if (!cm || cm <= 0) return null;
  const m = (cm / 100).toFixed(2);
  return lang === "ar" ? `${m} م` : `${m} m`;
}

/** "76 كغ" / "76 kg" (null when unknown). */
export function weightLabel(kg: number | null | undefined, lang: Lang): string | null {
  if (!kg || kg <= 0) return null;
  return lang === "ar" ? `${kg} كغ` : `${kg} kg`;
}

/** "30 أغسطس 1998" / "30 August 1998" (null when unknown). */
export function birthDateLabel(
  iso: string | null | undefined,
  lang: Lang,
): string | null {
  if (!iso) return null;
  const d = new Date(`${iso}T12:00:00Z`);
  if (isNaN(d.getTime())) return null;
  return new Intl.DateTimeFormat(localeOf(lang), {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  }).format(d);
}
