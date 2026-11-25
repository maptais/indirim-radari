import "server-only";

import { revalidateTag, unstable_cache } from "next/cache";

import { BRAND_CATALOG, getBrandLabel } from "@/lib/constants";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import type {
  BrandStatusItem,
  ScrapeLogRow,
  ScrapeRunMode,
  ScrapeStatus,
  StatusApiResponse,
} from "@/types/database";

const STATUS_CACHE_TAG = "system-status";
const STATUS_CACHE_TTL_SECONDS = 60;

const SCRAPE_LOG_COLUMNS =
  "id, brand, engine, run_mode, status, fetched_count, matched_count, saved_count, notified_count, duration_ms, error_message, started_at, finished_at";

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

export function invalidateSystemStatusCache(): void {
  try {
    revalidateTag(STATUS_CACHE_TAG);
  } catch {
    // Safe no-op if called outside Next.js request lifecycle
  }
}

/**
 * Fetches recent scan history (PK index scan, limit 20) and per-brand latest
 * health logs (index seek on idx_scrape_logs_brand_started, limit 1 per brand)
 * in parallel via Promise.all — avoiding a 500-row full-table sort on scrape_logs.
 */
async function fetchSystemStatusUncached(): Promise<StatusApiResponse> {
  const supabase = getSupabaseAdmin();

  const recentLogsQuery = supabase
    .from("scrape_logs")
    .select(SCRAPE_LOG_COLUMNS)
    .order("id", { ascending: false })
    .limit(20);

  const brandHealthQueries = BRAND_CATALOG.map((meta) =>
    supabase
      .from("scrape_logs")
      .select(SCRAPE_LOG_COLUMNS)
      .eq("brand", meta.slug)
      .order("started_at", { ascending: false })
      .limit(1)
      .maybeSingle()
  );

  const [recentLogsRes, ...brandHealthResults] = await Promise.all([
    recentLogsQuery,
    ...brandHealthQueries,
  ]);

  if (recentLogsRes.error) {
    throw new Error(
      `Tarama kayıtları okunamadı: ${recentLogsRes.error.message}`
    );
  }

  for (const res of brandHealthResults) {
    if (res.error) {
      throw new Error(`Marka sağlık durumu okunamadı: ${res.error.message}`);
    }
  }

  const recentLogs = (recentLogsRes.data || []).map((row) =>
    normalizeLogRow(row as Record<string, unknown>)
  );

  const latestByBrand = new Map<string, ScrapeLogRow>();

  BRAND_CATALOG.forEach((meta, idx) => {
    const rawRow = brandHealthResults[idx]?.data;
    if (rawRow) {
      latestByBrand.set(
        meta.slug,
        normalizeLogRow(rawRow as Record<string, unknown>)
      );
    }
  });

  // Also check recentLogs so any brand logged outside BRAND_CATALOG (or a brand
  // log inserted moments after its per-brand query) is reflected accurately.
  for (const log of recentLogs) {
    if (!log.brand) continue;
    const existing = latestByBrand.get(log.brand);
    if (!existing || log.started_at > existing.started_at || log.id > existing.id) {
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
  let latestRunAt: string | null =
    recentLogs[0]?.finished_at || recentLogs[0]?.started_at || null;

  for (const item of brands) {
    const log = item.latest_log;
    const status = log?.status;
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

    const candidateTs = log?.finished_at || log?.started_at || null;
    if (candidateTs && (!latestRunAt || candidateTs > latestRunAt)) {
      latestRunAt = candidateTs;
    }
  }

  return {
    brands,
    recentLogs,
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
  };
}

const getCachedSystemStatus = unstable_cache(
  async () => fetchSystemStatusUncached(),
  ["system-status-v1"],
  {
    revalidate: STATUS_CACHE_TTL_SECONDS,
    tags: [STATUS_CACHE_TAG],
  }
);

export async function loadSystemStatus(
  options: { bypassCache?: boolean } = {}
): Promise<{
  data: StatusApiResponse;
  error: string | null;
}> {
  const empty = buildEmptyStatusResponse();

  try {
    if (options.bypassCache) {
      invalidateSystemStatusCache();
      const fresh = await fetchSystemStatusUncached();
      return { data: fresh, error: null };
    }

    const cached = await getCachedSystemStatus();
    return { data: cached, error: null };
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
