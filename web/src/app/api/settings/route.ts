import { NextRequest, NextResponse } from "next/server";

import {
  CATEGORY_ORDER,
  DEFAULT_CATEGORY_THRESHOLDS,
} from "@/lib/constants";
import { getSupabaseAdmin } from "@/lib/supabase-server";
import type {
  CategoryThresholds,
  ProductCategory,
  UserSettingsRow,
} from "@/types/database";

export const dynamic = "force-dynamic";

function sanitizeStringArray(
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

function clampInteger(
  input: unknown,
  min: number,
  max: number,
  fallback: number
): number {
  const numeric = Number(input);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.min(max, Math.max(min, Math.round(numeric)));
}

function sanitizeThresholds(input: unknown): CategoryThresholds {
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

function normalizeSettingsRow(row: Record<string, unknown>): UserSettingsRow {
  return {
    id: 1,
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
    style_profile: typeof row.style_profile === "string" ? row.style_profile : "",
    min_ai_notify_score: clampInteger(row.min_ai_notify_score, 1, 10, 7),
    price_history_lookback_days: clampInteger(
      row.price_history_lookback_days,
      7,
      365,
      45
    ),
    notifications_enabled: Boolean(row.notifications_enabled ?? true),
    created_at: typeof row.created_at === "string" ? row.created_at : undefined,
    updated_at: typeof row.updated_at === "string" ? row.updated_at : undefined,
  };
}

export async function GET() {
  try {
    const supabase = getSupabaseAdmin();
    const { data, error } = await supabase
      .from("user_settings")
      .select("*")
      .eq("id", 1)
      .limit(1)
      .maybeSingle();

    if (error) {
      return NextResponse.json(
        { error: "Ayarlar veritabanından okunamadı.", details: error.message },
        { status: 500 }
      );
    }

    if (!data) {
      return NextResponse.json(
        {
          error:
            "user_settings (id=1) başlangıç kaydı bulunamadı. Lütfen supabase/schema.sql dosyasını çalıştırın.",
        },
        { status: 404 }
      );
    }

    return NextResponse.json({
      settings: normalizeSettingsRow(data as Record<string, unknown>),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Bilinmeyen sunucu hatası";
    return NextResponse.json(
      { error: "Ayarlar yüklenirken bir hata oluştu.", details: message },
      { status: 500 }
    );
  }
}

export async function PUT(request: NextRequest) {
  try {
    const body = (await request.json()) as Record<string, unknown>;
    if (!body || typeof body !== "object") {
      return NextResponse.json(
        { error: "Geçersiz ayar verisi gönderildi." },
        { status: 400 }
      );
    }

    const payload = {
      id: 1,
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
      .select("*")
      .single();

    if (error) {
      return NextResponse.json(
        { error: "Ayarlar kaydedilemedi.", details: error.message },
        { status: 500 }
      );
    }

    return NextResponse.json({
      message: "Ayarlarınız başarıyla kaydedildi.",
      settings: normalizeSettingsRow(data as Record<string, unknown>),
    });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Bilinmeyen sunucu hatası";
    return NextResponse.json(
      { error: "Ayarlar güncellenirken bir hata oluştu.", details: message },
      { status: 500 }
    );
  }
}
