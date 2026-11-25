"use client";

import { useCallback, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock,
  Cloud,
  Laptop,
  Loader2,
  RefreshCw,
  ShieldAlert,
  XCircle,
} from "lucide-react";

import { formatDurationMsTr, formatRelativeTimeTr } from "@/lib/constants";
import type {
  BrandStatusItem,
  ScrapeRunMode,
  ScrapeStatus,
  StatusApiResponse,
} from "@/types/database";

interface SystemStatusViewProps {
  initialData: StatusApiResponse;
  initialError?: string | null;
}

type StatusFilter = "all" | ScrapeStatus | "never";
type ModeFilter = "all" | ScrapeRunMode;

const STATUS_BADGE_MAP: Record<
  ScrapeStatus | "never",
  { label: string; className: string }
> = {
  success: {
    label: "Başarılı",
    className:
      "border-emerald-500/30 bg-emerald-500/15 text-emerald-300",
  },
  partial: {
    label: "Kısmi",
    className: "border-amber-500/30 bg-amber-500/15 text-amber-300",
  },
  blocked: {
    label: "Bloklu",
    className: "border-orange-500/30 bg-orange-500/15 text-orange-300",
  },
  failed: {
    label: "Hatalı",
    className: "border-rose-500/30 bg-rose-500/15 text-rose-300",
  },
  running: {
    label: "Çalışıyor",
    className: "border-sky-500/30 bg-sky-500/15 text-sky-300",
  },
  never: {
    label: "Henüz Taranmadı",
    className: "border-white/10 bg-white/5 text-zinc-400",
  },
};

function StatusIcon({ status }: { status: ScrapeStatus | "never" }) {
  switch (status) {
    case "success":
      return <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />;
    case "partial":
      return <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />;
    case "blocked":
      return <ShieldAlert className="h-3.5 w-3.5 text-orange-400" />;
    case "failed":
      return <XCircle className="h-3.5 w-3.5 text-rose-400" />;
    case "running":
      return <Loader2 className="h-3.5 w-3.5 animate-spin text-sky-400" />;
    default:
      return <Clock className="h-3.5 w-3.5 text-zinc-500" />;
  }
}

function RunModeBadge({ mode }: { mode: ScrapeRunMode }) {
  if (mode === "local") {
    return (
      <span
        title="Veri merkezi koruması nedeniyle yerel çalıştırma önerilir"
        className="inline-flex items-center gap-1 rounded-full border border-violet-500/30 bg-violet-500/15 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-violet-300 uppercase"
      >
        <Laptop className="h-2.5 w-2.5" />
        local
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-sky-500/25 bg-sky-500/10 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-sky-300 uppercase">
      <Cloud className="h-2.5 w-2.5" />
      cloud
    </span>
  );
}

function BrandStatusCard({ item }: { item: BrandStatusItem }) {
  const log = item.latest_log;
  const effectiveStatus: ScrapeStatus | "never" = log?.status ?? "never";
  const badge = STATUS_BADGE_MAP[effectiveStatus];
  const displayMode: ScrapeRunMode = log?.run_mode ?? item.default_run_mode;
  const timestamp = log?.finished_at || log?.started_at || null;

  return (
    <article className="flex flex-col justify-between gap-3 rounded-2xl border border-white/10 bg-zinc-900/65 p-3.5 transition hover:border-white/15">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <h3 className="truncate text-sm font-semibold text-white">
              {item.label}
            </h3>
            <RunModeBadge mode={displayMode} />
          </div>
          <p className="mt-0.5 text-[11px] text-zinc-500">
            {item.group}
            {item.default_run_mode === "local" && displayMode === "cloud"
              ? " · Varsayılan: local"
              : ""}
          </p>
        </div>

        <span
          className={`inline-flex shrink-0 items-center gap-1 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${badge.className}`}
        >
          <StatusIcon status={effectiveStatus} />
          {badge.label}
        </span>
      </div>

      {log ? (
        <>
          <div className="grid grid-cols-3 gap-2 rounded-xl border border-white/5 bg-zinc-950/60 px-2.5 py-2 text-center">
            <div>
              <p className="text-[10px] text-zinc-500">Çekilen</p>
              <p className="text-xs font-semibold text-zinc-100">
                {log.fetched_count}
              </p>
            </div>
            <div>
              <p className="text-[10px] text-zinc-500">Eşleşen</p>
              <p className="text-xs font-semibold text-emerald-300">
                {log.matched_count}
              </p>
            </div>
            <div>
              <p className="text-[10px] text-zinc-500">Kaydedilen</p>
              <p className="text-xs font-semibold text-zinc-100">
                {log.saved_count}
              </p>
            </div>
          </div>

          <div className="flex items-center justify-between text-[11px] text-zinc-400">
            <span className="inline-flex items-center gap-1">
              <Clock className="h-3 w-3 text-zinc-500" />
              {formatRelativeTimeTr(timestamp)}
            </span>
            <span>Süre: {formatDurationMsTr(log.duration_ms)}</span>
          </div>

          {log.error_message && (
            <div className="rounded-xl border border-rose-500/20 bg-rose-500/10 px-2.5 py-2 text-[11px] leading-relaxed text-rose-200/90">
              {log.error_message}
            </div>
          )}
        </>
      ) : (
        <div className="rounded-xl border border-dashed border-white/10 bg-zinc-950/40 px-3 py-3 text-[11px] text-zinc-500">
          Bu marka için henüz `scrape_logs` kaydı bulunmuyor.
        </div>
      )}
    </article>
  );
}

export function SystemStatusView({
  initialData,
  initialError = null,
}: SystemStatusViewProps) {
  const [data, setData] = useState<StatusApiResponse>(initialData);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [modeFilter, setModeFilter] = useState<ModeFilter>("all");
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(initialError);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/status?refresh=1", { cache: "no-store" });
      const payload = await res.json();
      if (!res.ok) {
        throw new Error(payload.error || "Sistem durumu güncellenemedi.");
      }
      setData(payload);
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Sistem durumu yenilenirken hata oluştu."
      );
    } finally {
      setLoading(false);
    }
  }, []);

  const filteredBrands = useMemo(() => {
    return data.brands.filter((item) => {
      const st: ScrapeStatus | "never" = item.latest_log?.status ?? "never";
      const mode: ScrapeRunMode =
        item.latest_log?.run_mode ?? item.default_run_mode;

      if (statusFilter !== "all" && st !== statusFilter) {
        return false;
      }
      if (modeFilter !== "all" && mode !== modeFilter) {
        return false;
      }
      return true;
    });
  }, [data.brands, statusFilter, modeFilter]);

  const { summary } = data;

  return (
    <div className="flex flex-col">
      {/* Sticky Header */}
      <header className="sticky top-0 z-30 border-b border-white/10 bg-[#09090b]/85 px-4 pb-3 pt-safe backdrop-blur-xl">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-2xl bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30">
              <Activity className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-base leading-tight font-bold tracking-tight text-white">
                Sistem Durumu
              </h1>
              <p className="text-[11px] text-zinc-400">
                Son çalışma: {formatRelativeTimeTr(summary.latestRunAt)} ·{" "}
                {summary.totalBrands} marka
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={() => void refresh()}
            disabled={loading}
            aria-label="Durumu yenile"
            className="flex h-9 w-9 items-center justify-center rounded-full border border-white/10 bg-white/5 text-zinc-300 transition hover:bg-white/10 disabled:opacity-60"
          >
            <RefreshCw
              className={`h-4 w-4 ${loading ? "animate-spin text-emerald-400" : ""}`}
            />
          </button>
        </div>
      </header>

      <div className="space-y-4 px-4 pt-4">
        {/* Summary Cards */}
        <section
          aria-label="Durum özeti"
          className="grid grid-cols-2 gap-2.5 sm:grid-cols-4"
        >
          <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/5 px-3 py-2.5">
            <p className="flex items-center gap-1 text-[11px] text-emerald-300/80">
              <CheckCircle2 className="h-3 w-3" />
              Başarılı
            </p>
            <p className="mt-0.5 text-lg font-bold text-emerald-400">
              {summary.successCount}
            </p>
          </div>
          <div className="rounded-2xl border border-amber-500/20 bg-amber-500/5 px-3 py-2.5">
            <p className="flex items-center gap-1 text-[11px] text-amber-300/80">
              <AlertTriangle className="h-3 w-3" />
              Kısmi
            </p>
            <p className="mt-0.5 text-lg font-bold text-amber-400">
              {summary.partialCount}
            </p>
          </div>
          <div className="rounded-2xl border border-orange-500/20 bg-orange-500/5 px-3 py-2.5">
            <p className="flex items-center gap-1 text-[11px] text-orange-300/80">
              <ShieldAlert className="h-3 w-3" />
              Bloklu (WAF)
            </p>
            <p className="mt-0.5 text-lg font-bold text-orange-400">
              {summary.blockedCount}
            </p>
          </div>
          <div className="rounded-2xl border border-rose-500/20 bg-rose-500/5 px-3 py-2.5">
            <p className="flex items-center gap-1 text-[11px] text-rose-300/80">
              <XCircle className="h-3 w-3" />
              Hatalı
            </p>
            <p className="mt-0.5 text-lg font-bold text-rose-400">
              {summary.failedCount}
            </p>
          </div>
        </section>

        {/* Filter Chips */}
        <section
          aria-label="Durum filtreleri"
          className="space-y-2.5 rounded-3xl border border-white/10 bg-zinc-900/60 p-3.5"
        >
          <div className="no-scrollbar flex items-center gap-1.5 overflow-x-auto pb-0.5">
            {(
              [
                ["all", "Tüm Durumlar"],
                ["success", "Başarılı"],
                ["partial", "Kısmi"],
                ["blocked", "Bloklu"],
                ["failed", "Hatalı"],
                ["never", "Henüz Yok"],
              ] as [StatusFilter, string][]
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setStatusFilter(key)}
                className={`shrink-0 rounded-full px-3 py-1.5 text-xs font-medium transition ${
                  statusFilter === key
                    ? "bg-emerald-500 text-zinc-950"
                    : "border border-white/10 bg-zinc-950/70 text-zinc-300 hover:bg-white/10"
                }`}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="flex items-center gap-1.5">
            <span className="text-[11px] font-medium text-zinc-500">Mod:</span>
            {(
              [
                ["all", "Tümü"],
                ["cloud", "Cloud"],
                ["local", "Local"],
              ] as [ModeFilter, string][]
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                onClick={() => setModeFilter(key)}
                className={`rounded-full px-2.5 py-1 text-[11px] font-medium transition ${
                  modeFilter === key
                    ? "bg-white text-zinc-950"
                    : "border border-white/10 bg-zinc-950/70 text-zinc-300 hover:bg-white/10"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </section>

        {error && (
          <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4 text-xs text-rose-200">
            {error}
          </div>
        )}

        {/* Brand Status Grid */}
        {filteredBrands.length === 0 ? (
          <div className="rounded-3xl border border-white/10 bg-zinc-900/40 px-6 py-12 text-center text-xs text-zinc-400">
            Seçili filtrelere uyan marka durumu bulunamadı.
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {filteredBrands.map((item) => (
              <BrandStatusCard key={item.brand} item={item} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
