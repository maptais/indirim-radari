import { revalidatePath } from "next/cache";
import { NextRequest, NextResponse } from "next/server";

import { invalidateProductsCache } from "@/lib/products-server";
import {
  USER_SETTINGS_COLUMNS,
  clampInteger,
  normalizeSettingsRow,
  sanitizeStringArray,
  sanitizeTargetGenders,
  sanitizeThresholds,
} from "@/lib/settings-server";
import { getSupabaseAdmin } from "@/lib/supabase-server";

export const dynamic = "force-dynamic";

const NO_CACHE_HEADERS = {
  "Cache-Control": "no-store, no-cache, must-revalidate",
  Pragma: "no-cache",
  Expires: "0",
} as const;

export async function GET() {
  try {
    const supabase = getSupabaseAdmin();
    const { data, error } = await supabase
      .from("user_settings")
      .select(USER_SETTINGS_COLUMNS)
      .eq("id", 1)
      .limit(1)
      .maybeSingle();

    if (error) {
      return NextResponse.json(
        { error: "Ayarlar veritabanından okunamadı.", details: error.message },
        { status: 500, headers: NO_CACHE_HEADERS }
      );
    }

    if (!data) {
      return NextResponse.json(
        {
          error:
            "user_settings (id=1) başlangıç kaydı bulunamadı. Lütfen supabase/schema.sql dosyasını çalıştırın.",
        },
        { status: 404, headers: NO_CACHE_HEADERS }
      );
    }

    return NextResponse.json(
      {
        settings: normalizeSettingsRow(
          data as unknown as Record<string, unknown>
        ),
      },
      { headers: NO_CACHE_HEADERS }
    );
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Bilinmeyen sunucu hatası";
    return NextResponse.json(
      { error: "Ayarlar yüklenirken bir hata oluştu.", details: message },
      { status: 500, headers: NO_CACHE_HEADERS }
    );
  }
}

export async function PUT(request: NextRequest) {
  try {
    const body = (await request.json()) as Record<string, unknown>;
    if (!body || typeof body !== "object") {
      return NextResponse.json(
        { error: "Geçersiz ayar verisi gönderildi." },
        { status: 400, headers: NO_CACHE_HEADERS }
      );
    }

    const payload = {
      id: 1,
      target_genders: sanitizeTargetGenders(body.target_genders),
      top_sizes: sanitizeStringArray(body.top_sizes, (v) => v.toUpperCase()),
      bottom_sizes: sanitizeStringArray(body.bottom_sizes, (v) =>
        v.toUpperCase()
      ),
      shoe_sizes: sanitizeStringArray(body.shoe_sizes, (v) => v.toUpperCase()),
      negative_keywords: sanitizeStringArray(body.negative_keywords, (v) =>
        v.toLocaleLowerCase("tr-TR")
      ),
      max_polyester_percent: clampInteger(
        body.max_polyester_percent,
        0,
        100,
        30
      ),
      excluded_fabrics: sanitizeStringArray(body.excluded_fabrics, (v) =>
        v.toLocaleLowerCase("tr-TR")
      ),
      category_thresholds: sanitizeThresholds(body.category_thresholds),
      style_profile:
        typeof body.style_profile === "string"
          ? body.style_profile.trim().slice(0, 2000)
          : "",
      min_ai_notify_score: clampInteger(body.min_ai_notify_score, 1, 10, 7),
      price_history_lookback_days: clampInteger(
        body.price_history_lookback_days,
        7,
        365,
        45
      ),
      notifications_enabled: Boolean(body.notifications_enabled),
    };

    const supabase = getSupabaseAdmin();
    const { data, error } = await supabase
      .from("user_settings")
      .upsert(payload, { onConflict: "id" })
      .select(USER_SETTINGS_COLUMNS)
      .single();

    if (error) {
      return NextResponse.json(
        { error: "Ayarlar kaydedilemedi.", details: error.message },
        { status: 500, headers: NO_CACHE_HEADERS }
      );
    }

    invalidateProductsCache();
    revalidatePath("/settings");
    revalidatePath("/");

    return NextResponse.json(
      {
        message: "Ayarlarınız başarıyla kaydedildi.",
        settings: normalizeSettingsRow(
          data as unknown as Record<string, unknown>
        ),
      },
      { headers: NO_CACHE_HEADERS }
    );
  } catch (error) {
    const message =
      error instanceof Error ? error.message : "Bilinmeyen sunucu hatası";
    return NextResponse.json(
      { error: "Ayarlar güncellenirken bir hata oluştu.", details: message },
      { status: 500, headers: NO_CACHE_HEADERS }
    );
  }
}
