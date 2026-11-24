"use client";

import { useState, type FormEvent } from "react";
import {
  CheckCircle2,
  Clock,
  Filter,
  Loader2,
  Plus,
  Ruler,
  Save,
  Sparkles,
  Tag,
  Users,
  X,
} from "lucide-react";

import { PushControl } from "@/components/PushControl";
import {
  BOTTOM_SIZE_SUGGESTIONS,
  CATEGORY_LABELS,
  CATEGORY_ORDER,
  GENDER_LABELS,
  GENDER_ORDER,
  SHOE_SIZE_SUGGESTIONS,
  TOP_SIZE_SUGGESTIONS,
} from "@/lib/constants";
import type {
  ProductCategory,
  ProductGender,
  UserSettingsRow,
} from "@/types/database";

interface SettingsFormProps {
  initialSettings: UserSettingsRow | null;
  initialError?: string | null;
}

interface ChipInputProps {
  label: string;
  helper?: string;
  values: string[];
  onChange: (next: string[]) => void;
  placeholder: string;
  suggestions?: string[];
  uppercase?: boolean;
}

function ChipInput({
  label,
  helper,
  values,
  onChange,
  placeholder,
  suggestions = [],
  uppercase = false,
}: ChipInputProps) {
  const [draft, setDraft] = useState("");

  const normalize = (raw: string) =>
    uppercase ? raw.trim().toUpperCase() : raw.trim().toLocaleLowerCase("tr-TR");

  const addValue = (raw: string) => {
    const tokens = raw
      .split(",")
      .map(normalize)
      .filter(Boolean);
    if (tokens.length === 0) return;

    const next = [...values];
    for (const token of tokens) {
      if (!next.includes(token)) {
        next.push(token);
      }
    }
    onChange(next);
    setDraft("");
  };

  const removeValue = (item: string) => {
    onChange(values.filter((v) => v !== item));
  };

  const toggleSuggestion = (suggestion: string) => {
    const normalized = normalize(suggestion);
    if (values.includes(normalized)) {
      removeValue(normalized);
    } else {
      onChange([...values, normalized]);
    }
  };

  return (
    <div className="space-y-2.5">
      <div>
        <label className="block text-xs font-semibold text-zinc-200">
          {label}
        </label>
        {helper && <p className="mt-0.5 text-[11px] text-zinc-400">{helper}</p>}
      </div>

      {/* Selected Chips */}
      <div className="flex flex-wrap gap-1.5">
        {values.length === 0 ? (
          <span className="text-xs text-zinc-500 italic">Henüz eklenmedi</span>
        ) : (
          values.map((item) => (
            <span
              key={item}
              className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/15 px-2.5 py-1 text-xs font-medium text-emerald-200"
            >
              <span>{item}</span>
              <button
                type="button"
                onClick={() => removeValue(item)}
                aria-label={`${item} kaldır`}
                className="rounded-full p-0.5 text-emerald-300 hover:bg-emerald-500/30 hover:text-white"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))
        )}
      </div>

      {/* Input to add custom chip */}
      <div className="flex gap-2">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addValue(draft);
            }
          }}
          placeholder={placeholder}
          className="h-10 flex-1 rounded-2xl border border-white/10 bg-zinc-950/80 px-3.5 text-xs text-zinc-100 placeholder:text-zinc-500 focus:border-emerald-500/50 focus:outline-none"
        />
        <button
          type="button"
          onClick={() => addValue(draft)}
          className="inline-flex h-10 items-center gap-1 rounded-2xl border border-white/10 bg-white/5 px-3.5 text-xs font-medium text-zinc-200 transition hover:bg-white/10"
        >
          <Plus className="h-3.5 w-3.5" />
          <span>Ekle</span>
        </button>
      </div>

      {/* Quick Suggestions */}
      {suggestions.length > 0 && (
        <div className="flex flex-wrap gap-1.5 pt-0.5">
          {suggestions.map((suggestion) => {
            const active = values.includes(normalize(suggestion));
            return (
              <button
                key={suggestion}
                type="button"
                onClick={() => toggleSuggestion(suggestion)}
                className={`rounded-lg px-2 py-1 text-[11px] font-medium transition ${
                  active
                    ? "bg-emerald-500 text-zinc-950"
                    : "border border-white/10 bg-zinc-950/60 text-zinc-400 hover:text-zinc-200"
                }`}
              >
                {suggestion}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

export function SettingsForm({
  initialSettings,
  initialError = null,
}: SettingsFormProps) {
  const [settings, setSettings] = useState<UserSettingsRow | null>(
    initialSettings
  );
  const [saving, setSaving] = useState(false);
  const [statusMessage, setStatusMessage] = useState<{
    type: "success" | "error";
    text: string;
  } | null>(
    initialError ? { type: "error", text: initialError } : null
  );

  const toggleTargetGender = (gender: ProductGender) => {
    if (!settings) return;
    const current = settings.target_genders;
    const exists = current.includes(gender);
    if (exists && current.length <= 1) {
      return;
    }
    const next = exists
      ? current.filter((g) => g !== gender)
      : GENDER_ORDER.filter((g) => g === gender || current.includes(g));
    setSettings({ ...settings, target_genders: next });
  };

  const updateThreshold = (
    category: ProductCategory,
    field: "min_discount_percent" | "max_price_try",
    value: number
  ) => {
    if (!settings) return;
    setSettings({
      ...settings,
      category_thresholds: {
        ...settings.category_thresholds,
        [category]: {
          ...settings.category_thresholds[category],
          [field]: Number.isFinite(value) ? value : 0,
        },
      },
    });
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!settings) return;

    setSaving(true);
    setStatusMessage(null);

    try {
      const res = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(settings),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error || "Ayarlar kaydedilemedi.");
      }

      if (data.settings) {
        setSettings(data.settings);
      }
      setStatusMessage({
        type: "success",
        text: data.message || "Ayarlarınız Supabase veritabanına kaydedildi.",
      });
    } catch (error) {
      setStatusMessage({
        type: "error",
        text:
          error instanceof Error
            ? error.message
            : "Kaydetme sırasında beklenmeyen bir hata oluştu.",
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="flex flex-col">
      {/* Header */}
      <header className="sticky top-0 z-30 border-b border-white/10 bg-[#09090b]/85 px-4 pb-3 pt-safe backdrop-blur-xl">
        <div className="flex items-center justify-between gap-3">
          <div>
            <h1 className="text-base font-bold tracking-tight text-white">
              Kişisel Radar Ayarları
            </h1>
            <p className="text-[11px] text-zinc-400">
              Beden, kumaş kuralları, bütçe eşikleri ve AI tarz profili
            </p>
          </div>
          {settings && (
            <button
              type="button"
              onClick={handleSubmit}
              disabled={saving}
              className="inline-flex h-9 items-center gap-1.5 rounded-full bg-emerald-500 px-4 text-xs font-semibold text-zinc-950 shadow-sm shadow-emerald-500/20 transition hover:bg-emerald-400 disabled:opacity-60"
            >
              {saving ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Save className="h-3.5 w-3.5" />
              )}
              <span>Kaydet</span>
            </button>
          )}
        </div>
      </header>

      <div className="space-y-4 px-4 pt-4">
        {/* Web Push Subscription Card */}
        <PushControl variant="card" />

        {statusMessage && (
          <div
            role="status"
            className={`flex items-center gap-2 rounded-2xl px-4 py-3 text-xs font-medium ${
              statusMessage.type === "success"
                ? "border border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
                : "border border-rose-500/30 bg-rose-500/10 text-rose-200"
            }`}
          >
            {statusMessage.type === "success" && (
              <CheckCircle2 className="h-4 w-4 shrink-0" />
            )}
            <span>{statusMessage.text}</span>
          </div>
        )}

        {!settings ? (
          <div className="rounded-3xl border border-white/10 bg-zinc-900/60 p-6 text-center text-xs text-zinc-400">
            Ayarlar yüklenemedi. Lütfen Supabase bağlantınızı kontrol edin.
          </div>
        ) : (
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* 0. Gender Preference */}
            <section className="space-y-4 rounded-3xl border border-white/10 bg-zinc-900/70 p-5">
              <div className="flex items-center gap-2">
                <Users className="h-4 w-4 text-emerald-400" />
                <h2 className="text-base font-semibold text-zinc-100">
                  Cinsiyet Tercihi
                </h2>
              </div>
              <p className="text-xs leading-relaxed text-zinc-400">
                Tarayıcı, AI puanlama, bildirimler ve ana sayfa akışı seçtiğiniz
                reyonlara göre otomatik filtrelenir (en az bir seçenek aktif
                olmalıdır).
              </p>

              <div className="grid grid-cols-3 gap-2.5">
                {GENDER_ORDER.map((gender) => {
                  const selected = settings.target_genders.includes(gender);
                  const isOnlySelected =
                    selected && settings.target_genders.length <= 1;
                  return (
                    <button
                      key={gender}
                      type="button"
                      aria-pressed={selected}
                      disabled={isOnlySelected}
                      onClick={() => toggleTargetGender(gender)}
                      className={`flex items-center justify-center gap-1.5 rounded-2xl border px-3 py-3 text-xs font-semibold transition ${
                        selected
                          ? "border-emerald-500/50 bg-emerald-500/15 text-emerald-200 shadow-sm shadow-emerald-500/10"
                          : "border-white/10 bg-zinc-950/70 text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
                      } ${isOnlySelected ? "cursor-not-allowed opacity-90" : ""}`}
                    >
                      {selected && (
                        <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-400" />
                      )}
                      <span>{GENDER_LABELS[gender]}</span>
                    </button>
                  );
                })}
              </div>
            </section>

            {/* 1. Size Preferences */}
            <section className="space-y-5 rounded-3xl border border-white/10 bg-zinc-900/70 p-5">
              <div className="flex items-center gap-2">
                <Ruler className="h-4 w-4 text-emerald-400" />
                <h2 className="text-base font-semibold text-zinc-100">
                  Beden ve Numara Tercihleri
                </h2>
              </div>

              <ChipInput
                label="Üst Giyim ve Dış Giyim Bedenleri"
                helper="Web Push bildirimleri ve 'Bedenime Uygun' filtresi bu bedenlere göre çalışır."
                values={settings.top_sizes}
                onChange={(top_sizes) => setSettings({ ...settings, top_sizes })}
                placeholder="Örn: M, L (Enter ile ekle)"
                suggestions={TOP_SIZE_SUGGESTIONS}
                uppercase
              />

              <div className="border-t border-white/5 pt-4">
                <ChipInput
                  label="Alt Giyim Bedenleri"
                  helper="Pantolon/şort bel numaraları ve harf bedenleri."
                  values={settings.bottom_sizes}
                  onChange={(bottom_sizes) =>
                    setSettings({ ...settings, bottom_sizes })
                  }
                  placeholder="Örn: 32, 33, M"
                  suggestions={BOTTOM_SIZE_SUGGESTIONS}
                  uppercase
                />
              </div>

              <div className="border-t border-white/5 pt-4">
                <ChipInput
                  label="Ayakkabı Numaraları (EU)"
                  values={settings.shoe_sizes}
                  onChange={(shoe_sizes) =>
                    setSettings({ ...settings, shoe_sizes })
                  }
                  placeholder="Örn: 42, 42.5, 43"
                  suggestions={SHOE_SIZE_SUGGESTIONS}
                  uppercase
                />
              </div>
            </section>

            {/* 2. Negative Keywords & Fabric Rules */}
            <section className="space-y-5 rounded-3xl border border-white/10 bg-zinc-900/70 p-5">
              <div className="flex items-center gap-2">
                <Filter className="h-4 w-4 text-emerald-400" />
                <h2 className="text-base font-semibold text-zinc-100">
                  Negatif Filtre ve Kumaş Kuralları
                </h2>
              </div>

              <ChipInput
                label="Negatif Anahtar Kelimeler"
                helper="Başlıkta bu kelimelerden herhangi biri geçerse ürün otomatik elenir."
                values={settings.negative_keywords}
                onChange={(negative_keywords) =>
                  setSettings({ ...settings, negative_keywords })
                }
                placeholder="Örn: skinny, baskılı, logolu, neon"
              />

              <div className="border-t border-white/5 pt-4">
                <ChipInput
                  label="Yasaklı Kumaşlar"
                  helper="Kumaş içeriğinde bu materyaller yer alıyorsa elenir."
                  values={settings.excluded_fabrics}
                  onChange={(excluded_fabrics) =>
                    setSettings({ ...settings, excluded_fabrics })
                  }
                  placeholder="Örn: akrilik, naylon"
                />
              </div>

              <div className="space-y-2 border-t border-white/5 pt-4">
                <div className="flex items-center justify-between">
                  <label
                    htmlFor="max-polyester"
                    className="text-xs font-semibold text-zinc-200"
                  >
                    Maksimum Polyester Oranı
                  </label>
                  <span className="rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-xs font-bold text-emerald-300">
                    %{settings.max_polyester_percent}
                  </span>
                </div>
                <input
                  id="max-polyester"
                  type="range"
                  min={0}
                  max={100}
                  step={5}
                  value={settings.max_polyester_percent}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      max_polyester_percent: Number(e.target.value),
                    })
                  }
                  className="h-2 w-full cursor-pointer appearance-none rounded-lg bg-zinc-800 accent-emerald-500"
                />
                <p className="text-[11px] text-zinc-400">
                  Polyester yüzdesi bu değerin üstündeki ürünler Web Push bildirimlerinde elenir.
                </p>
              </div>
            </section>

            {/* 3. Category Thresholds */}
            <section className="space-y-4 rounded-3xl border border-white/10 bg-zinc-900/70 p-5">
              <div className="flex items-center gap-2">
                <Tag className="h-4 w-4 text-emerald-400" />
                <h2 className="text-base font-semibold text-zinc-100">
                  Kategori Bazlı İndirim ve Bütçe Eşikleri
                </h2>
              </div>
              <p className="text-xs text-zinc-400">
                Her kategori için minimum indirim yüzdesi ve kabul edilebilir
                maksimum TL bütçesi.
              </p>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {CATEGORY_ORDER.map((category) => {
                  const threshold = settings.category_thresholds[category];
                  return (
                    <div
                      key={category}
                      className="rounded-2xl border border-white/10 bg-zinc-950/60 p-3.5"
                    >
                      <h3 className="text-xs font-semibold text-zinc-100">
                        {CATEGORY_LABELS[category]}
                      </h3>
                      <div className="mt-3 grid grid-cols-2 gap-2.5">
                        <div>
                          <label className="mb-1 block text-[11px] text-zinc-400">
                            Min. İndirim (%)
                          </label>
                          <input
                            type="number"
                            min={0}
                            max={100}
                            value={threshold.min_discount_percent}
                            onChange={(e) =>
                              updateThreshold(
                                category,
                                "min_discount_percent",
                                Number(e.target.value)
                              )
                            }
                            className="h-9 w-full rounded-xl border border-white/10 bg-zinc-900 px-3 text-xs font-semibold text-white focus:border-emerald-500/50 focus:outline-none"
                          />
                        </div>
                        <div>
                          <label className="mb-1 block text-[11px] text-zinc-400">
                            Max. Fiyat (TL)
                          </label>
                          <input
                            type="number"
                            min={0}
                            step={50}
                            value={threshold.max_price_try}
                            onChange={(e) =>
                              updateThreshold(
                                category,
                                "max_price_try",
                                Number(e.target.value)
                              )
                            }
                            className="h-9 w-full rounded-xl border border-white/10 bg-zinc-900 px-3 text-xs font-semibold text-white focus:border-emerald-500/50 focus:outline-none"
                          />
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>

            {/* 4. Price History & AI Style Profile */}
            <section className="space-y-5 rounded-3xl border border-white/10 bg-zinc-900/70 p-5">
              <div className="flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-emerald-400" />
                <h2 className="text-base font-semibold text-zinc-100">
                  Fiyat Hafızası ve AI Tarz Profili
                </h2>
              </div>

              {/* Price lookback days */}
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label
                    htmlFor="lookback-days"
                    className="flex items-center gap-1.5 text-xs font-semibold text-zinc-200"
                  >
                    <Clock className="h-3.5 w-3.5 text-zinc-400" />
                    Fiyat Hafızası Penceresi (Gün)
                  </label>
                  <span className="rounded-full bg-white/10 px-2.5 py-0.5 text-xs font-bold text-zinc-100">
                    Son {settings.price_history_lookback_days} gün
                  </span>
                </div>
                <input
                  id="lookback-days"
                  type="number"
                  min={7}
                  max={365}
                  value={settings.price_history_lookback_days}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      price_history_lookback_days: Number(e.target.value),
                    })
                  }
                  className="h-10 w-full rounded-2xl border border-white/10 bg-zinc-950/80 px-3.5 text-sm font-semibold text-zinc-100 focus:border-emerald-500/50 focus:outline-none sm:w-44"
                />
                <p className="text-[11px] text-zinc-400">
                  Sahte indirim tespiti için fiyat geçmişinin geriye dönük kaç
                  gün inceleneceğini belirler (7–365 gün).
                </p>
              </div>

              {/* Min AI Score */}
              <div className="space-y-2 border-t border-white/5 pt-4">
                <div className="flex items-center justify-between">
                  <label
                    htmlFor="min-ai-score"
                    className="text-xs font-semibold text-zinc-200"
                  >
                    Minimum AI Bildirim Puanı
                  </label>
                  <span className="rounded-full bg-emerald-500/15 px-2.5 py-0.5 text-xs font-bold text-emerald-300">
                    {settings.min_ai_notify_score} / 10
                  </span>
                </div>
                <input
                  id="min-ai-score"
                  type="range"
                  min={1}
                  max={10}
                  step={1}
                  value={settings.min_ai_notify_score}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      min_ai_notify_score: Number(e.target.value),
                    })
                  }
                  className="h-2 w-full cursor-pointer appearance-none rounded-lg bg-zinc-800 accent-emerald-500"
                />
                <p className="text-[11px] text-zinc-400">
                  Gemini AI tarz puanlayıcısı yalnızca bu puan ve üzerindeki
                  ürünler için bildirim gönderir.
                </p>
              </div>

              {/* AI Style Profile Textarea */}
              <div className="space-y-2 border-t border-white/5 pt-4">
                <label
                  htmlFor="style-profile"
                  className="block text-xs font-semibold text-zinc-200"
                >
                  AI Tarz Profili Metni
                </label>
                <textarea
                  id="style-profile"
                  rows={4}
                  value={settings.style_profile}
                  onChange={(e) =>
                    setSettings({ ...settings, style_profile: e.target.value })
                  }
                  placeholder="Beğendiğiniz kesim, kumaş, renk paleti ve stil tercihlerinizi yazın..."
                  className="w-full rounded-2xl border border-white/10 bg-zinc-950/80 p-3.5 text-xs leading-relaxed text-zinc-100 placeholder:text-zinc-500 focus:border-emerald-500/50 focus:outline-none"
                />
              </div>

              {/* Global Notifications Enabled Toggle */}
              <div className="flex items-center justify-between gap-4 border-t border-white/5 pt-4">
                <div>
                  <p className="text-xs font-semibold text-zinc-200">
                    Otomatik İndirim Bildirimleri
                  </p>
                  <p className="text-[11px] text-zinc-400">
                    Kapatıldığında tarayıcı ürünleri kaydeder ancak push
                    bildirimi göndermez.
                  </p>
                </div>
                <button
                  type="button"
                  role="switch"
                  aria-checked={settings.notifications_enabled}
                  onClick={() =>
                    setSettings({
                      ...settings,
                      notifications_enabled: !settings.notifications_enabled,
                    })
                  }
                  className={`relative inline-flex h-7 w-12 shrink-0 items-center rounded-full transition-colors ${
                    settings.notifications_enabled
                      ? "bg-emerald-500"
                      : "bg-zinc-700"
                  }`}
                >
                  <span
                    className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                      settings.notifications_enabled
                        ? "translate-x-6"
                        : "translate-x-1"
                    }`}
                  />
                </button>
              </div>
            </section>

            {/* Primary Save Button */}
            <button
              type="submit"
              disabled={saving}
              className="flex w-full items-center justify-center gap-2 rounded-2xl bg-emerald-500 px-5 py-3.5 text-sm font-semibold text-zinc-950 shadow-lg shadow-emerald-500/20 transition hover:bg-emerald-400 active:scale-[0.99] disabled:opacity-60"
            >
              {saving ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              <span>Ayarları Kaydet</span>
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
