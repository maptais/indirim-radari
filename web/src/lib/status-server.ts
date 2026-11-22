import "server-only";

import { BRAND_CATALOG, getBrandLabel } from "@/lib/constants";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import type {
  BrandStatusItem,
  ScrapeLogRow,
  ScrapeRunMode,
  ScrapeStatus,
  StatusApiResponse,
} from "@/types/database";

const VALID_RUN_MODES = new Set<ScrapeRunMode>(["cloud", "local"]);
const VALID_STATUSES = new Set<ScrapeStatus>([
  "running",
  "success",
  "partial",
  "failed",
  "blocked",
]);

function normalizeLogRow(row: Record<string, unknown>): ScrapeLogRow {
  const rawMode = String(row.run_mode ?? "cloud").toLowerCase() as ScrapeRunMode;
  const rawStatus = String(row.status ?? "failed").toLowerCase() as ScrapeStatus;

  return {
    id: Number(row.id ?? 0),
    brand: String(row.brand ?? "").trim().toLowerCase(),
    engine: String(row.engine ?? "").trim().toLowerCase(),
    run_mode: VALID_RUN_MODES.has(rawMode) ? rawMode : "cloud",
    status: VALID_STATUSES.has(rawStatus) ? rawStatus : "failed",
    fetched_count: Number(row.fetched_count ?? 0),
    matched_count: Number(row.matched_count ?? 0),
    saved_count: Number(row.saved_count ?? 0),
    notified_count: Number(row.notified_count ?? 0),
    duration_ms:
      row.duration_ms !== null && row.duration_ms !== undefined
        ? Number(row.duration_ms)
        : null,
    error_message:
      typeof row.error_message === "string" && row.error_message.trim()
        ? row.error_message.trim()
        : null,
    started_at: String(row.started_at ?? ""),
    finished_at:
      typeof row.finished_at === "string" && row.finished_at.trim()
        ? row.finished_at
        : null,
  };
}

export function buildEmptyStatusResponse(): StatusApiResponse {
  const brands: BrandStatusItem[] = BRAND_CATALOG.map((meta) => ({
    brand: meta.slug,
    label: meta.label,
    group: meta.group,
    default_run_mode: meta.defaultRunMode,
    latest_log: null,
  }));

  return {
    brands,
    recentLogs: [],
    summary: {
      totalBrands: brands.length,
      successCount: 0,
      partialCount: 0,
      blockedCount: 0,
      failedCount: 0,
      runningCount: 0,
      neverRunCount: brands.length,
      latestRunAt: null,
    },
  };
}

export async function loadSystemStatus(): Promise<{
  data: StatusApiResponse;
  error: string | null;
}> {
  const empty = buildEmptyStatusResponse();

  try {
    const supabase = getSupabaseAdmin();
    const { data: rawLogs, error } = await supabase
      .from("scrape_logs")
      .select(
        "id, brand, engine, run_mode, status, fetched_count, matched_count, saved_count, notified_count, duration_ms, error_message, started_at, finished_at"
      )
      .order("started_at", { ascending: false })
      .limit(500);

    if (error) {
      return {
        data: empty,
        error: `Tarama kayıtları okunamadı: ${error.message}`,
      };
    }

    const logs = (rawLogs || []).map((row) =>
      normalizeLogRow(row as Record<string, unknown>)
    );

    const latestByBrand = new Map<string, ScrapeLogRow>();
    for (const log of logs) {
      if (log.brand && !latestByBrand.has(log.brand)) {
        latestByBrand.set(log.brand, log);
      }
    }

    const knownSlugs = new Set<string>(BRAND_CATALOG.map((b) => b.slug));
    const brands: BrandStatusItem[] = BRAND_CATALOG.map((meta) => ({
      brand: meta.slug,
      label: meta.label,
      group: meta.group,
      default_run_mode: meta.defaultRunMode,
      latest_log: latestByBrand.get(meta.slug) ?? null,
    }));

    // Include any extra brand slugs logged in DB outside the canonical 22
    for (const [slug, log] of latestByBrand.entries()) {
      if (!knownSlugs.has(slug)) {
        brands.push({
          brand: slug,
          label: getBrandLabel(slug),
          group: "Diğer",
          default_run_mode: log.run_mode,
          latest_log: log,
        });
      }
    }

    let successCount = 0;
    let partialCount = 0;
    let blockedCount = 0;
    let failedCount = 0;
    let runningCount = 0;
    let neverRunCount = 0;

    for (const item of brands) {
      const status = item.latest_log?.status;
      if (!status) {
        neverRunCount += 1;
      } else if (status === "success") {
        successCount += 1;
      } else if (status === "partial") {
        partialCount += 1;
      } else if (status === "blocked") {
        blockedCount += 1;
      } else if (status === "failed") {
        failedCount += 1;
      } else if (status === "running") {
        runningCount += 1;
      }
    }

    const firstLog = logs[0] ?? null;
    const latestRunAt = firstLog?.finished_at || firstLog?.started_at || null;

    return {
      data: {
        brands,
        recentLogs: logs.slice(0, 20),
        summary: {
          totalBrands: brands.length,
          successCount,
          partialCount,
          blockedCount,
          failedCount,
          runningCount,
          neverRunCount,
          latestRunAt,
        },
      },
      error: null,
    };
  } catch (err) {
    return {
      data: empty,
      error:
        err instanceof Error
          ? err.message
          : "Sistem durumu veritabanından alınamadı.",
    };
  }
}
