"use client";

import { useEffect } from "react";
import { RefreshCw } from "lucide-react";

/**
 * Root error boundary: catches any React render crash so the user sees a
 * friendly bilingual retry screen instead of a white page.
 * (Matches the app's visual language: blue header, light body.)
 */
export default function ErrorPage({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // surface for client error reporting / browser console logs
    console.error("app error boundary:", error);
  }, [error]);

  return (
    <div dir="rtl" className="flex min-h-screen items-center justify-center bg-[#e9edf2] p-6">
      <div className="w-full max-w-sm rounded-lg border border-[#b9c8dd] bg-white text-center shadow-sm">
        <div className="rounded-t-lg bg-gradient-to-b from-[#1d4f92] to-[#123a70] px-4 py-3">
          <h1 className="text-[15px] font-bold text-white">
            حدث خطأ غير متوقع
            <span className="mx-1.5 font-normal text-white/60">|</span>
            Something went wrong
          </h1>
        </div>
        <div className="px-5 py-6">
          <p className="text-[13.5px] leading-relaxed text-[#5b6b80]">
            نعتذر، وقع خلل أثناء عرض الصفحة. يمكنك إعادة المحاولة أو التحديث لاحقاً.
            <br />
            <span className="text-[12px] text-[#7d8ea3]">
              Sorry, an error occurred while rendering the page. Retry or refresh in a moment.
            </span>
          </p>
          <button
            type="button"
            onClick={reset}
            className="mx-auto mt-4 flex items-center gap-1.5 rounded border border-[#17457f] bg-[#17457f] px-3.5 py-1.5 text-[13px] font-semibold text-white hover:bg-[#123a70]"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            إعادة المحاولة | Try again
          </button>
        </div>
      </div>
    </div>
  );
}
