import type { SupabaseClient } from "@supabase/supabase-js";

import {
  DEFAULT_TARGET_GENDERS,
  GENDER_ORDER,
  getBrandLabel,
} from "@/lib/constants";
import { matchesUserSizes } from "@/lib/size-match";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import type {
  DefaultUserPrefs,
  ProductCategory,
  ProductGender,
  ProductRow,
  ProductsApiResponse,
} from "@/types/database";

export const DEFAULT_PAGE_SIZE = 60;
const MAX_PAGE_SIZE = 120;
const SUPABASE_CHUNK_SIZE = 1000;
const MAX_SUPABASE_PAGES = 20; // Safety ceiling: 20,000 active products

const VALID_CATEGORIES = new Set<ProductCategory>([
  "shoes",
  "tops",
  "bottoms",
  "outerwear",
  "other",
]);

const VALID_GENDERS = new Set<ProductGender>(GENDER_ORDER);

export interface ProductQueryOptions {
  brand?: string;
  category?: string;
  genders?: ProductGender[] | "all" | "settings" | string;
  topSizes?: string[];
  bottomSizes?: string[];
  shoeSizes?: string[];
  onlyMySize?: boolean;
  hideUnknownSize?: boolean;
  minDiscount?: number;
  realOnly?: boolean;
  cartOnly?: boolean;
  sort?: string;
  q?: string;
  page?: number;
  pageSize?: number;
}

export function createEmptyProductsResponse(
  pageSize = DEFAULT_PAGE_SIZE
): ProductsApiResponse {
  return {
    products: [],
    brands: [],
    brandCounts: {},
    targetGenders: [...DEFAULT_TARGET_GENDERS],
    defaultPrefs: {
      targetGenders: [...DEFAULT_TARGET_GENDERS],
      topSizes: [],
      bottomSizes: [],
      shoeSizes: [],
    },
    page: 1,
    pageSize,
    hasMore: false,
    totalCount: 0,
    realDiscountCount: 0,
    criticalStockCount: 0,
    stats: {
      totalActive: 0,
      totalCatalog: 0,
      realDiscountCount: 0,
      criticalStockCount: 0,
      cartPromoCount: 0,
      unknownSizeCount: 0,
      latestScrapedAt: null,
    },
  };
}

function clampInteger(
  input: unknown,
  min: number,
  max: number,
  fallback: number
): number {
  const numeric = Number(input);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.min(max, Math.max(min, Math.trunc(numeric)));
}

function canonicalizeGenderToken(raw: unknown): ProductGender | null {
  const cleaned = String(raw ?? "")
    .trim()
    .toLocaleLowerCase("tr-TR")
    .replace(/ı/g, "i");
  if (cleaned === "men" || cleaned === "man" || cleaned === "erkek") {
    return "men";
  }
  if (
    cleaned === "women" ||
    cleaned === "woman" ||
    cleaned === "kadin" ||
    cleaned === "bayan"
  ) {
    return "women";
  }
  if (cleaned === "unisex") {
    return "unisex";
  }
  return null;
}

function normalizeGender(raw: unknown): ProductGender {
  return canonicalizeGenderToken(raw) ?? "unisex";
}

function normalizeTargetGenders(raw: unknown): ProductGender[] {
  if (!Array.isArray(raw)) return [...DEFAULT_TARGET_GENDERS];
  const seen = new Set<ProductGender>();
  const result: ProductGender[] = [];
  for (const item of raw) {
    const val = canonicalizeGenderToken(item);
    if (val && VALID_GENDERS.has(val) && !seen.has(val)) {
      seen.add(val);
      result.push(val);
    }
  }
  return result.length > 0 ? result : [...DEFAULT_TARGET_GENDERS];
}

function normalizeSizeArray(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const item of raw) {
    const cleaned = String(item ?? "").trim().toUpperCase();
    if (cleaned && !seen.has(cleaned)) {
      seen.add(cleaned);
      out.push(cleaned);
    }
  }
  return out;
}

export function getEffectivePrice(item: ProductRow): number {
  if (
    item.cart_price !== null &&
    item.cart_price > 0 &&
    item.cart_price < item.current_price
  ) {
    return item.cart_price;
  }
  return item.current_price;
}

export function getEffectiveDiscountPercent(item: ProductRow): number {
  const effPrice = getEffectivePrice(item);
  const refOriginal =
    item.original_price !== null && item.original_price > effPrice
      ? item.original_price
      : effPrice < item.current_price
        ? item.current_price
        : null;
  if (refOriginal !== null && refOriginal > effPrice) {
    return Math.min(
      100,
      Math.max(0, Math.round(((refOriginal - effPrice) / refOriginal) * 100))
    );
  }
  return item.discount_percent;
}

function normalizeNumericProduct(row: Record<string, unknown>): ProductRow {
  const currentPrice = Number(row.current_price ?? 0);
  const rawCart =
    row.cart_price !== null && row.cart_price !== undefined
      ? Number(row.cart_price)
      : null;
  const cartPrice =
    rawCart !== null &&
    Number.isFinite(rawCart) &&
    rawCart > 0 &&
    rawCart < currentPrice
      ? rawCart
      : null;
  const promoBadge =
    typeof row.promo_badge === "string" && row.promo_badge.trim()
      ? row.promo_badge.trim()
      : null;

  return {
    ...(row as unknown as ProductRow),
    gender: normalizeGender(row.gender),
    current_price: currentPrice,
    original_price:
      row.original_price !== null && row.original_price !== undefined
        ? Number(row.original_price)
        : null,
    cart_price: cartPrice,
    promo_badge: promoBadge,
    discount_percent: Number(row.discount_percent ?? 0),
    available_sizes: Array.isArray(row.available_sizes)
      ? (row.available_sizes as string[])
      : [],
    polyester_percent:
      row.polyester_percent !== null && row.polyester_percent !== undefined
        ? Number(row.polyester_percent)
        : null,
    lowest_price_days:
      row.lowest_price_days !== null && row.lowest_price_days !== undefined
        ? Number(row.lowest_price_days)
        : null,
    historical_min_price:
      row.historical_min_price !== null &&
      row.historical_min_price !== undefined
        ? Number(row.historical_min_price)
        : null,
    ai_score:
      row.ai_score !== null && row.ai_score !== undefined
        ? Number(row.ai_score)
        : null,
    last_notified_price:
      row.last_notified_price !== null && row.last_notified_price !== undefined
        ? Number(row.last_notified_price)
        : null,
  };
}

function getTargetSizesForCategory(
  category: ProductCategory,
  sizes: { topSizes: string[]; bottomSizes: string[]; shoeSizes: string[] }
): string[] {
  if (category === "shoes") return sizes.shoeSizes;
  if (category === "bottoms") return sizes.bottomSizes;
  if (category === "tops" || category === "outerwear") return sizes.topSizes;
  return [...sizes.topSizes, ...sizes.bottomSizes, ...sizes.shoeSizes];
}

function resolveAllowedGenders(
  rawGenders: ProductQueryOptions["genders"],
  fallbackGenders: ProductGender[]
): Set<ProductGender> | null {
  if (rawGenders === undefined || rawGenders === null) {
    return new Set<ProductGender>(fallbackGenders);
  }

  if (Array.isArray(rawGenders)) {
    const valid = rawGenders.filter((g) => VALID_GENDERS.has(g));
    if (valid.length === 0) return new Set<ProductGender>(fallbackGenders);
    return new Set<ProductGender>(valid);
  }

  const trimmed = String(rawGenders).trim().toLowerCase();
  if (!trimmed || trimmed === "settings") {
    return new Set<ProductGender>(fallbackGenders);
  }
  if (trimmed === "all") {
    return null;
  }

  const parsed = trimmed
    .split(",")
    .map((part) => canonicalizeGenderToken(part))
    .filter((part): part is ProductGender => part !== null && VALID_GENDERS.has(part));

  if (parsed.length === 0) {
    return new Set<ProductGender>(fallbackGenders);
  }
  return new Set<ProductGender>(parsed);
}

/**
 * Fetch ALL active products from Supabase without hitting PostgREST's 1000-row cap.
 * First page requests `count: "exact"`; remaining pages are fetched in parallel via `.range()`.
 */
async function fetchAllActiveProducts(
  supabase: SupabaseClient
): Promise<{
  rows: Record<string, unknown>[];
  error: { message: string } | null;
}> {
  const first = await supabase
    .from("products")
    .select("*", { count: "exact" })
    .eq("is_active", true)
    .order("discount_percent", { ascending: false })
    .order("last_seen_at", { ascending: false })
    .order("id", { ascending: true })
    .range(0, SUPABASE_CHUNK_SIZE - 1);

  if (first.error) {
    return { rows: [], error: first.error };
  }

  const firstRows = (first.data || []) as Record<string, unknown>[];
  const exactTotal =
    typeof first.count === "number" && Number.isFinite(first.count)
      ? first.count
      : null;

  if (
    firstRows.length === 0 ||
    (exactTotal !== null && exactTotal <= firstRows.length) ||
    (exactTotal === null && firstRows.length < SUPABASE_CHUNK_SIZE)
  ) {
    return { rows: firstRows, error: null };
  }

  const allRows: Record<string, unknown>[] = [...firstRows];
  const step = firstRows.length;

  if (exactTotal !== null) {
    const totalPages = Math.min(
      MAX_SUPABASE_PAGES,
      Math.ceil(exactTotal / step)
    );
    const remainingQueries = [];
    for (let pageIdx = 1; pageIdx < totalPages; pageIdx += 1) {
      const from = pageIdx * step;
      const to = from + step - 1;
      remainingQueries.push(
        supabase
          .from("products")
          .select("*")
          .eq("is_active", true)
          .order("discount_percent", { ascending: false })
          .order("last_seen_at", { ascending: false })
          .order("id", { ascending: true })
          .range(from, to)
      );
    }

    const results = await Promise.all(remainingQueries);
    for (const res of results) {
      if (res.error) {
        return { rows: [], error: res.error };
      }
      if (Array.isArray(res.data) && res.data.length > 0) {
        allRows.push(...(res.data as Record<string, unknown>[]));
      }
    }
  } else {
    for (let pageIdx = 1; pageIdx < MAX_SUPABASE_PAGES; pageIdx += 1) {
      const from = pageIdx * step;
      const to = from + step - 1;
      const res = await supabase
        .from("products")
        .select("*")
        .eq("is_active", true)
        .order("discount_percent", { ascending: false })
        .order("last_seen_at", { ascending: false })
        .order("id", { ascending: true })
        .range(from, to);

      if (res.error) {
        return { rows: [], error: res.error };
      }
      const chunk = (res.data || []) as Record<string, unknown>[];
      allRows.push(...chunk);
      if (chunk.length < step) break;
    }
  }

  // Deduplicate by id in case concurrent scrape upserts shifted rows across chunk boundaries
  const seenIds = new Set<string>();
  const deduped: Record<string, unknown>[] = [];
  for (const row of allRows) {
    const id = String(row.id ?? "");
    if (!id || !seenIds.has(id)) {
      if (id) seenIds.add(id);
      deduped.push(row);
    }
  }

  return { rows: deduped, error: null };
}

export async function queryProductsCatalog(
  options: ProductQueryOptions = {}
): Promise<{ data: ProductsApiResponse; error: string | null }> {
  const pageSize = clampInteger(
    options.pageSize,
    1,
    MAX_PAGE_SIZE,
    DEFAULT_PAGE_SIZE
  );
  const page = clampInteger(options.page, 1, 1000, 1);

  const brandParam = (options.brand || "").trim().toLowerCase();
  const categoryParam = (options.category || "").trim().toLowerCase();
  const sortParam = (options.sort || "discount").trim().toLowerCase();
  const queryText = (options.q || "").trim().toLowerCase();
  const realOnly = Boolean(options.realOnly);
  const cartOnly = Boolean(options.cartOnly);
  const onlyMySize = Boolean(options.onlyMySize);
  const hideUnknownSize = Boolean(options.hideUnknownSize);
  const minDiscount = clampInteger(options.minDiscount, 0, 100, 0);

  const supabase = getSupabaseAdmin();

  const [productsResult, { data: latestLogs }, { data: userSettingsRow }] =
    await Promise.all([
      fetchAllActiveProducts(supabase),
      supabase
        .from("scrape_logs")
        .select("finished_at, started_at")
        .order("started_at", { ascending: false })
        .limit(1),
      supabase
        .from("user_settings")
        .select("target_genders, top_sizes, bottom_sizes, shoe_sizes")
        .eq("id", 1)
        .limit(1)
        .maybeSingle(),
    ]);

  if (productsResult.error) {
    return {
      data: createEmptyProductsResponse(pageSize),
      error: productsResult.error.message,
    };
  }

  const defaultPrefs: DefaultUserPrefs = {
    targetGenders: normalizeTargetGenders(userSettingsRow?.target_genders),
    topSizes: normalizeSizeArray(userSettingsRow?.top_sizes),
    bottomSizes: normalizeSizeArray(userSettingsRow?.bottom_sizes),
    shoeSizes: normalizeSizeArray(userSettingsRow?.shoe_sizes),
  };

  const effectiveSizes = {
    topSizes:
      options.topSizes !== undefined
        ? normalizeSizeArray(options.topSizes)
        : defaultPrefs.topSizes,
    bottomSizes:
      options.bottomSizes !== undefined
        ? normalizeSizeArray(options.bottomSizes)
        : defaultPrefs.bottomSizes,
    shoeSizes:
      options.shoeSizes !== undefined
        ? normalizeSizeArray(options.shoeSizes)
        : defaultPrefs.shoeSizes,
  };

  const allowedGenders = resolveAllowedGenders(
    options.genders,
    defaultPrefs.targetGenders
  );

  const allActive = productsResult.rows.map(normalizeNumericProduct);

  let latestProductSeen: string | null = null;
  const genderScoped: ProductRow[] = [];
  for (const item of allActive) {
    if (
      item.last_seen_at &&
      (!latestProductSeen || item.last_seen_at > latestProductSeen)
    ) {
      latestProductSeen = item.last_seen_at;
    }
    if (!allowedGenders || allowedGenders.has(item.gender)) {
      genderScoped.push(item);
    }
  }

  const matchesCoreFilters = (
    item: ProductRow,
    opts: {
      applyBrand: boolean;
      applyCartOnly: boolean;
      applyHideUnknown: boolean;
    }
  ): boolean => {
    if (
      opts.applyBrand &&
      brandParam &&
      brandParam !== "all" &&
      item.brand !== brandParam
    ) {
      return false;
    }

    if (
      categoryParam &&
      categoryParam !== "all" &&
      VALID_CATEGORIES.has(categoryParam as ProductCategory) &&
      item.category !== categoryParam
    ) {
      return false;
    }

    if (realOnly && !item.is_real_discount) {
      return false;
    }

    if (
      opts.applyCartOnly &&
      cartOnly &&
      item.cart_price === null &&
      !item.promo_badge
    ) {
      return false;
    }

    if (minDiscount > 0 && getEffectiveDiscountPercent(item) < minDiscount) {
      return false;
    }

    if (
      opts.applyHideUnknown &&
      hideUnknownSize &&
      item.available_sizes.length === 0
    ) {
      return false;
    }

    // When filtering by user's sizes, unknown-size products remain visible by default
    // (marked as "Beden sitede kontrol") unless `hideUnknownSize` is also active.
    if (onlyMySize) {
      if (item.available_sizes.length === 0) {
        if (opts.applyHideUnknown && hideUnknownSize) {
          return false;
        }
      } else {
        const targets = getTargetSizesForCategory(item.category, effectiveSizes);
        if (
          targets.length > 0 &&
          !matchesUserSizes(item.available_sizes, targets, item.category)
        ) {
          return false;
        }
      }
    }

    if (queryText) {
      const matchesText =
        item.title.toLowerCase().includes(queryText) ||
        item.brand.toLowerCase().includes(queryText) ||
        getBrandLabel(item.brand).toLowerCase().includes(queryText) ||
        (item.fabric_info || "").toLowerCase().includes(queryText) ||
        (item.promo_badge || "").toLowerCase().includes(queryText);
      if (!matchesText) return false;
    }

    return true;
  };

  // Compute per-brand counts across all matched items (excluding the active brand filter itself)
  const brandCounts: Record<string, number> = {};
  const brandsSet = new Set<string>();
  const filtered: ProductRow[] = [];
  let cartPromoCount = 0;
  let unknownSizeCount = 0;
  let realDiscountCount = 0;
  let criticalStockCount = 0;

  for (const item of genderScoped) {
    const matchesWithoutBrand = matchesCoreFilters(item, {
      applyBrand: false,
      applyCartOnly: true,
      applyHideUnknown: true,
    });

    if (matchesWithoutBrand && item.brand) {
      brandsSet.add(item.brand);
      brandCounts[item.brand] = (brandCounts[item.brand] || 0) + 1;
    }

    // Toggle badge counters stay stable while toggling cartOnly / hideUnknownSize
    if (
      matchesCoreFilters(item, {
        applyBrand: true,
        applyCartOnly: false,
        applyHideUnknown: true,
      }) &&
      (item.cart_price !== null || Boolean(item.promo_badge))
    ) {
      cartPromoCount += 1;
    }

    if (
      matchesCoreFilters(item, {
        applyBrand: true,
        applyCartOnly: true,
        applyHideUnknown: false,
      }) &&
      item.available_sizes.length === 0
    ) {
      unknownSizeCount += 1;
    }

    if (
      matchesWithoutBrand &&
      (!brandParam || brandParam === "all" || item.brand === brandParam)
    ) {
      filtered.push(item);
      if (item.is_real_discount) realDiscountCount += 1;
      if (item.is_critical_stock) criticalStockCount += 1;
    }
  }

  if (brandParam && brandParam !== "all" && !brandsSet.has(brandParam)) {
    brandsSet.add(brandParam);
    brandCounts[brandParam] = brandCounts[brandParam] || 0;
  }

  filtered.sort((a, b) => {
    switch (sortParam) {
      case "newest":
        return Date.parse(b.last_seen_at) - Date.parse(a.last_seen_at);
      case "price_asc":
        return getEffectivePrice(a) - getEffectivePrice(b);
      case "ai_score": {
        const scoreA = a.ai_score ?? -1;
        const scoreB = b.ai_score ?? -1;
        if (scoreB !== scoreA) return scoreB - scoreA;
        return getEffectiveDiscountPercent(b) - getEffectiveDiscountPercent(a);
      }
      case "discount":
      default: {
        const discDiff =
          getEffectiveDiscountPercent(b) - getEffectiveDiscountPercent(a);
        if (discDiff !== 0) {
          return discDiff;
        }
        if (Number(b.is_real_discount) !== Number(a.is_real_discount)) {
          return Number(b.is_real_discount) - Number(a.is_real_discount);
        }
        return getEffectivePrice(a) - getEffectivePrice(b);
      }
    }
  });

  const totalCount = filtered.length;
  const start = (page - 1) * pageSize;
  const end = start + pageSize;
  const pagedProducts = filtered.slice(start, end);
  const hasMore = end < totalCount;

  const latestLogEntry = latestLogs && latestLogs[0];
  const latestScrapedAt =
    (latestLogEntry?.finished_at as string | null) ||
    (latestLogEntry?.started_at as string | null) ||
    latestProductSeen;

  return {
    data: {
      products: pagedProducts,
      brands: Array.from(brandsSet).sort(),
      brandCounts,
      targetGenders: defaultPrefs.targetGenders,
      defaultPrefs,
      page,
      pageSize,
      hasMore,
      totalCount,
      realDiscountCount,
      criticalStockCount,
      stats: {
        totalActive: totalCount,
        totalCatalog: genderScoped.length,
        realDiscountCount,
        criticalStockCount,
        cartPromoCount,
        unknownSizeCount,
        latestScrapedAt,
      },
    },
    error: null,
  };
}
