import { NextRequest, NextResponse } from "next/server";

import { DEFAULT_TARGET_GENDERS, GENDER_ORDER } from "@/lib/constants";
import { matchesUserSizes } from "@/lib/size-match";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import type {
  ProductCategory,
  ProductGender,
  ProductRow,
  ProductsApiResponse,
} from "@/types/database";

export const dynamic = "force-dynamic";
export const revalidate = 0;

const NO_CACHE_HEADERS = {
  "Cache-Control": "no-store, no-cache, must-revalidate",
  Pragma: "no-cache",
  Expires: "0",
} as const;

const VALID_CATEGORIES = new Set<ProductCategory>([
  "shoes",
  "tops",
  "bottoms",
  "outerwear",
  "other",
]);

const VALID_GENDERS = new Set<ProductGender>(GENDER_ORDER);

function normalizeGender(raw: unknown): ProductGender {
  const cleaned = String(raw ?? "").trim().toLowerCase() as ProductGender;
  return VALID_GENDERS.has(cleaned) ? cleaned : "unisex";
}

function normalizeTargetGenders(raw: unknown): ProductGender[] {
  if (!Array.isArray(raw)) return [...DEFAULT_TARGET_GENDERS];
  const seen = new Set<ProductGender>();
  const result: ProductGender[] = [];
  for (const item of raw) {
    const val = String(item ?? "").trim().toLowerCase() as ProductGender;
    if (VALID_GENDERS.has(val) && !seen.has(val)) {
      seen.add(val);
      result.push(val);
    }
  }
  return result.length > 0 ? result : [...DEFAULT_TARGET_GENDERS];
}

function normalizeSizeArray(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((item) => String(item ?? "").trim().toUpperCase()).filter(Boolean);
}

function getEffectivePrice(item: ProductRow): number {
  if (
    item.cart_price !== null &&
    item.cart_price > 0 &&
    item.cart_price < item.current_price
  ) {
    return item.cart_price;
  }
  return item.current_price;
}

function getEffectiveDiscountPercent(item: ProductRow): number {
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
    rawCart !== null && Number.isFinite(rawCart) && rawCart > 0 && rawCart < currentPrice
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
      row.historical_min_price !== null && row.historical_min_price !== undefined
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

export async function GET(request: NextRequest) {
  try {
    const supabase = getSupabaseAdmin();
    const { searchParams } = new URL(request.url);

    const brandParam = (searchParams.get("brand") || "").trim().toLowerCase();
    const categoryParam = (searchParams.get("category") || "").trim().toLowerCase();
    const genderParam = (searchParams.get("gender") || "settings").trim().toLowerCase();
    const realOnly = searchParams.get("realOnly") === "true";
    const cartOnly = searchParams.get("cartOnly") === "true";
    const hideUnknownSizes = searchParams.get("hideUnknownSizes") === "true";
    const mySizesOnly = searchParams.get("mySizesOnly") === "true";
    const sortParam = (searchParams.get("sort") || "discount").trim().toLowerCase();
    const queryText = (searchParams.get("q") || "").trim().toLowerCase();

    const needsBrandQuery = Boolean(brandParam && brandParam !== "all");

    const [
      { data: rawProducts, error: productsError },
      { data: brandScopedRaw },
      { data: latestLogs },
      { data: userSettingsRow },
    ] = await Promise.all([
      supabase
        .from("products")
        .select("*")
        .eq("is_active", true)
        .order("discount_percent", { ascending: false })
        .order("last_seen_at", { ascending: false })
        .limit(1000),
      needsBrandQuery
        ? supabase
            .from("products")
            .select("*")
            .eq("is_active", true)
            .eq("brand", brandParam)
            .order("discount_percent", { ascending: false })
            .order("last_seen_at", { ascending: false })
            .limit(1000)
        : Promise.resolve({ data: null }),
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

    if (productsError) {
      return NextResponse.json(
        { error: "Ürünler veritabanından alınamadı.", details: productsError.message },
        { status: 500, headers: NO_CACHE_HEADERS }
      );
    }

    const targetGenders = normalizeTargetGenders(userSettingsRow?.target_genders);
    const userSizes = {
      topSizes: normalizeSizeArray(userSettingsRow?.top_sizes),
      bottomSizes: normalizeSizeArray(userSettingsRow?.bottom_sizes),
      shoeSizes: normalizeSizeArray(userSettingsRow?.shoe_sizes),
    };

    const allActive = (rawProducts || []).map((row) =>
      normalizeNumericProduct(row as Record<string, unknown>)
    );

    const applyGenderScope = (items: ProductRow[]): ProductRow[] => {
      if (genderParam === "settings" || genderParam === "") {
        const allowed = new Set<ProductGender>(targetGenders);
        return items.filter((item) => allowed.has(item.gender));
      }
      if (genderParam !== "all") {
        const requested = genderParam
          .split(",")
          .map((g) => g.trim().toLowerCase())
          .filter((g): g is ProductGender => VALID_GENDERS.has(g as ProductGender));
        if (requested.length > 0) {
          const allowed = new Set<ProductGender>(requested);
          return items.filter((item) => allowed.has(item.gender));
        }
      }
      return items;
    };

    const genderScoped = applyGenderScope(allActive);

    const brandsSet = new Set<string>();
    let realDiscountCount = 0;
    let criticalStockCount = 0;
    let cartPromoCount = 0;
    let unknownSizeCount = 0;
    let latestProductSeen: string | null = null;

    for (const item of genderScoped) {
      if (item.brand) brandsSet.add(item.brand);
      if (item.is_real_discount) realDiscountCount += 1;
      if (item.is_critical_stock) criticalStockCount += 1;
      if (item.cart_price !== null || item.promo_badge) cartPromoCount += 1;
      if (item.available_sizes.length === 0) unknownSizeCount += 1;
      if (
        item.last_seen_at &&
        (!latestProductSeen || item.last_seen_at > latestProductSeen)
      ) {
        latestProductSeen = item.last_seen_at;
      }
    }

    let filtered = genderScoped;

    if (needsBrandQuery) {
      if (Array.isArray(brandScopedRaw)) {
        const brandItems = brandScopedRaw.map((row) =>
          normalizeNumericProduct(row as Record<string, unknown>)
        );
        filtered = applyGenderScope(brandItems);
        if (brandItems.length > 0) brandsSet.add(brandParam);
      } else {
        filtered = filtered.filter((item) => item.brand === brandParam);
      }
    }

    if (
      categoryParam &&
      categoryParam !== "all" &&
      VALID_CATEGORIES.has(categoryParam as ProductCategory)
    ) {
      filtered = filtered.filter((item) => item.category === categoryParam);
    }

    if (realOnly) {
      filtered = filtered.filter((item) => item.is_real_discount);
    }

    if (cartOnly) {
      filtered = filtered.filter(
        (item) => item.cart_price !== null || Boolean(item.promo_badge)
      );
    }

    if (hideUnknownSizes) {
      filtered = filtered.filter((item) => item.available_sizes.length > 0);
    }

    // When filtering by user's sizes, unknown-size products remain visible by default
    // (marked as "Beden sitede kontrol") unless `hideUnknownSizes` is explicitly active.
    if (mySizesOnly) {
      filtered = filtered.filter((item) => {
        if (item.available_sizes.length === 0) {
          return !hideUnknownSizes;
        }
        const targets = getTargetSizesForCategory(item.category, userSizes);
        if (targets.length === 0) return true;
        return matchesUserSizes(item.available_sizes, targets, item.category);
      });
    }

    if (queryText) {
      filtered = filtered.filter(
        (item) =>
          item.title.toLowerCase().includes(queryText) ||
          item.brand.toLowerCase().includes(queryText) ||
          (item.fabric_info || "").toLowerCase().includes(queryText) ||
          (item.promo_badge || "").toLowerCase().includes(queryText)
      );
    }

    filtered = [...filtered].sort((a, b) => {
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

    const latestLogEntry = latestLogs && latestLogs[0];
    const latestScrapedAt =
      (latestLogEntry?.finished_at as string | null) ||
      (latestLogEntry?.started_at as string | null) ||
      latestProductSeen;

    const payload: ProductsApiResponse = {
      products: filtered,
      brands: Array.from(brandsSet).sort(),
      targetGenders,
      stats: {
        totalActive: genderScoped.length,
        realDiscountCount,
        criticalStockCount,
        cartPromoCount,
        unknownSizeCount,
        latestScrapedAt,
      },
    };

    return NextResponse.json(payload, { headers: NO_CACHE_HEADERS });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Bilinmeyen sunucu hatası";
    return NextResponse.json(
      { error: "İndirim akışı yüklenirken bir hata oluştu.", details: message },
      { status: 500, headers: NO_CACHE_HEADERS }
    );
  }
}
