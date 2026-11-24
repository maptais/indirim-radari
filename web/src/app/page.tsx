import { DealsFeed } from "@/components/DealsFeed";
import { DEFAULT_TARGET_GENDERS, GENDER_ORDER } from "@/lib/constants";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import type {
  ProductGender,
  ProductRow,
  ProductsApiResponse,
} from "@/types/database";

export const dynamic = "force-dynamic";
export const revalidate = 0;

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

async function loadInitialProducts(): Promise<{
  data: ProductsApiResponse;
  error: string | null;
}> {
  const empty: ProductsApiResponse = {
    products: [],
    brands: [],
    targetGenders: [...DEFAULT_TARGET_GENDERS],
    stats: {
      totalActive: 0,
      realDiscountCount: 0,
      criticalStockCount: 0,
      cartPromoCount: 0,
      unknownSizeCount: 0,
      latestScrapedAt: null,
    },
  };

  try {
    const supabase = getSupabaseAdmin();
    const [
      { data: rawProducts, error: productsError },
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
      supabase
        .from("scrape_logs")
        .select("finished_at, started_at")
        .order("started_at", { ascending: false })
        .limit(1),
      supabase
        .from("user_settings")
        .select("target_genders")
        .eq("id", 1)
        .limit(1)
        .maybeSingle(),
    ]);

    if (productsError) {
      return {
        data: empty,
        error: `Ürünler alınamadı: ${productsError.message}`,
      };
    }

    const targetGenders = normalizeTargetGenders(userSettingsRow?.target_genders);
    const allowedGenders = new Set<ProductGender>(targetGenders);

    const allProducts: ProductRow[] = (rawProducts || []).map((row) => {
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
        ...(row as ProductRow),
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
          ? row.available_sizes
          : [],
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
      };
    });

    const products = allProducts.filter((item) => allowedGenders.has(item.gender));

    const brandsSet = new Set<string>();
    let realDiscountCount = 0;
    let criticalStockCount = 0;
    let cartPromoCount = 0;
    let unknownSizeCount = 0;
    let latestProductSeen: string | null = null;

    for (const item of products) {
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

    const latestLog = latestLogs && latestLogs[0];
    const latestScrapedAt =
      (latestLog?.finished_at as string | null) ||
      (latestLog?.started_at as string | null) ||
      latestProductSeen;

    return {
      data: {
        products,
        brands: Array.from(brandsSet).sort(),
        targetGenders,
        stats: {
          totalActive: products.length,
          realDiscountCount,
          criticalStockCount,
          cartPromoCount,
          unknownSizeCount,
          latestScrapedAt,
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
          : "Veritabanına bağlanılamadı.",
    };
  }
}

export default async function HomePage() {
  const { data, error } = await loadInitialProducts();
  return <DealsFeed initialData={data} initialError={error} />;
}
