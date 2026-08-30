#!/usr/bin/env node
/**
 * Mock of the Python scraper backend (Flask API) for local testing of the
 * Fkoora frontend. Serves on :9000 (what FOOTBALL_API_BASE defaults to).
 *
 * Endpoints (all JSON, strong ETags + 304 support):
 *   GET /api/matches?date&today&major&tz          - bilingual day listing
 *   GET /api/match/:id                            - match detail (events,
 *                                                   lineups, stats)
 *   GET /api/competition/:id                      - standings + rounds
 *   GET /api/competition/:id/matches?gameset      - one round's matches
 *
 * Sample data covers every URL/SEO case:
 *   m1  Real Madrid vs Bayern München     (UCL, LIVE)      - accented Latin
 *   m2  Al Hilal vs Al Nassr              (Saudi league)   - Arabic-first
 *   m3  Chelsea vs Brighton & Hove Albion (EPL, RESULT)    - "&" in the name,
 *         the user's real example id KmnxUMTh30bqzp9LEGdDS
 *   m4  Barcelona vs Sevilla              (LaLiga, FIXTURE)
 *   m6  Botafogo vs Palmeiras             (Libertadores)   - NO Arabic names
 *         -> AR/EN slug collision -> "?lang=en" variant URL
 *   m5  Cruzeiro vs Atlético Mineiro      (Copa do Brasil) - minor (major=0
 *         filter removes it from the sitemap)
 */
"use strict";

const http = require("http");
const crypto = require("crypto");

const PORT = Number(process.env.PORT || 9000);

// ---------------------------------------------------------------------------
// reference data (bilingual)
// ---------------------------------------------------------------------------

const COMPS = {
  ucl1: {
    id: "ucl1",
    nameEn: "UEFA Champions League",
    nameAr: "دوري أبطال أوروبا",
    areaNameEn: "Europe",
    areaNameAr: "أوروبا",
    areaCode: "EU",
  },
  spl2: {
    id: "spl2",
    nameEn: "Saudi Pro League",
    nameAr: "دوري روشن السعودي",
    areaNameEn: "Saudi Arabia",
    areaNameAr: "السعودية",
    areaCode: "SA",
  },
  epl3: {
    id: "epl3",
    nameEn: "Premier League",
    nameAr: "الدوري الإنجليزي الممتاز",
    areaNameEn: "England",
    areaNameAr: "إنجلترا",
    areaCode: "EN",
  },
  lal5: {
    id: "lal5",
    nameEn: "LaLiga",
    nameAr: "الدوري الإسباني",
    areaNameEn: "Spain",
    areaNameAr: "إسبانيا",
    areaCode: "ES",
  },
  lib7: {
    id: "lib7",
    nameEn: "CONMEBOL Libertadores",
    nameAr: "كوبا ليبرتادوريس",
    areaNameEn: "South America",
    areaNameAr: "أمريكا الجنوبية",
    areaCode: "SA",
  },
  ccf4: {
    id: "ccf4",
    nameEn: "Copa do Brasil",
    nameAr: "كأس البرازيل",
    areaNameEn: "Brazil",
    areaNameAr: "البرازيل",
    areaCode: "BR",
  },
};

function team(id, nameEn, nameAr, code) {
  return { id, nameEn, nameAr, code, crestUrl: null };
}

const TEAMS = {
  real: team("t-real", "Real Madrid", "ريال مدريد", "RMA"),
  bayern: team("t-bayern", "Bayern München", "بايرن ميونخ", "BAY"),
  hilal: team("t-hilal", "Al Hilal", "الهلال", "HIL"),
  nassr: team("t-nassr", "Al Nassr", "النصر", "NAS"),
  chelsea: team("t-chelsea", "Chelsea", "تشيلسي", "CHE"),
  brighton: team("t-brighton", "Brighton & Hove Albion", "برايتون أند هوف ألبيون", "BHA"),
  barca: team("t-barca", "Barcelona", "برشلونة", "BAR"),
  sevilla: team("t-sevilla", "Sevilla", "إشبيلية", "SEV"),
  botafogo: team("t-botafogo", "Botafogo", null, "BOT"),
  palmeiras: team("t-palmeiras", "Palmeiras", null, "PAL"),
  cruzeiro: team("t-cruzeiro", "Cruzeiro", "كروزيرو", "CRU"),
  atletico: team("t-atletico", "Atlético Mineiro", "أتلتيكو مينيرو", "CAM"),
  arsenal: team("t-arsenal", "Arsenal", "أرسنال", "ARS"),
  liverpool: team("t-liverpool", "Liverpool", "ليفربول", "LIV"),
  city: team("t-city", "Manchester City", "مانشستر سيتي", "MCI"),
  united: team("t-united", "Manchester United", "مانشستر يونايتد", "MUN"),
  newcastle: team("t-newcastle", "Newcastle United", "نيوكاسل يونايتد", "NEW"),
  villa: team("t-villa", "Aston Villa", "أستون فيلا", "AVL"),
  spurs: team("t-spurs", "Tottenham Hotspur", "توتنهام", "TOT"),
  westham: team("t-westham", "West Ham United", "وست هام", "WHU"),
  everton: team("t-everton", "Everton", "إيفرتون", "EVE"),
  forest: team("t-forest", "Nottingham Forest", "نوتنغهام فورست", "NFO"),
  bournemouth: team("t-bournemouth", "Bournemouth", "بورنموث", "BOU"),
  fulham: team("t-fulham", "Fulham", "فولهام", "FUL"),
  brentford: team("t-brentford", "Brentford", "برينتفورد", "BRE"),
  palace: team("t-palace", "Crystal Palace", "كريستال بالاس", "CRY"),
  wolves: team("t-wolves", "Wolverhampton", "وولفرهامبتون", "WOL"),
  leicester: team("t-leicester", "Leicester City", "ليستر سيتي", "LEI"),
  ipswich: team("t-ipswich", "Ipswich Town", "إبسويتش تاون", "IPS"),
  saints: team("t-saints", "Southampton", "ساوثهامبتون", "SOU"),
};

// ---------------------------------------------------------------------------
// time helpers - kickoff times relative to NOW so LIVE matches stay live
// ---------------------------------------------------------------------------

function hoursAgo(h) {
  return new Date(Date.now() - h * 3600_000).toISOString();
}
function hoursAhead(h) {
  return new Date(Date.now() + h * 3600_000).toISOString();
}

// ---------------------------------------------------------------------------
// matches
// ---------------------------------------------------------------------------

/** base rows; status/kickoff are adjusted per requested day below */
function baseMatches(nowIso, dayType) {
  const isPast = dayType === "past";
  const isFuture = dayType === "future";

  const rows = [
    {
      matchId: "m1liveucl",
      kickoffUtc: isPast ? hoursAgo(24 + 2) : isFuture ? hoursAhead(24 + 3) : hoursAgo(1.2),
      status: isPast ? "RESULT" : isFuture ? "FIXTURE" : "LIVE",
      period: isPast || isFuture ? null : "LIVE 63",
      homeTeam: TEAMS.real,
      awayTeam: TEAMS.bayern,
      competition: COMPS.ucl1,
      homeScore: isPast ? 3 : isFuture ? null : 2,
      awayScore: isPast ? 1 : isFuture ? null : 1,
      homeRedCards: 0,
      awayRedCards: 0,
      roundName: "Quarter-finals",
      gamesetName: "Quarter-finals",
      gamesetNameAr: "ربع النهائي",
      venueNameEn: "Santiago Bernabéu",
      venueNameAr: "سانتياغو برنابيو",
    },
    {
      matchId: "m2riyadhderby",
      kickoffUtc: isPast ? hoursAgo(24 + 3) : isFuture ? hoursAhead(24 + 5) : hoursAgo(1),
      status: isPast ? "RESULT" : isFuture ? "FIXTURE" : "LIVE",
      period: isPast || isFuture ? null : "HT",
      homeTeam: TEAMS.hilal,
      awayTeam: TEAMS.nassr,
      competition: COMPS.spl2,
      homeScore: isPast ? 2 : isFuture ? null : 1,
      awayScore: isPast ? 0 : isFuture ? null : 1,
      homeRedCards: 0,
      awayRedCards: 0,
      roundName: "Round 24",
      gamesetName: "Round 24",
      gamesetNameAr: "الجولة 24",
      venueNameEn: "King Fahd Stadium",
      venueNameAr: "ملعب الملك فهد",
    },
    {
      // the user's real-world example id
      matchId: "KmnxUMTh30bqzp9LEGdDS",
      kickoffUtc: isPast ? hoursAgo(24 + 5) : isFuture ? hoursAhead(24 + 2) : hoursAgo(4),
      status: "RESULT",
      period: null,
      homeTeam: TEAMS.chelsea,
      awayTeam: TEAMS.brighton,
      competition: COMPS.epl3,
      homeScore: 2,
      awayScore: 0,
      homeRedCards: 0,
      awayRedCards: 1,
      roundName: "Round 28",
      gamesetName: "Round 28",
      gamesetNameAr: "الجولة 28",
      venueNameEn: "Stamford Bridge",
      venueNameAr: "ستامفورد بريدج",
    },
    {
      matchId: "m4barcafixture",
      kickoffUtc: isPast ? hoursAgo(24 + 1) : isFuture ? hoursAhead(24 + 4) : hoursAhead(3),
      status: isPast ? "RESULT" : "FIXTURE",
      period: null,
      homeTeam: TEAMS.barca,
      awayTeam: TEAMS.sevilla,
      competition: COMPS.lal5,
      homeScore: isPast ? 2 : null,
      awayScore: isPast ? 2 : null,
      homeRedCards: 0,
      awayRedCards: 0,
      roundName: "Round 29",
      gamesetName: "Round 29",
      gamesetNameAr: "الجولة 29",
      venueNameEn: "Estadi Olímpic Lluís Companys",
      venueNameAr: "الملعب الأولمبي لويس كومبانيس",
    },
    {
      // no Arabic team names -> AR and EN slugs collide -> "?lang=en" variant
      matchId: "m6nocoast",
      kickoffUtc: isPast ? hoursAgo(24 + 6) : isFuture ? hoursAhead(24 + 6) : hoursAgo(6),
      status: "RESULT",
      period: null,
      homeTeam: TEAMS.botafogo,
      awayTeam: TEAMS.palmeiras,
      competition: COMPS.lib7,
      homeScore: 1,
      awayScore: 1,
      homeRedCards: 0,
      awayRedCards: 0,
      roundName: "Group Stage",
      gamesetName: "Group Stage",
      gamesetNameAr: "دور المجموعات",
      venueNameEn: "Estádio Nilton Santos",
      venueNameAr: null,
    },
    {
      // minor competition: excluded from the sitemap when major=1
      matchId: "m5minorcup",
      kickoffUtc: isPast ? hoursAgo(24 + 4) : isFuture ? hoursAhead(24 + 1) : hoursAgo(2),
      status: isPast ? "RESULT" : isFuture ? "FIXTURE" : "RESULT",
      period: null,
      homeTeam: TEAMS.cruzeiro,
      awayTeam: TEAMS.atletico,
      competition: COMPS.ccf4,
      homeScore: 1,
      awayScore: 0,
      homeRedCards: 0,
      awayRedCards: 0,
      roundName: "Round of 16",
      gamesetName: "Round of 16",
      gamesetNameAr: "دور الـ16",
      venueNameEn: "Mineirão",
      venueNameAr: "مينيراو",
    },
  ];

  return rows;
}

const MAJOR_IDS = new Set([
  "m1liveucl",
  "m2riyadhderby",
  "KmnxUMTh30bqzp9LEGdDS",
  "m4barcafixture",
  "m6nocoast",
]);

// ---------------------------------------------------------------------------
// match details
// ---------------------------------------------------------------------------

function detail(base) {
  return {
    matchId: base.matchId,
    kickoffUtc: base.kickoffUtc,
    status: base.status,
    period: base.period ?? null,
    homeTeam: base.homeTeam,
    awayTeam: base.awayTeam,
    homeScore: base.homeScore,
    awayScore: base.awayScore,
    homeScoreHt: base.status === "RESULT" ? Math.floor((base.homeScore || 0) / 2) : null,
    awayScoreHt: base.status === "RESULT" ? Math.floor((base.awayScore || 0) / 2) : null,
    competition: base.competition,
    roundName: base.roundName,
    seasonName: "2025/2026",
    venueNameEn: base.venueNameEn,
    venueNameAr: base.venueNameAr,
    referee: base.status === "FIXTURE" ? null : "Sandro Meira Ricci",
    events: buildEvents(base),
    lineups: {
      confirmed: base.status !== "FIXTURE",
      home: lineup(base.homeTeam, "4-3-3"),
      away: lineup(base.awayTeam, "4-2-3-1"),
    },
    stats: [
      { statType: "POSSESSION", homeValue: "58%", awayValue: "42%" },
      { statType: "SHOTS", homeValue: 14, awayValue: 9 },
      { statType: "SHOTS_ON_TARGET", homeValue: 6, awayValue: 3 },
      { statType: "CORNERS", homeValue: 7, awayValue: 4 },
      { statType: "FOULS", homeValue: 11, awayValue: 13 },
    ],
  };
}

function buildEvents(m) {
  if (m.status === "FIXTURE") return [];
  const ev = [];
  const hs = m.homeScore || 0;
  const as = m.awayScore || 0;
  for (let i = 0; i < hs; i++) {
    ev.push({
      teamSide: "home",
      eventType: "GOAL",
      minute: 12 + i * 34,
      extraMinute: null,
      player: { id: `p-h${i}`, nameEn: "Home Scorer", nameAr: "مهاجم الفريق المضيف" },
      relatedPlayer: { id: `p-ha${i}`, nameEn: "Home Assist", nameAr: "صانع الهدف" },
      homeScoreAfter: i + 1,
      awayScoreAfter: Math.min(i, as),
    });
  }
  for (let i = 0; i < as; i++) {
    ev.push({
      teamSide: "away",
      eventType: "GOAL",
      minute: 27 + i * 41,
      extraMinute: null,
      player: { id: `p-a${i}`, nameEn: "Away Scorer", nameAr: "مهاجم الفريق الضيف" },
      relatedPlayer: { id: `p-aa${i}`, nameEn: "Away Assist", nameAr: "صانع الهدف" },
      homeScoreAfter: Math.min(i + 1, hs),
      awayScoreAfter: i + 1,
    });
  }
  ev.push({
    teamSide: "away",
    eventType: "YELLOW_CARD",
    minute: 66,
    extraMinute: null,
    player: { id: "p-yc", nameEn: "Away Defender", nameAr: "مدافع الفريق الضيف" },
    relatedPlayer: { id: null, nameEn: null, nameAr: null },
    homeScoreAfter: m.homeScore,
    awayScoreAfter: m.awayScore,
  });
  return ev.sort((a, b) => (a.minute || 0) - (b.minute || 0));
}

function lineup(t, formation) {
  const entries = [];
  for (let i = 1; i <= 11; i++) {
    entries.push({
      person: { id: `${t.id}-p${i}`, nameEn: `${t.nameEn || t.id} Player ${i}`, nameAr: t.nameAr ? `${t.nameAr} لاعب ${i}` : null },
      isStarter: i <= 11,
      shirtNumber: i,
      isCaptain: i === 1,
      positionX: (i % 4) + 1,
      positionY: (i % 5) + 1,
      rating: 6.5 + (i % 3),
    });
  }
  return {
    teamId: t.id,
    formation,
    manager: { id: `${t.id}-mgr`, nameEn: "The Manager", nameAr: "المدير الفني" },
    entries,
  };
}

// ---------------------------------------------------------------------------
// team profile (recent results + upcoming fixtures + squad)
// ---------------------------------------------------------------------------

/** position bucket by shirt number (1 GK, 2-5 DEF, 6-9 MID, 10+ FWD) */
function squadPosition(i) {
  if (i === 1) return "GOALKEEPER";
  if (i <= 5) return "DEFENDER";
  if (i <= 9) return "MIDFIELDER";
  return "FORWARD";
}

function teamInfo(teamId) {
  const t = Object.values(TEAMS).find((x) => x.id === teamId);
  if (!t) return null;

  const rows = baseMatches(new Date().toISOString(), "today");
  const played = rows.filter(
    (m) =>
      (m.homeTeam.id === teamId || m.awayTeam.id === teamId) &&
      (m.status === "RESULT" || m.status === "LIVE"),
  );
  const upcoming = rows.filter(
    (m) =>
      (m.homeTeam.id === teamId || m.awayTeam.id === teamId) &&
      m.status === "FIXTURE",
  );

  const squad = [];
  for (let i = 1; i <= 14; i++) {
    squad.push({
      id: `${t.id}-p${i}`,
      nameEn: `${t.nameEn || t.id} Player ${i}`,
      nameAr: t.nameAr ? `${t.nameAr} لاعب ${i}` : null,
      position: i <= 11 ? squadPosition(i) : null,
      shirtNumber: i,
      imageUrl: null,
    });
  }

  return {
    team: t,
    recentMatches: played,
    upcomingMatches: upcoming,
    squad,
    generatedAt: new Date().toISOString(),
  };
}

// ---------------------------------------------------------------------------
// player profile (bio + career history)
// ---------------------------------------------------------------------------

function playerDetail(playerId) {
  // lineup players look like "t-real-p7" -> derive the club from the id
  const m = /^(t-[a-z]+)-p(\d+)$/.exec(playerId);
  if (!m) {
    // event players (p-h0, p-a1, p-yc, ...) - a bare profile, no club
    return {
      player: {
        id: playerId,
        nameEn: "Unknown Player",
        nameAr: "لاعب غير معروف",
        fullNameEn: null,
        fullNameAr: null,
        imageUrl: null,
        position: null,
        shirtNumber: null,
        heightCm: null,
        weightKg: null,
        birthDate: null,
        age: null,
        nationalityEn: null,
        nationalityAr: null,
        placeOfBirthEn: null,
        placeOfBirthAr: null,
      },
      currentClub: null,
      career: [],
      profileFetched: true,
      generatedAt: new Date().toISOString(),
    };
  }

  const teamId = m[1];
  const shirt = Number(m[2]);
  const t = Object.values(TEAMS).find((x) => x.id === teamId);
  const club = t || null;

  const career = [];
  if (club) {
    career.push({
      team: club,
      seasonName: "2025/2026",
      competition: {
        id: COMPS.ucl1.id,
        nameEn: COMPS.ucl1.nameEn,
        nameAr: COMPS.ucl1.nameAr,
      },
      appearances: 18 + (shirt % 7),
      goals: shirt % 5,
      assists: shirt % 3,
      yellowCards: shirt % 4,
      redCards: shirt % 11 === 0 ? 1 : 0,
      minutesPlayed: 1450 + shirt * 37,
      isLoan: false,
    });
    career.push({
      team: club,
      seasonName: "2024/2025",
      competition: {
        id: COMPS.epl3.id,
        nameEn: COMPS.epl3.nameEn,
        nameAr: COMPS.epl3.nameAr,
      },
      appearances: 24 + (shirt % 9),
      goals: (shirt % 5) + 1,
      assists: shirt % 4,
      yellowCards: shirt % 3,
      redCards: 0,
      minutesPlayed: 1980 + shirt * 41,
      isLoan: shirt % 6 === 0,
    });
  }

  return {
    player: {
      id: playerId,
      nameEn: club ? `${club.nameEn || club.id} Player ${shirt}` : "Unknown Player",
      nameAr: club && club.nameAr ? `${club.nameAr} لاعب ${shirt}` : null,
      fullNameEn: club ? `${club.nameEn || club.id} Player The ${shirt}th` : null,
      fullNameAr: null,
      imageUrl: null,
      position: shirt <= 11 ? squadPosition(shirt) : null,
      shirtNumber: shirt,
      heightCm: 172 + (shirt % 12),
      weightKg: 68 + (shirt % 10),
      birthDate: `199${shirt % 10}-0${(shirt % 9) + 1}-1${shirt % 9}`,
      age: 22 + (shirt % 9),
      nationalityEn: "Brazil",
      nationalityAr: "البرازيل",
      placeOfBirthEn: "São Paulo",
      placeOfBirthAr: "ساو باولو",
    },
    currentClub: club,
    career,
    profileFetched: true,
    generatedAt: new Date().toISOString(),
  };
}

// ---------------------------------------------------------------------------
// competition info (standings + gamesets)
// ---------------------------------------------------------------------------

function row(pos, t, played, w, d, l, gf, ga, pts, markers, form) {
  return {
    position: pos,
    team: t,
    played, win: w, draw: d, lose: l,
    goalsFor: gf, goalsAgainst: ga,
    goalDiff: gf - ga,
    points: pts,
    form: (form || "WDLW").split("").map((x, i) => ({
      wdl: x === "W" ? "WIN" : x === "D" ? "DRAW" : "LOSS",
      matchId: `form-${t.id}-${i}`,
    })),
    markers: markers || [],
  };
}

const EPL_STANDINGS = {
  tables: [
    {
      name: null,
      rows: [
        row(1, TEAMS.liverpool, 28, 20, 5, 3, 62, 27, 65, ["UCL"], "WWWDW"),
        row(2, TEAMS.arsenal, 28, 18, 6, 4, 55, 24, 60, ["UCL"], "WDWWW"),
        row(3, TEAMS.city, 28, 17, 7, 4, 60, 30, 58, ["UCL"], "WWDWD"),
        row(4, TEAMS.chelsea, 28, 15, 7, 6, 51, 34, 52, ["UCL"], "WLWDW"),
        row(5, TEAMS.newcastle, 28, 14, 6, 8, 45, 38, 48, ["UEL"], "WWWLD"),
        row(6, TEAMS.villa, 28, 13, 8, 7, 43, 39, 47, ["UECL"], "DWDWW"),
        row(7, TEAMS.spurs, 28, 12, 6, 10, 48, 42, 42, [], "LWWLD"),
        row(8, TEAMS.united, 28, 11, 8, 9, 40, 38, 41, [], "DWLDW"),
        row(9, TEAMS.westham, 28, 10, 7, 11, 36, 44, 37, [], "LDWDL"),
        row(10, TEAMS.brighton, 28, 9, 9, 10, 38, 40, 36, [], "DDLWL"),
        row(11, TEAMS.everton, 28, 8, 11, 9, 31, 35, 35, [], "DDLDW"),
        row(12, TEAMS.fulham, 28, 9, 6, 13, 35, 45, 33, [], "LWLDL"),
        row(13, TEAMS.brentford, 28, 8, 8, 12, 40, 47, 32, [], "WLLDW"),
        row(14, TEAMS.forest, 28, 7, 9, 12, 30, 42, 30, [], "LDLWD"),
        row(15, TEAMS.bournemouth, 28, 7, 8, 13, 33, 44, 29, [], "DLLWL"),
        row(16, TEAMS.palace, 28, 6, 10, 12, 29, 41, 28, [], "DDLDL"),
        row(17, TEAMS.wolves, 28, 6, 8, 14, 32, 50, 26, [], "LWLDL"),
        row(18, TEAMS.leicester, 28, 5, 7, 16, 27, 55, 22, ["RELEGATION"], "LLDLL"),
        row(19, TEAMS.ipswich, 28, 3, 9, 16, 22, 58, 18, ["RELEGATION"], "LDLLL"),
        row(20, TEAMS.saints, 28, 2, 8, 18, 19, 60, 14, ["RELEGATION"], "LLLDL"),
      ],
    },
  ],
  markers: [
    { id: "UCL", nameEn: "Champions League", nameAr: "دوري أبطال أوروبا", type: "PROMOTION" },
    { id: "UEL", nameEn: "Europa League", nameAr: "الدوري الأوروبي", type: "PROMOTION" },
    { id: "UECL", nameEn: "Conference League", nameAr: "دوري المؤتمرات", type: "PROMOTION" },
    { id: "RELEGATION", nameEn: "Relegation", nameAr: "الهبوط", type: "RELEGATION" },
  ],
};

const UCL_STANDINGS = {
  tables: [
    {
      name: null,
      rows: [
        row(1, TEAMS.real, 8, 6, 1, 1, 19, 8, 19, [], "WWWDW"),
        row(2, TEAMS.bayern, 8, 5, 2, 1, 17, 9, 17, [], "WWDWW"),
        row(3, TEAMS.city, 8, 5, 1, 2, 15, 9, 16, [], "WWLWD"),
        row(4, TEAMS.barca, 8, 4, 2, 2, 12, 9, 14, [], "WDWLW"),
      ],
    },
  ],
  markers: [],
};

function gamesetsFor(compId) {
  if (compId === "epl3") {
    return [
      { gameSetTypeId: "gs-epl-27", nameEn: "Round 27", nameAr: "الجولة 27", isActive: false, sortOrder: 27, matchCount: 10 },
      { gameSetTypeId: "gs-epl-28", nameEn: "Round 28", nameAr: "الجولة 28", isActive: true, sortOrder: 28, matchCount: 10 },
      { gameSetTypeId: "gs-epl-29", nameEn: "Round 29", nameAr: "الجولة 29", isActive: false, sortOrder: 29, matchCount: 10 },
    ];
  }
  if (compId === "ucl1") {
    return [
      { gameSetTypeId: "gs-ucl-r16", nameEn: "Round of 16", nameAr: "دور الـ16", isActive: false, sortOrder: 1, matchCount: 8 },
      { gameSetTypeId: "gs-ucl-qf", nameEn: "Quarter-finals", nameAr: "ربع النهائي", isActive: true, sortOrder: 2, matchCount: 8 },
      { gameSetTypeId: "gs-ucl-sf", nameEn: "Semi-finals", nameAr: "نصف النهائي", isActive: false, sortOrder: 3, matchCount: 4 },
    ];
  }
  return [
    { gameSetTypeId: `gs-${compId}-1`, nameEn: "Round 1", nameAr: "الجولة 1", isActive: true, sortOrder: 1, matchCount: 8 },
    { gameSetTypeId: `gs-${compId}-2`, nameEn: "Round 2", nameAr: "الجولة 2", isActive: false, sortOrder: 2, matchCount: 8 },
  ];
}

function compInfo(compId) {
  const c = COMPS[compId];
  if (!c) return null;
  const standings = compId === "epl3" ? EPL_STANDINGS : compId === "ucl1" ? UCL_STANDINGS : null;
  return {
    competition: c,
    season: { id: `season-${compId}-26`, name: "2025/2026" },
    standings,
    gamesets: gamesetsFor(compId),
    generatedAt: new Date().toISOString(),
    refreshing: false,
  };
}

// ---------------------------------------------------------------------------
// one round's matches
// ---------------------------------------------------------------------------

function compRoundMatches(compId, gamesetId) {
  const c = COMPS[compId];
  if (!c) return null;
  const gs = gamesetsFor(compId).find(
    (g) => !gamesetId || g.gameSetTypeId === gamesetId,
  ) || gamesetsFor(compId).find((g) => g.isActive);

  let matches;
  if (compId === "epl3" && gs.gameSetTypeId === "gs-epl-28") {
    matches = [
      matchRow("KmnxUMTh30bqzp9LEGdDS", TEAMS.chelsea, TEAMS.brighton, c, "RESULT", 2, 0, hoursAgo(4), "Round 28"),
      matchRow("epl-ars-liv", TEAMS.arsenal, TEAMS.liverpool, c, "RESULT", 1, 1, hoursAgo(3), "Round 28"),
      matchRow("epl-mci-new", TEAMS.city, TEAMS.newcastle, c, "LIVE", 2, 1, hoursAgo(1), "Round 28"),
      matchRow("epl-tot-whu", TEAMS.spurs, TEAMS.westham, c, "FIXTURE", null, null, hoursAhead(2), "Round 28"),
    ];
  } else if (compId === "ucl1" && gs.gameSetTypeId === "gs-ucl-qf") {
    matches = [
      matchRow("m1liveucl", TEAMS.real, TEAMS.bayern, c, "LIVE", 2, 1, hoursAgo(1.2), "Quarter-finals"),
      matchRow("ucl-bar-sev", TEAMS.barca, TEAMS.sevilla, c, "FIXTURE", null, null, hoursAhead(3), "Quarter-finals"),
    ];
  } else {
    matches = [
      matchRow(`${compId}-demo-1`, TEAMS.chelsea, TEAMS.arsenal, c, "RESULT", 1, 0, hoursAgo(5), gs.nameEn),
      matchRow(`${compId}-demo-2`, TEAMS.liverpool, TEAMS.city, c, "FIXTURE", null, null, hoursAhead(4), gs.nameEn),
    ];
  }

  return { gameset: gs, competition: c, matches, refreshing: false };
}

function matchRow(matchId, home, away, comp, status, hs, as, kickoff, roundName) {
  return {
    matchId,
    kickoffUtc: kickoff,
    status,
    period: status === "LIVE" ? "LIVE 63" : null,
    homeTeam: home,
    awayTeam: away,
    competition: comp,
    homeScore: hs,
    awayScore: as,
    homeRedCards: 0,
    awayRedCards: 0,
    roundName,
    gamesetName: roundName,
    gamesetNameAr: roundName === "Quarter-finals" ? "ربع النهائي" : null,
  };
}

// ---------------------------------------------------------------------------
// HTTP plumbing (ETag + 304 like the real Flask backend)
// ---------------------------------------------------------------------------

function sendJson(req, res, status, body) {
  const payload = JSON.stringify(body);
  const etag = `"${crypto.createHash("sha1").update(payload).digest("hex").slice(0, 16)}"`;
  const headers = {
    "Content-Type": "application/json; charset=utf-8",
    ETag: etag,
    "Cache-Control": "no-cache",
  };
  if (req.headers["if-none-match"] === etag) {
    res.writeHead(304, headers);
    res.end();
    return;
  }
  res.writeHead(status, headers);
  res.end(payload);
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://127.0.0.1:${PORT}`);
  const path = decodeURIComponent(url.pathname);
  const log = () => console.log(`${new Date().toISOString()} ${req.method} ${url.pathname}${url.search} -> ${res.statusCode}`);

  if (req.method !== "GET") {
    res.writeHead(405, { "Content-Type": "application/json" });
    res.end('{"error":"method not allowed"}');
    return;
  }

  // ----- day listing -------------------------------------------------------
  if (path === "/api/matches") {
    const today = url.searchParams.get("today") || new Date().toISOString().slice(0, 10);
    const date = url.searchParams.get("date") || today;
    const major = url.searchParams.get("major") !== "0";
    const dayType = date === today ? "today" : date < today ? "past" : "future";

    let rows = baseMatches(new Date().toISOString(), dayType);
    if (major) rows = rows.filter((m) => MAJOR_IDS.has(m.matchId));

    const groups = [];
    for (const m of rows) {
      let g = groups.find((x) => x.competition.id === m.competition.id);
      if (!g) {
        g = { competition: m.competition, matches: [], isMajor: MAJOR_IDS.has(m.matchId) };
        groups.push(g);
      }
      g.matches.push(m);
    }

    const body = {
      date,
      dayType,
      generatedAt: new Date().toISOString(),
      totalMatches: rows.length,
      groups,
    };
    sendJson(req, res, 200, body);
    log();
    return;
  }

  // ----- match detail ------------------------------------------------------
  const matchMatch = /^\/api\/match\/([^/]+)$/.exec(path);
  if (matchMatch) {
    const id = matchMatch[1];
    const nowDay = "today";
    const base = baseMatches(new Date().toISOString(), nowDay).find((m) => m.matchId === id);
    if (!base) {
      sendJson(req, res, 404, { error: `match ${id} not found` });
      log();
      return;
    }
    sendJson(req, res, 200, detail(base));
    log();
    return;
  }

  // ----- team profile -------------------------------------------------------
  const teamMatch = /^\/api\/team\/([^/]+)$/.exec(path);
  if (teamMatch) {
    const info = teamInfo(teamMatch[1]);
    if (!info) {
      sendJson(req, res, 404, { error: `team ${teamMatch[1]} not found` });
      log();
      return;
    }
    sendJson(req, res, 200, info);
    log();
    return;
  }

  // ----- player profile -----------------------------------------------------
  const playerMatch = /^\/api\/player\/([^/]+)$/.exec(path);
  if (playerMatch) {
    sendJson(req, res, 200, playerDetail(playerMatch[1]));
    log();
    return;
  }

  // ----- competition info --------------------------------------------------
  const compMatch = /^\/api\/competition\/([^/]+)$/.exec(path);
  if (compMatch) {
    const info = compInfo(compMatch[1]);
    if (!info) {
      sendJson(req, res, 404, { error: `competition ${compMatch[1]} not found` });
      log();
      return;
    }
    sendJson(req, res, 200, info);
    log();
    return;
  }

  // ----- competition round matches ------------------------------------------
  const compRoundMatch = /^\/api\/competition\/([^/]+)\/matches$/.exec(path);
  if (compRoundMatch) {
    const gameset = url.searchParams.get("gameset");
    const payload = compRoundMatches(compRoundMatch[1], gameset);
    if (!payload) {
      sendJson(req, res, 404, { error: `competition ${compRoundMatch[1]} not found` });
      log();
      return;
    }
    sendJson(req, res, 200, payload);
    log();
    return;
  }

  res.writeHead(404, { "Content-Type": "application/json" });
  res.end('{"error":"not found"}');
  log();
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`mock scraper backend listening on http://127.0.0.1:${PORT}`);
  console.log("  GET /api/matches?date=&today=&major=&tz=");
  console.log("  GET /api/match/:id            (m1liveucl, m2riyadhderby, KmnxUMTh30bqzp9LEGdDS, m4barcafixture, m6nocoast, m5minorcup)");
  console.log("  GET /api/competition/:id      (ucl1, spl2, epl3, lal5, lib7, ccf4)");
  console.log("  GET /api/competition/:id/matches?gameset=");
  console.log("  GET /api/team/:id             (t-real, t-bayern, t-hilal, ...)");
  console.log("  GET /api/player/:id           (t-real-p1, ..., p-h0, ...)");
});
