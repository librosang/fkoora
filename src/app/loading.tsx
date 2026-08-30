import { Loader2 } from "lucide-react";

/** Route-level loading state (initial page bundle / slow navigation). */
export default function Loading() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[#e9edf2]">
      <Loader2 className="h-6 w-6 animate-spin text-[#17457f]" />
    </div>
  );
}
