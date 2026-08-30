import Link from "next/link";

/** Bilingual 404 page (default Arabic, matching the app default). */
export default function NotFound() {
  return (
    <div dir="rtl" className="flex min-h-screen items-center justify-center bg-[#e9edf2] p-6">
      <div className="w-full max-w-sm rounded-lg border border-[#b9c8dd] bg-white text-center shadow-sm">
        <div className="rounded-t-lg bg-gradient-to-b from-[#1d4f92] to-[#123a70] px-4 py-3">
          <h1 className="text-[15px] font-bold text-white">الصفحة غير موجودة</h1>
        </div>
        <div className="px-5 py-6">
          <p className="text-4xl font-extrabold tracking-wider text-[#17457f]">404</p>
          <p className="mt-2 text-[13.5px] leading-relaxed text-[#5b6b80]">
            الرابط الذي طلبته غير موجود.
            <span className="mx-1 text-[#b9c8dd]">|</span>
            The page you requested does not exist.
          </p>
          <Link
            href="/"
            className="mx-auto mt-4 inline-flex items-center rounded border border-[#17457f] bg-[#17457f] px-3.5 py-1.5 text-[13px] font-semibold text-white hover:bg-[#123a70]"
          >
            العودة إلى فكوورة | Back to Fkoora
          </Link>
        </div>
      </div>
    </div>
  );
}
