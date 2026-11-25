import { Radar } from "lucide-react";

export default function HomeLoading() {
  return (
    <div className="flex flex-col" aria-busy="true" aria-label="İndirim akışı yükleniyor">
      {/* Sticky Header Skeleton */}
      <header className="sticky top-0 z-30 border-b border-white/10 bg-[#09090b]/85 px-4 pb-3 pt-safe backdrop-blur-xl">
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2.5">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30">
              <Radar className="h-5 w-5" />
            </div>
            <div className="min-w-0 space-y-1">
              <p className="truncate text-base leading-tight font-bold tracking-tight text-white">
                İndirim Radarı
              </p>
              <div className="h-3 w-28 animate-pulse rounded-full bg-zinc-800/80" />
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <div className="h-9 w-24 animate-pulse rounded-full border border-white/10 bg-zinc-900/80" />
            <div className="h-9 w-20 animate-pulse rounded-full border border-emerald-500/20 bg-emerald-500/10" />
          </div>
        </div>
      </header>

      <div className="space-y-4 px-4 pt-4">
        {/* Summary Strip Skeleton */}
        <div className="grid grid-cols-3 gap-2.5">
          {[0, 1, 2].map((idx) => (
            <div
              key={idx}
              className="rounded-2xl border border-white/10 bg-zinc-900/60 px-3 py-2.5"
            >
              <div className="h-3 w-16 animate-pulse rounded bg-zinc-800" />
              <div className="mt-2 h-6 w-12 animate-pulse rounded bg-zinc-800/90" />
            </div>
          ))}
        </div>

        {/* Filter Bar Skeleton */}
        <div className="space-y-3 rounded-3xl border border-white/10 bg-zinc-900/60 p-3.5">
          <div className="h-10 w-full animate-pulse rounded-2xl border border-white/5 bg-zinc-950/80" />
          <div className="flex flex-wrap gap-2">
            {[112, 128, 156, 118, 132].map((w, idx) => (
              <div
                key={idx}
                style={{ width: `${w}px` }}
                className="h-9 animate-pulse rounded-2xl border border-white/5 bg-zinc-950/70"
              />
            ))}
          </div>
          <div className="flex gap-1.5 overflow-hidden">
            {[72, 64, 68, 84, 76, 80].map((w, idx) => (
              <div
                key={idx}
                style={{ width: `${w}px` }}
                className="h-7 shrink-0 animate-pulse rounded-full border border-white/5 bg-zinc-950/70"
              />
            ))}
          </div>
        </div>

        {/* Product Grid Skeleton */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, idx) => (
            <div
              key={idx}
              className="flex flex-col overflow-hidden rounded-3xl border border-white/10 bg-zinc-900/65"
            >
              <div className="aspect-[4/5] w-full animate-pulse bg-zinc-950/90" />
              <div className="space-y-3 p-4">
                <div className="flex items-center justify-between">
                  <div className="h-3 w-24 animate-pulse rounded bg-zinc-800" />
                  <div className="h-3 w-14 animate-pulse rounded bg-zinc-800/70" />
                </div>
                <div className="space-y-1.5">
                  <div className="h-4 w-11/12 animate-pulse rounded bg-zinc-800" />
                  <div className="h-4 w-2/3 animate-pulse rounded bg-zinc-800/80" />
                </div>
                <div className="h-6 w-28 animate-pulse rounded bg-zinc-800" />
                <div className="h-10 w-full animate-pulse rounded-2xl bg-zinc-800/80" />
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
