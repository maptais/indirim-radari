import "server-only";

import {
  CATEGORY_ORDER,
  DEFAULT_CATEGORY_THRESHOLDS,
  DEFAULT_TARGET_GENDERS,
  GENDER_ORDER,
} from "@/lib/constants";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import type {
  CategoryThresholds,
  ProductCategory,
  ProductGender,
  UserSettingsRow,
} from "@/types/database";

const VALID_GENDERS = new Set<ProductGender>(GENDER_ORDER);

// Minimal columns required by SettingsForm and /api/settings (avoids SELECT *)
export const USER_SETTINGS_COLUMNS = [
  "id",
  "target_genders",
  "top_sizes",
  "bottom_sizes",
  "shoe_sizes",
  "negative_keywords",
  "max_polyester_percent",
  "excluded_fabrics",
  "category_thresholds",
  "style_profile",
  "min_ai_notify_score",
  "price_history_lookback_days",
  "notifications_enabled",
].join(",");

export function sanitizeTargetGenders(input: unknown): ProductGender[] {
  if (!Array.isArray(input)) return [...DEFAULT_TARGET_GENDERS];
  const seen = new Set<ProductGender>();
  const result: ProductGender[] = [];
  for (const raw of input) {
    if (typeof raw !== "string") continue;
    const normalized = raw.trim().toLowerCase() as ProductGender;
    if (VALID_GENDERS.has(normalized) && !seen.has(normalized)) {
      seen.add(normalized);
      result.push(normalized);
    }
  }
  return result.length > 0 ? result : [...DEFAULT_TARGET_GENDERS];
}

export function sanitizeStringArray(
  input: unknown,
  transform: (value: string) => string
): string[] {
  if (!Array.isArray(input)) return [];
  const seen = new Set<string>();
  const result: string[] = [];
  for (const raw of input) {
    if (typeof raw !== "string") continue;
    const cleaned = transform(raw.trim());
    if (cleaned && !seen.has(cleaned)) {
      seen.add(cleaned);
      result.push(cleaned);
    }
  }
  return result;
}

export function clampInteger(
  input: unknown,
  min: number,
  max: number,
  fallback: number
): number {
  const numeric = Number(input);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.min(max, Math.max(min, Math.round(numeric)));
}

export function sanitizeThresholds(input: unknown): CategoryThresholds {
  const source =
    input && typeof input === "object"
      ? (input as Record<string, Record<string, unknown>>)
      : {};

  const result = { ...DEFAULT_CATEGORY_THRESHOLDS };

  for (const category of CATEGORY_ORDER) {
    const fallback = DEFAULT_CATEGORY_THRESHOLDS[category];
    const entry = source[category];
    if (entry && typeof entry === "object") {
      const maxPriceRaw = Number(entry.max_price_try);
      result[category as ProductCategory] = {
        min_discount_percent: clampInteger(
          entry.min_discount_percent,
          0,
          100,
          fallback.min_discount_percent
        ),
        max_price_try:
          Number.isFinite(maxPriceRaw) && maxPriceRaw >= 0
            ? Math.round(maxPriceRaw)
            : fallback.max_price_try,
      };
    }
  }

  return result;
}

export function normalizeSettingsRow(
  row: Record<string, unknown>
): UserSettingsRow {
  return {
    id: 1,
    target_genders: sanitizeTargetGenders(row.target_genders),
    top_sizes: sanitizeStringArray(row.top_sizes, (v) => v.toUpperCase()),
    bottom_sizes: sanitizeStringArray(row.bottom_sizes, (v) => v.toUpperCase()),
    shoe_sizes: sanitizeStringArray(row.shoe_sizes, (v) => v.toUpperCase()),
    negative_keywords: sanitizeStringArray(row.negative_keywords, (v) =>
      v.toLocaleLowerCase("tr-TR")
    ),
    max_polyester_percent: clampInteger(row.max_polyester_percent, 0, 100, 30),
    excluded_fabrics: sanitizeStringArray(row.excluded_fabrics, (v) =>
      v.toLocaleLowerCase("tr-TR")
    ),
    category_thresholds: sanitizeThresholds(row.category_thresholds),
    style_profile:
      typeof row.style_profile === "string" ? row.style_profile : "",
    min_ai_notify_score: clampInteger(row.min_ai_notify_score, 1, 10, 7),
    price_history_lookback_days: clampInteger(
      row.price_history_lookback_days,
      7,
      365,
      45
    ),
    notifications_enabled: Boolean(row.notifications_enabled ?? true),
    created_at:
      typeof row.created_at === "string" ? row.created_at : undefined,
    updated_at:
      typeof row.updated_at === "string" ? row.updated_at : undefined,
  };
}

export interface SettingsPageData {
  settings: UserSettingsRow | null;
  activeSubscriptionCount: number;
  error: string | null;
}

/**
 * Fetch user_settings (minimal columns) and active push_subscriptions count
 * in parallel with Promise.all.
 */
export async function loadSettingsPageData(): Promise<SettingsPageData> {
  try {
    const supabase = getSupabaseAdmin();
    const [settingsRes, subscriptionCountRes] = await Promise.all([
      supabase
        .from("user_settings")
        .select(USER_SETTINGS_COLUMNS)
        .eq("id", 1)
        .limit(1)
        .maybeSingle(),
      supabase
        .from("push_subscriptions")
        .select("id", { count: "exact", head: true })
        .eq("is_active", true),
    ]);

    const activeSubscriptionCount = subscriptionCountRes.error
      ? 0
      : (subscriptionCountRes.count ?? 0);

    if (settingsRes.error) {
      return {
        settings: null,
        activeSubscriptionCount,
        error: `Ayarlar okunamadı: ${settingsRes.error.message}`,
      };
    }

    if (!settingsRes.data) {
      return {
        settings: null,
        activeSubscriptionCount,
        error: "user_settings (id=1) kaydı bulunamadı.",
      };
    }

    return {
      settings: normalizeSettingsRow(
        settingsRes.data as unknown as Record<string, unknown>
      ),
      activeSubscriptionCount,
      error: null,
    };
  } catch (err) {
    return {
      settings: null,
      activeSubscriptionCount: 0,
      error:
        err instanceof Error
          ? err.message
          : "Ayarlar veritabanından yüklenemedi.",
    };
  }
}
