export default function SettingsLoading() {
  return (
    <div className="flex flex-col" aria-busy="true" aria-label="Ayarlar yükleniyor">
      {/* Sticky Header Skeleton */}
      <header className="sticky top-0 z-30 border-b border-white/10 bg-[#09090b]/85 px-4 pb-3 pt-safe backdrop-blur-xl">
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-base font-bold tracking-tight text-white">
              Kişisel Radar Ayarları
            </p>
            <p className="text-[11px] text-zinc-400">
              Beden, kumaş kuralları, bütçe eşikleri ve AI tarz profili
            </p>
          </div>
          <div className="h-9 w-24 animate-pulse rounded-full bg-emerald-500/25" />
        </div>
      </header>

      <div className="space-y-4 px-4 pt-4">
        {/* Info Banner Skeleton */}
        <div className="h-20 animate-pulse rounded-2xl border border-sky-400/15 bg-sky-500/5" />

        {/* Web Push Card Skeleton */}
        <div className="space-y-3 rounded-3xl border border-white/10 bg-zinc-900/70 p-5">
          <div className="h-4 w-48 animate-pulse rounded bg-zinc-800" />
          <div className="h-3 w-3/4 animate-pulse rounded bg-zinc-800/70" />
          <div className="h-10 w-40 animate-pulse rounded-2xl bg-zinc-800/80" />
        </div>

        {/* Settings Sections Skeletons */}
        {[0, 1, 2].map((idx) => (
          <div
            key={idx}
            className="space-y-4 rounded-3xl border border-white/10 bg-zinc-900/70 p-5"
          >
            <div className="h-4 w-44 animate-pulse rounded bg-zinc-800" />
            <div className="h-3 w-5/6 animate-pulse rounded bg-zinc-800/70" />
            <div className="grid grid-cols-3 gap-2.5">
              {[0, 1, 2].map((cell) => (
                <div
                  key={cell}
                  className="h-11 animate-pulse rounded-2xl border border-white/5 bg-zinc-950/70"
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
