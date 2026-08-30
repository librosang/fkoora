/**
 * Minimal branded HTML pages for plain Route Handlers (no React): the legacy
 * /match/<id> and /competition/<id> redirect handlers use these for their
 * 404 ("does not exist") and retry ("backend slow") answers.
 */

/** Bilingual minimal page. `refresh` > 0 adds a meta-refresh self-retry. */
export function minimalHtml(
  langEn: boolean,
  title: string,
  body: string,
  refresh = 0,
): string {
  return `<!DOCTYPE html>
<html lang="${langEn ? "en" : "ar"}" dir="${langEn ? "ltr" : "rtl"}">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="robots" content="noindex, follow"/>${refresh > 0 ? `<meta http-equiv="refresh" content="${refresh}"/>` : ""}
<title>${title}</title>
<style>body{font-family:system-ui,sans-serif;background:#e9edf2;color:#1c2b3a;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0}main{max-width:420px;padding:24px;text-align:center}h1{font-size:18px;margin:0 0 8px}p{font-size:14px;color:#5b6b80;margin:0 0 16px}a{display:inline-block;padding:8px 16px;border-radius:6px;background:#17457f;color:#fff;text-decoration:none;font-size:14px;font-weight:600}</style>
</head>
<body><main>
<svg viewBox="0 0 24 24" width="40" height="40" fill="none" stroke="#17457f" stroke-width="1.6" aria-hidden="true"><circle cx="12" cy="12" r="9.5"/><path d="M12 8.2l3.6 2.6-1.4 4.2H9.8L8.4 10.8 12 8.2z" fill="#17457f" stroke="none"/></svg>
<h1>${title}</h1>
${body}
</main></body></html>`;
}

/** 404 body: the backend says this entity does not exist. */
export function notFoundHtml(langEn: boolean, kind: {
  ar: string;
  en: string;
  bodyAr: string;
}): string {
  return minimalHtml(
    langEn,
    langEn ? `${kind.en} not found | Fkoora` : `${kind.ar} غير موجودة | فكوورة`,
    langEn
      ? `<p>This ${kind.en.toLowerCase()} does not exist (or has been removed).</p><a href="/">Fkoora home</a>`
      : `<p>${kind.bodyAr}</p><a href="/">صفحة فكوورة الرئيسية</a>`,
  );
}

/** 200 + noindex + meta-refresh page: the backend is slow, retry shortly. */
export function retryHtml(langEn: boolean, kind: {
  ar: string;
  en: string;
}): string {
  return minimalHtml(
    langEn,
    langEn ? `Loading ${kind.en}… | Fkoora` : `جارٍ تحميل ${kind.ar}… | فكوورة`,
    langEn
      ? `<p>${kind.en} details are taking longer than usual. This page will retry automatically…</p><a href="/">Go to today's matches</a>`
      : `<p>تفاصيل ${kind.ar} تستغرق وقتًا أطول من المعتاد. سيعيد تحديث الصفحة تلقائيًا…</p><a href="/">الذهاب لمباريات اليوم</a>`,
    5,
  );
}
