import type { Metadata } from "next";

import { SettingsForm } from "@/components/SettingsForm";
import {
  CATEGORY_ORDER,
  DEFAULT_CATEGORY_THRESHOLDS,
  DEFAULT_TARGET_GENDERS,
  GENDER_ORDER,
} from "@/lib/constants";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import type {
  CategoryThresholds,
  ProductGender,
  UserSettingsRow,
} from "@/types/database";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Ayarlar",
};

async function loadUserSettings(): Promise<{
  settings: UserSettingsRow | null;
  error: string | null;
}> {
  try {
    const supabase = getSupabaseAdmin();
    const { data, error } = await supabase
      .from("user_settings")
      .select("*")
      .eq("id", 1)
      .limit(1)
      .maybeSingle();

    if (error) {
      return {
        settings: null,
        error: `Ayarlar okunamadı: ${error.message}`,
      };
    }

    if (!data) {
      return {
        settings: null,
        error: "user_settings (id=1) kaydı bulunamadı.",
      };
    }

    const rawThresholds =
      (data.category_thresholds as Record<
        string,
        { min_discount_percent?: number; max_price_try?: number }
      >) || {};
    const mergedThresholds: CategoryThresholds = {
      ...DEFAULT_CATEGORY_THRESHOLDS,
    };
    for (const cat of CATEGORY_ORDER) {
      if (rawThresholds[cat]) {
        mergedThresholds[cat] = {
          min_discount_percent: Number(
            rawThresholds[cat].min_discount_percent ??
              DEFAULT_CATEGORY_THRESHOLDS[cat].min_discount_percent
          ),
          max_price_try: Number(
            rawThresholds[cat].max_price_try ??
              DEFAULT_CATEGORY_THRESHOLDS[cat].max_price_try
          ),
        };
      }
    }

    const validGenderSet = new Set<ProductGender>(GENDER_ORDER);
    const parsedGenders = Array.isArray(data.target_genders)
      ? (data.target_genders
          .map((g: unknown) => String(g).trim().toLowerCase())
          .filter((g: string): g is ProductGender =>
            validGenderSet.has(g as ProductGender)
          ) as ProductGender[])
      : [];

    const normalized: UserSettingsRow = {
      id: 1,
      target_genders:
        parsedGenders.length > 0 ? parsedGenders : [...DEFAULT_TARGET_GENDERS],
      top_sizes: Array.isArray(data.top_sizes) ? data.top_sizes : [],
      bottom_sizes: Array.isArray(data.bottom_sizes) ? data.bottom_sizes : [],
      shoe_sizes: Array.isArray(data.shoe_sizes) ? data.shoe_sizes : [],
      negative_keywords: Array.isArray(data.negative_keywords)
        ? data.negative_keywords
        : [],
      max_polyester_percent: Number(data.max_polyester_percent ?? 30),
      excluded_fabrics: Array.isArray(data.excluded_fabrics)
        ? data.excluded_fabrics
        : [],
      category_thresholds: mergedThresholds,
      style_profile: typeof data.style_profile === "string" ? data.style_profile : "",
      min_ai_notify_score: Number(data.min_ai_notify_score ?? 7),
      price_history_lookback_days: Number(data.price_history_lookback_days ?? 45),
      notifications_enabled: Boolean(data.notifications_enabled ?? true),
      created_at: data.created_at,
      updated_at: data.updated_at,
    };

    return { settings: normalized, error: null };
  } catch (err) {
    return {
      settings: null,
      error:
        err instanceof Error
          ? err.message
          : "Ayarlar veritabanından yüklenemedi.",
    };
  }
}

export default async function SettingsPage() {
  const { settings, error } = await loadUserSettings();
  return <SettingsForm initialSettings={settings} initialError={error} />;
}
