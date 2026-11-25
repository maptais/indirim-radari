import { Activity } from "lucide-react";

export default function SystemStatusLoading() {
  return (
    <div className="flex flex-col" aria-busy="true" aria-label="Sistem durumu yükleniyor">
      {/* Sticky Header Skeleton */}
      <header className="sticky top-0 z-30 border-b border-white/10 bg-[#09090b]/85 px-4 pb-3 pt-safe backdrop-blur-xl">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-2xl bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30">
              <Activity className="h-5 w-5" />
            </div>
            <div className="space-y-1">
              <p className="text-base leading-tight font-bold tracking-tight text-white">
                Sistem Durumu
              </p>
              <div className="h-3 w-36 animate-pulse rounded-full bg-zinc-800/80" />
            </div>
          </div>

          <div className="h-9 w-9 animate-pulse rounded-full border border-white/10 bg-zinc-900/80" />
        </div>
      </header>

      <div className="space-y-4 px-4 pt-4">
        {/* Summary Cards Skeleton */}
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          {[0, 1, 2, 3].map((idx) => (
            <div
              key={idx}
              className="rounded-2xl border border-white/10 bg-zinc-900/60 px-3 py-2.5"
            >
              <div className="h-3 w-20 animate-pulse rounded bg-zinc-800" />
              <div className="mt-2 h-6 w-10 animate-pulse rounded bg-zinc-800/90" />
            </div>
          ))}
        </div>

        {/* Filter Chips Skeleton */}
        <div className="space-y-2.5 rounded-3xl border border-white/10 bg-zinc-900/60 p-3.5">
          <div className="flex gap-1.5 overflow-hidden">
            {[92, 74, 58, 66, 62, 78].map((w, idx) => (
              <div
                key={idx}
                style={{ width: `${w}px` }}
                className="h-7 shrink-0 animate-pulse rounded-full border border-white/5 bg-zinc-950/70"
              />
            ))}
          </div>
          <div className="flex items-center gap-1.5">
            <div className="h-3 w-8 rounded bg-zinc-800/80" />
            {[54, 56, 54].map((w, idx) => (
              <div
                key={idx}
                style={{ width: `${w}px` }}
                className="h-6 animate-pulse rounded-full border border-white/5 bg-zinc-950/70"
              />
            ))}
          </div>
        </div>

        {/* Brand Cards Grid Skeleton */}
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, idx) => (
            <div
              key={idx}
              className="flex flex-col gap-3 rounded-2xl border border-white/10 bg-zinc-900/65 p-3.5"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="space-y-1.5">
                  <div className="h-4 w-28 animate-pulse rounded bg-zinc-800" />
                  <div className="h-3 w-20 animate-pulse rounded bg-zinc-800/70" />
                </div>
                <div className="h-6 w-20 animate-pulse rounded-full bg-zinc-800" />
              </div>
              <div className="grid grid-cols-3 gap-2 rounded-xl border border-white/5 bg-zinc-950/60 px-2.5 py-2.5">
                {[0, 1, 2].map((col) => (
                  <div key={col} className="flex flex-col items-center gap-1">
                    <div className="h-2.5 w-10 animate-pulse rounded bg-zinc-800/80" />
                    <div className="h-3.5 w-8 animate-pulse rounded bg-zinc-800" />
                  </div>
                ))}
              </div>
              <div className="flex items-center justify-between">
                <div className="h-3 w-20 animate-pulse rounded bg-zinc-800/70" />
                <div className="h-3 w-16 animate-pulse rounded bg-zinc-800/70" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
