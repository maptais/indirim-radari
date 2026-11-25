"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  EyeOff,
  Flame,
  Loader2,
  Percent,
  Plus,
  Radar,
  RefreshCw,
  RotateCcw,
  Ruler,
  Search,
  ShoppingCart,
  SlidersHorizontal,
  UserCog,
  X,
} from "lucide-react";

import { ProductCard } from "@/components/ProductCard";
import { PushControl } from "@/components/PushControl";
import {
  BOTTOM_SIZE_SUGGESTIONS,
  CATEGORY_LABELS,
  CATEGORY_ORDER,
  DEFAULT_TARGET_GENDERS,
  GENDER_LABELS,
  GENDER_ORDER,
  SHOE_SIZE_SUGGESTIONS,
  TOP_SIZE_SUGGESTIONS,
  formatRelativeTimeTr,
  getBrandLabel,
} from "@/lib/constants";
import type {
  DefaultUserPrefs,
  ProductCategory,
  ProductGender,
  ProductRow,
  ProductsApiResponse,
} from "@/types/database";

interface DealsFeedProps {
  initialData: ProductsApiResponse;
  initialError?: string | null;
}

type SortKey = "discount" | "newest" | "price_asc" | "ai_score";
type GenderSelection = "all" | ProductGender[];

interface LocalDevicePrefs {
  genders: GenderSelection;
  topSizes: string[];
  bottomSizes: string[];
  shoeSizes: string[];
  sort: SortKey;
  minDiscount: number;
  onlyMySize: boolean;
  hideUnknownSize: boolean;
}

interface FetchParams {
  genders: GenderSelection;
  topSizes: string[];
  bottomSizes: string[];
  shoeSizes: string[];
  brand: string;
  category: string;
  realOnly: boolean;
  cartOnly: boolean;
  hideUnknownSize: boolean;
  onlyMySize: boolean;
  minDiscount: number;
  sort: SortKey;
  q: string;
  usePersonalSizes: boolean;
}

const LOCAL_PREFS_KEY = "indirim_radari_prefs_v1";
const PAGE_SIZE = 60;
const AUTO_REFRESH_COOLDOWN_MS = 3000;

const VALID_GENDERS = new Set<ProductGender>(GENDER_ORDER);
const VALID_SORTS = new Set<SortKey>([
  "discount",
  "newest",
  "price_asc",
  "ai_score",
]);

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "discount", label: "En Yüksek İndirim" },
  { value: "newest", label: "En Yeni" },
  { value: "price_asc", label: "En Düşük Fiyat" },
  { value: "ai_score", label: "AI Tarz Puanı" },
];

const MIN_DISCOUNT_PRESETS = [0, 20, 30, 40, 50, 60];

const PERSONAL_SHOE_SUGGESTIONS = Array.from(
  new Set(["36", "37", "38", "38.5", ...SHOE_SIZE_SUGGESTIONS])
);

function sanitizeSizeList(input: unknown): string[] {
  if (!Array.isArray(input)) return [];
  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of input) {
    const cleaned = String(item ?? "").trim().toUpperCase();
    if (cleaned && !seen.has(cleaned)) {
      seen.add(cleaned);
      result.push(cleaned);
    }
  }
  return result;
}

function sanitizeGenderSelection(
  input: unknown,
  fallback: ProductGender[]
): GenderSelection {
  if (input === "all") return "all";
  if (Array.isArray(input)) {
    const seen = new Set<ProductGender>();
    const valid: ProductGender[] = [];
    for (const item of input) {
      const val = String(item ?? "").trim().toLowerCase() as ProductGender;
      if (VALID_GENDERS.has(val) && !seen.has(val)) {
        seen.add(val);
        valid.push(val);
      }
    }
    if (valid.length > 0) {
      return GENDER_ORDER.filter((g) => valid.includes(g));
    }
  }
  return fallback.length > 0 ? [...fallback] : [...DEFAULT_TARGET_GENDERS];
}

function buildDefaultLocalPrefs(defaults: DefaultUserPrefs): LocalDevicePrefs {
  return {
    genders:
      defaults.targetGenders.length > 0
        ? [...defaults.targetGenders]
        : [...DEFAULT_TARGET_GENDERS],
    topSizes: [...defaults.topSizes],
    bottomSizes: [...defaults.bottomSizes],
    shoeSizes: [...defaults.shoeSizes],
    sort: "discount",
    minDiscount: 0,
    onlyMySize: false,
    hideUnknownSize: false,
  };
}

function parseStoredPrefs(
  raw: string,
  fallback: DefaultUserPrefs
): LocalDevicePrefs | null {
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (!parsed || typeof parsed !== "object") return null;

    const rawSort = String(parsed.sort ?? "discount") as SortKey;
    const rawMinDisc = Number(parsed.minDiscount ?? 0);
    const minDiscount = Number.isFinite(rawMinDisc)
      ? Math.min(90, Math.max(0, Math.round(rawMinDisc)))
      : 0;

    return {
      genders: sanitizeGenderSelection(parsed.genders, fallback.targetGenders),
      topSizes: Array.isArray(parsed.topSizes)
        ? sanitizeSizeList(parsed.topSizes)
        : [...fallback.topSizes],
      bottomSizes: Array.isArray(parsed.bottomSizes)
        ? sanitizeSizeList(parsed.bottomSizes)
        : [...fallback.bottomSizes],
      shoeSizes: Array.isArray(parsed.shoeSizes)
        ? sanitizeSizeList(parsed.shoeSizes)
        : [...fallback.shoeSizes],
      sort: VALID_SORTS.has(rawSort) ? rawSort : "discount",
      minDiscount,
      onlyMySize: Boolean(parsed.onlyMySize),
      hideUnknownSize: Boolean(parsed.hideUnknownSize),
    };
  } catch {
    return null;
  }
}

function saveLocalPrefs(prefs: LocalDevicePrefs): void {
  try {
    window.localStorage.setItem(LOCAL_PREFS_KEY, JSON.stringify(prefs));
  } catch {
    // Ignore storage quota or private-mode errors
  }
}

function clearLocalPrefs(): void {
  try {
    window.localStorage.removeItem(LOCAL_PREFS_KEY);
  } catch {
    // Ignore storage errors
  }
}

function gendersEqual(a: GenderSelection, b: GenderSelection): boolean {
  if (a === "all" || b === "all") return a === b;
  if (a.length !== b.length) return false;
  return a.every((val, idx) => val === b[idx]);
}

function formatGenderSummary(selection: GenderSelection): string {
  if (selection === "all") return "Tümü";
  if (selection.length === GENDER_ORDER.length) return "Tümü";
  return selection.map((g) => GENDER_LABELS[g]).join(" · ");
}

interface CompactSizePickerProps {
  label: string;
  values: string[];
  suggestions: string[];
  placeholder: string;
  onChange: (next: string[]) => void;
}

function CompactSizePicker({
  label,
  values,
  suggestions,
  placeholder,
  onChange,
}: CompactSizePickerProps) {
  const [customDraft, setCustomDraft] = useState("");

  const allChips = useMemo(() => {
    const merged = [...suggestions];
    for (const v of values) {
      if (!merged.includes(v)) merged.push(v);
    }
    return merged;
  }, [suggestions, values]);

  const toggle = (size: string) => {
    const normalized = size.trim().toUpperCase();
    if (!normalized) return;
    if (values.includes(normalized)) {
      onChange(values.filter((item) => item !== normalized));
    } else {
      onChange([...values, normalized]);
    }
  };

  const addCustom = () => {
    const tokens = customDraft
      .split(",")
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    if (tokens.length === 0) return;
    const next = [...values];
    for (const token of tokens) {
      if (!next.includes(token)) next.push(token);
    }
    onChange(next);
    setCustomDraft("");
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold text-zinc-300">{label}</span>
        {values.length > 0 && (
          <button
            type="button"
            onClick={() => onChange([])}
            className="text-[11px] text-zinc-500 hover:text-zinc-300"
          >
            Temizle ({values.length})
          </button>
        )}
      </div>

      <div className="flex flex-wrap gap-1.5">
        {allChips.map((chip) => {
          const active = values.includes(chip);
          return (
            <button
              key={chip}
              type="button"
              aria-pressed={active}
              onClick={() => toggle(chip)}
              className={`rounded-xl px-2.5 py-1 text-[11px] font-semibold transition ${
                active
                  ? "bg-emerald-500 text-zinc-950 shadow-sm shadow-emerald-500/20"
                  : "border border-white/10 bg-zinc-950/70 text-zinc-400 hover:border-white/20 hover:text-zinc-200"
              }`}
            >
              {chip}
            </button>
          );
        })}
      </div>

      <div className="flex gap-1.5">
        <input
          type="text"
          value={customDraft}
          onChange={(e) => setCustomDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addCustom();
            }
          }}
          placeholder={placeholder}
          className="h-8 flex-1 rounded-xl border border-white/10 bg-zinc-950/80 px-2.5 text-[11px] text-zinc-100 placeholder:text-zinc-500 focus:border-emerald-500/50 focus:outline-none"
        />
        <button
          type="button"
          onClick={addCustom}
          className="inline-flex h-8 items-center gap-1 rounded-xl border border-white/10 bg-white/5 px-2.5 text-[11px] font-medium text-zinc-200 transition hover:bg-white/10"
        >
          <Plus className="h-3 w-3" />
          <span>Ekle</span>
        </button>
      </div>
    </div>
  );
}

export function DealsFeed({ initialData, initialError = null }: DealsFeedProps) {
  const initialDefaults: DefaultUserPrefs = useMemo(
    () =>
      initialData.defaultPrefs || {
        targetGenders: initialData.targetGenders || [...DEFAULT_TARGET_GENDERS],
        topSizes: [],
        bottomSizes: [],
        shoeSizes: [],
      },
    [initialData.defaultPrefs, initialData.targetGenders]
  );

  const [defaultPrefs, setDefaultPrefs] =
    useState<DefaultUserPrefs>(initialDefaults);

  const [products, setProducts] = useState<ProductRow[]>(initialData.products);
  const [brands, setBrands] = useState<string[]>(initialData.brands);
  const [brandCounts, setBrandCounts] = useState<Record<string, number>>(
    initialData.brandCounts || {}
  );
  const [stats, setStats] = useState(initialData.stats);
  const [totalCount, setTotalCount] = useState<number>(
    initialData.totalCount ?? initialData.stats.totalActive
  );
  const [realDiscountCount, setRealDiscountCount] = useState<number>(
    initialData.realDiscountCount ?? initialData.stats.realDiscountCount
  );
  const [criticalStockCount, setCriticalStockCount] = useState<number>(
    initialData.criticalStockCount ?? initialData.stats.criticalStockCount
  );
  const [page, setPage] = useState<number>(initialData.page || 1);
  const [hasMore, setHasMore] = useState<boolean>(Boolean(initialData.hasMore));

  // Device-local persisted preferences
  const [prefs, setPrefs] = useState<LocalDevicePrefs>(() =>
    buildDefaultLocalPrefs(initialDefaults)
  );
  const [hasLocalPrefs, setHasLocalPrefs] = useState<boolean>(false);

  // Collapsible "Bedenim / Cinsiyet" panel state
  const [prefsPanelOpen, setPrefsPanelOpen] = useState<boolean>(false);
  const [draftPrefs, setDraftPrefs] = useState<LocalDevicePrefs>(() =>
    buildDefaultLocalPrefs(initialDefaults)
  );
  const [prefsFeedback, setPrefsFeedback] = useState<string | null>(null);

  // Ephemeral browse filters (not persisted in localStorage)
  const [selectedBrand, setSelectedBrand] = useState<string>("all");
  const [selectedCategory, setSelectedCategory] = useState<
    ProductCategory | "all"
  >("all");
  const [realOnly, setRealOnly] = useState<boolean>(false);
  const [cartOnly, setCartOnly] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>("");

  const [loading, setLoading] = useState<boolean>(false);
  const [loadingMore, setLoadingMore] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(initialError);

  const hydratedRef = useRef<boolean>(false);
  const userTouchedPrefsRef = useRef<boolean>(false);
  const prefsRef = useRef<LocalDevicePrefs>(prefs);
  prefsRef.current = prefs;
  const inFlightRef = useRef<boolean>(false);
  const lastAutoRefreshRef = useRef<number>(0);
  const requestSeqRef = useRef<number>(0);
  const abortControllerRef = useRef<AbortController | null>(null);

  const currentParamsRef = useRef<FetchParams>({
    genders: prefs.genders,
    topSizes: prefs.topSizes,
    bottomSizes: prefs.bottomSizes,
    shoeSizes: prefs.shoeSizes,
    brand: selectedBrand,
    category: selectedCategory,
    realOnly,
    cartOnly,
    hideUnknownSize: prefs.hideUnknownSize,
    onlyMySize: prefs.onlyMySize,
    minDiscount: prefs.minDiscount,
    sort: prefs.sort,
    q: searchQuery,
    usePersonalSizes: hasLocalPrefs,
  });

  currentParamsRef.current = {
    genders: prefs.genders,
    topSizes: prefs.topSizes,
    bottomSizes: prefs.bottomSizes,
    shoeSizes: prefs.shoeSizes,
    brand: selectedBrand,
    category: selectedCategory,
    realOnly,
    cartOnly,
    hideUnknownSize: prefs.hideUnknownSize,
    onlyMySize: prefs.onlyMySize,
    minDiscount: prefs.minDiscount,
    sort: prefs.sort,
    q: searchQuery,
    usePersonalSizes: hasLocalPrefs,
  };

  const fetchProducts = useCallback(
    async (
      params: FetchParams,
      options: { page?: number; append?: boolean } = {}
    ) => {
      const targetPage = options.page ?? 1;
      const append = Boolean(options.append && targetPage > 1);

      if (!append) {
        abortControllerRef.current?.abort();
      }
      const controller = new AbortController();
      abortControllerRef.current = controller;

      const seq = ++requestSeqRef.current;
      inFlightRef.current = true;
      if (append) {
        setLoadingMore(true);
      } else {
        setLoading(true);
      }
      setError(null);

      try {
        const sp = new URLSearchParams();
        sp.set("page", String(targetPage));
        sp.set("pageSize", String(PAGE_SIZE));

        if (params.genders === "all") {
          sp.set("genders", "all");
        } else if (params.genders.length > 0) {
          sp.set("genders", params.genders.join(","));
        }

        if (params.usePersonalSizes) {
          sp.set("topSizes", params.topSizes.join(","));
          sp.set("bottomSizes", params.bottomSizes.join(","));
          sp.set("shoeSizes", params.shoeSizes.join(","));
        }

        if (params.brand !== "all") sp.set("brand", params.brand);
        if (params.category !== "all") sp.set("category", params.category);
        if (params.realOnly) sp.set("realOnly", "true");
        if (params.cartOnly) sp.set("cartOnly", "true");
        if (params.hideUnknownSize) sp.set("hideUnknownSize", "true");
        if (params.onlyMySize) sp.set("onlyMySize", "true");
        if (params.minDiscount > 0) {
          sp.set("minDiscount", String(params.minDiscount));
        }
        if (params.sort) sp.set("sort", params.sort);
        if (params.q.trim()) sp.set("q", params.q.trim());

        const res = await fetch(`/api/products?${sp.toString()}`, {
          cache: "no-store",
          signal: controller.signal,
          headers: {
            "Cache-Control": "no-cache",
          },
        });
        const data = (await res.json()) as ProductsApiResponse & {
          error?: string;
        };
        if (!res.ok) {
          throw new Error(data.error || "Ürünler yüklenemedi.");
        }

        // Ignore stale out-of-order responses
        if (seq !== requestSeqRef.current) return;

        const incoming = Array.isArray(data.products) ? data.products : [];
        if (append) {
          setProducts((prev) => {
            const seen = new Set(prev.map((item) => item.id));
            const next = [...prev];
            for (const item of incoming) {
              if (!seen.has(item.id)) {
                seen.add(item.id);
                next.push(item);
              }
            }
            return next;
          });
        } else {
          setProducts(incoming);
        }

        setBrands(Array.isArray(data.brands) ? data.brands : []);
        setBrandCounts(data.brandCounts || {});
        if (data.stats) setStats(data.stats);
        setTotalCount(data.totalCount ?? data.stats?.totalActive ?? incoming.length);
        setRealDiscountCount(
          data.realDiscountCount ?? data.stats?.realDiscountCount ?? 0
        );
        setCriticalStockCount(
          data.criticalStockCount ?? data.stats?.criticalStockCount ?? 0
        );
        setPage(data.page || targetPage);
        setHasMore(Boolean(data.hasMore));

        if (data.defaultPrefs) {
          setDefaultPrefs(data.defaultPrefs);
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === "AbortError") {
          return;
        }
        if (seq !== requestSeqRef.current) return;
        setError(
          err instanceof Error
            ? err.message
            : "İndirim akışı alınırken bir hata oluştu."
        );
      } finally {
        if (seq === requestSeqRef.current) {
          inFlightRef.current = false;
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    []
  );

  // Hydrate device-local preferences from localStorage (or initialize from /api/settings if empty)
  useEffect(() => {
    let cancelled = false;

    const hydrate = async () => {
      let storedRaw: string | null = null;
      try {
        storedRaw = window.localStorage.getItem(LOCAL_PREFS_KEY);
      } catch {
        storedRaw = null;
      }

      if (storedRaw) {
        const parsed = parseStoredPrefs(storedRaw, initialDefaults);
        if (parsed) {
          setPrefs(parsed);
          setDraftPrefs(parsed);
          setHasLocalPrefs(true);
          hydratedRef.current = true;
          void fetchProducts(
            {
              ...currentParamsRef.current,
              genders: parsed.genders,
              topSizes: parsed.topSizes,
              bottomSizes: parsed.bottomSizes,
              shoeSizes: parsed.shoeSizes,
              sort: parsed.sort,
              minDiscount: parsed.minDiscount,
              onlyMySize: parsed.onlyMySize,
              hideUnknownSize: parsed.hideUnknownSize,
              usePersonalSizes: true,
            },
            { page: 1, append: false }
          );
          return;
        }
      }

      // localStorage is empty: initialize defaults from /api/settings
      hydratedRef.current = true;
      try {
        const res = await fetch("/api/settings", {
          cache: "no-store",
          headers: { "Cache-Control": "no-cache" },
        });
        if (!res.ok || cancelled) return;
        const json = (await res.json()) as {
          settings?: {
            target_genders?: ProductGender[];
            top_sizes?: string[];
            bottom_sizes?: string[];
            shoe_sizes?: string[];
          };
        };
        if (cancelled || !json.settings) return;

        const parsedTargetGenders = sanitizeGenderSelection(
          json.settings.target_genders,
          initialDefaults.targetGenders
        );
        const freshDefaults: DefaultUserPrefs = {
          targetGenders:
            parsedTargetGenders === "all"
              ? [...GENDER_ORDER]
              : parsedTargetGenders,
          topSizes: sanitizeSizeList(json.settings.top_sizes),
          bottomSizes: sanitizeSizeList(json.settings.bottom_sizes),
          shoeSizes: sanitizeSizeList(json.settings.shoe_sizes),
        };
        setDefaultPrefs(freshDefaults);

        if (!userTouchedPrefsRef.current) {
          const nextPrefs = buildDefaultLocalPrefs(freshDefaults);
          setPrefs(nextPrefs);
          setDraftPrefs(nextPrefs);
          if (
            !gendersEqual(freshDefaults.targetGenders, initialDefaults.targetGenders)
          ) {
            void fetchProducts(
              {
                ...currentParamsRef.current,
                genders: nextPrefs.genders,
                topSizes: nextPrefs.topSizes,
                bottomSizes: nextPrefs.bottomSizes,
                shoeSizes: nextPrefs.shoeSizes,
                usePersonalSizes: false,
              },
              { page: 1, append: false }
            );
          }
        }
      } catch {
        // Fallback silently to initialData.defaultPrefs
      }
    };

    void hydrate();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-fetch from page 1 whenever ephemeral filters change after mount
  const isFirstFilterEffectRef = useRef(true);
  useEffect(() => {
    if (isFirstFilterEffectRef.current) {
      isFirstFilterEffectRef.current = false;
      return;
    }
    if (!hydratedRef.current) return;

    const timer = window.setTimeout(() => {
      void fetchProducts(currentParamsRef.current, { page: 1, append: false });
    }, 180);

    return () => window.clearTimeout(timer);
  }, [
    selectedBrand,
    selectedCategory,
    realOnly,
    cartOnly,
    searchQuery,
    fetchProducts,
  ]);

  // Helper to update & persist personal prefs immediately (for quick controls in the bar)
  const updatePersistedPrefs = useCallback(
    (
      updater: (prev: LocalDevicePrefs) => LocalDevicePrefs,
      ephemeralOverrides?: Partial<
        Pick<FetchParams, "brand" | "category" | "realOnly" | "cartOnly" | "q">
      >
    ) => {
      userTouchedPrefsRef.current = true;
      const next = updater(prefsRef.current);
      prefsRef.current = next;
      saveLocalPrefs(next);
      setPrefs(next);
      setDraftPrefs(next);
      setHasLocalPrefs(true);

      const nextParams: FetchParams = {
        ...currentParamsRef.current,
        ...ephemeralOverrides,
        genders: next.genders,
        topSizes: next.topSizes,
        bottomSizes: next.bottomSizes,
        shoeSizes: next.shoeSizes,
        sort: next.sort,
        minDiscount: next.minDiscount,
        onlyMySize: next.onlyMySize,
        hideUnknownSize: next.hideUnknownSize,
        usePersonalSizes: true,
      };
      currentParamsRef.current = nextParams;
      void fetchProducts(nextParams, { page: 1, append: false });
    },
    [fetchProducts]
  );

  // Auto-refresh when PWA returns to foreground (visibilitychange, focus, iOS bfcache pageshow)
  useEffect(() => {
    const triggerForegroundRefresh = () => {
      const now = Date.now();
      if (
        inFlightRef.current ||
        now - lastAutoRefreshRef.current < AUTO_REFRESH_COOLDOWN_MS
      ) {
        return;
      }
      lastAutoRefreshRef.current = now;
      void fetchProducts(currentParamsRef.current, { page: 1, append: false });
    };

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        triggerForegroundRefresh();
      }
    };

    const handleFocus = () => {
      triggerForegroundRefresh();
    };

    const handlePageShow = (event: PageTransitionEvent) => {
      if (event.persisted) {
        triggerForegroundRefresh();
      }
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("focus", handleFocus);
    window.addEventListener("pageshow", handlePageShow);

    return () => {
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("focus", handleFocus);
      window.removeEventListener("pageshow", handlePageShow);
    };
  }, [fetchProducts]);

  // Clear temporary feedback toast
  useEffect(() => {
    if (!prefsFeedback) return;
    const t = window.setTimeout(() => setPrefsFeedback(null), 2600);
    return () => window.clearTimeout(t);
  }, [prefsFeedback]);

  const toggleQuickGender = (gender: ProductGender) => {
    updatePersistedPrefs((prev) => {
      if (prev.genders === "all") {
        return { ...prev, genders: [gender] };
      }
      if (prev.genders.includes(gender)) {
        const remaining = prev.genders.filter((g) => g !== gender);
        return {
          ...prev,
          genders: remaining.length > 0 ? remaining : "all",
        };
      }
      const next = GENDER_ORDER.filter(
        (g) => g === gender || prev.genders.includes(g)
      );
      return {
        ...prev,
        genders: next.length === GENDER_ORDER.length ? "all" : next,
      };
    });
  };

  const toggleDraftGender = (gender: ProductGender) => {
    setDraftPrefs((prev) => {
      if (prev.genders === "all") {
        return { ...prev, genders: [gender] };
      }
      if (prev.genders.includes(gender)) {
        const remaining = prev.genders.filter((g) => g !== gender);
        return {
          ...prev,
          genders: remaining.length > 0 ? remaining : "all",
        };
      }
      const next = GENDER_ORDER.filter(
        (g) => g === gender || prev.genders.includes(g)
      );
      return {
        ...prev,
        genders: next.length === GENDER_ORDER.length ? "all" : next,
      };
    });
  };

  const openPrefsPanel = () => {
    setDraftPrefs({
      ...prefs,
      genders: prefs.genders === "all" ? "all" : [...prefs.genders],
      topSizes: [...prefs.topSizes],
      bottomSizes: [...prefs.bottomSizes],
      shoeSizes: [...prefs.shoeSizes],
    });
    setPrefsPanelOpen((prev) => !prev);
  };

  const handleSavePrefsPanel = () => {
    userTouchedPrefsRef.current = true;
    const cleaned: LocalDevicePrefs = {
      ...draftPrefs,
      topSizes: sanitizeSizeList(draftPrefs.topSizes),
      bottomSizes: sanitizeSizeList(draftPrefs.bottomSizes),
      shoeSizes: sanitizeSizeList(draftPrefs.shoeSizes),
    };
    saveLocalPrefs(cleaned);
    setHasLocalPrefs(true);
    setPrefs(cleaned);
    setDraftPrefs(cleaned);
    setPrefsPanelOpen(false);
    setPrefsFeedback("Kişisel filtreler bu cihaza kaydedildi.");

    const nextParams: FetchParams = {
      ...currentParamsRef.current,
      genders: cleaned.genders,
      topSizes: cleaned.topSizes,
      bottomSizes: cleaned.bottomSizes,
      shoeSizes: cleaned.shoeSizes,
      sort: cleaned.sort,
      minDiscount: cleaned.minDiscount,
      onlyMySize: cleaned.onlyMySize,
      hideUnknownSize: cleaned.hideUnknownSize,
      usePersonalSizes: true,
    };
    currentParamsRef.current = nextParams;
    void fetchProducts(nextParams, { page: 1, append: false });
  };

  const handleResetPersonalPrefs = () => {
    userTouchedPrefsRef.current = true;
    clearLocalPrefs();
    setHasLocalPrefs(false);
    const restored = buildDefaultLocalPrefs(defaultPrefs);
    setPrefs(restored);
    setDraftPrefs(restored);
    setPrefsPanelOpen(false);
    setPrefsFeedback("Varsayılan ayarlara sıfırlandı.");

    const nextParams: FetchParams = {
      ...currentParamsRef.current,
      genders: restored.genders,
      topSizes: restored.topSizes,
      bottomSizes: restored.bottomSizes,
      shoeSizes: restored.shoeSizes,
      sort: restored.sort,
      minDiscount: restored.minDiscount,
      onlyMySize: restored.onlyMySize,
      hideUnknownSize: restored.hideUnknownSize,
      usePersonalSizes: false,
    };
    currentParamsRef.current = nextParams;
    void fetchProducts(nextParams, { page: 1, append: false });
  };

  const handleLoadMore = () => {
    if (loading || loadingMore || !hasMore) return;
    void fetchProducts(currentParamsRef.current, {
      page: page + 1,
      append: true,
    });
  };

  const allBrandsTotalCount = useMemo(
    () => Object.values(brandCounts).reduce((sum, count) => sum + count, 0),
    [brandCounts]
  );

  const selectedSizeCount =
    prefs.topSizes.length + prefs.bottomSizes.length + prefs.shoeSizes.length;

  const hasActiveFilters = useMemo(
    () =>
      !gendersEqual(prefs.genders, defaultPrefs.targetGenders) ||
      selectedBrand !== "all" ||
      selectedCategory !== "all" ||
      realOnly ||
      cartOnly ||
      prefs.hideUnknownSize ||
      prefs.onlyMySize ||
      prefs.minDiscount > 0 ||
      searchQuery.trim().length > 0,
    [
      prefs.genders,
      prefs.hideUnknownSize,
      prefs.onlyMySize,
      prefs.minDiscount,
      defaultPrefs.targetGenders,
      selectedBrand,
      selectedCategory,
      realOnly,
      cartOnly,
      searchQuery,
    ]
  );

  const resetAllFilters = () => {
    setSelectedBrand("all");
    setSelectedCategory("all");
    setRealOnly(false);
    setCartOnly(false);
    setSearchQuery("");
    updatePersistedPrefs(
      (prev) => ({
        ...prev,
        genders: [...defaultPrefs.targetGenders],
        minDiscount: 0,
        onlyMySize: false,
        hideUnknownSize: false,
        sort: "discount",
      }),
      {
        brand: "all",
        category: "all",
        realOnly: false,
        cartOnly: false,
        q: "",
      }
    );
  };

  const remainingCount = Math.max(0, totalCount - products.length);

  return (
    <div className="flex flex-col">
      {/* Sticky iOS Translucent Header */}
      <header className="sticky top-0 z-30 border-b border-white/10 bg-[#09090b]/85 px-4 pb-3 pt-safe backdrop-blur-xl">
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-2.5">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-2xl bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30">
              <Radar className="h-5 w-5" />
            </div>
            <div className="min-w-0">
              <h1 className="truncate text-base leading-tight font-bold tracking-tight text-white">
                İndirim Radarı
              </h1>
              <p className="truncate text-[11px] text-zinc-400">
                Son tarama: {formatRelativeTimeTr(stats.latestScrapedAt)}
              </p>
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-2">
            <PushControl variant="compact" />
            <button
              type="button"
              onClick={() =>
                void fetchProducts(currentParamsRef.current, {
                  page: 1,
                  append: false,
                })
              }
              disabled={loading}
              aria-label="Listeyi yenile"
              className="inline-flex h-9 items-center gap-1.5 rounded-full border border-emerald-500/35 bg-emerald-500/15 px-3.5 text-xs font-semibold text-emerald-200 shadow-sm shadow-emerald-950/30 transition hover:bg-emerald-500/25 active:scale-[0.98] disabled:opacity-60"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${loading ? "animate-spin text-emerald-300" : ""}`}
              />
              <span>{loading ? "Yenileniyor" : "Yenile"}</span>
            </button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <div className="space-y-4 px-4 pt-4">
        {/* Compact Summary Strip (Real Full-Catalog Counts) */}
        <section
          aria-label="Özet istatistikler"
          className="grid grid-cols-3 gap-2.5"
        >
          <div className="rounded-2xl border border-white/10 bg-zinc-900/60 px-3 py-2.5">
            <p className="text-[11px] text-zinc-400">Aktif Fırsat</p>
            <p className="mt-0.5 text-lg font-bold text-white">
              {totalCount.toLocaleString("tr-TR")}
            </p>
          </div>
          <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/5 px-3 py-2.5">
            <p className="flex items-center gap-1 text-[11px] text-emerald-300/80">
              <CheckCircle2 className="h-3 w-3" />
              Gerçek İndirim
            </p>
            <p className="mt-0.5 text-lg font-bold text-emerald-400">
              {realDiscountCount.toLocaleString("tr-TR")}
            </p>
          </div>
          <div className="rounded-2xl border border-rose-500/20 bg-rose-500/5 px-3 py-2.5">
            <p className="flex items-center gap-1 text-[11px] text-rose-300/80">
              <Flame className="h-3 w-3" />
              Kritik Stok
            </p>
            <p className="mt-0.5 text-lg font-bold text-rose-400">
              {criticalStockCount.toLocaleString("tr-TR")}
            </p>
          </div>
        </section>

        {/* Filter Bar */}
        <section
          aria-label="Filtre çubuğu"
          className="space-y-3 rounded-3xl border border-white/10 bg-zinc-900/60 p-3.5"
        >
          {/* Search + Action Controls */}
          <div className="flex flex-col gap-2.5 sm:flex-row">
            <div className="relative flex-1">
              <Search className="pointer-events-none absolute top-1/2 left-3.5 h-4 w-4 -translate-y-1/2 text-zinc-500" />
              <input
                type="search"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Ürün, marka veya kumaş ara..."
                className="h-10 w-full rounded-2xl border border-white/10 bg-zinc-950/80 pr-9 pl-10 text-sm text-zinc-100 placeholder:text-zinc-500 focus:border-emerald-500/50 focus:outline-none"
              />
              {searchQuery && (
                <button
                  type="button"
                  onClick={() => setSearchQuery("")}
                  aria-label="Aramayı temizle"
                  className="absolute top-1/2 right-3 -translate-y-1/2 text-zinc-500 hover:text-zinc-200"
                >
                  <X className="h-4 w-4" />
                </button>
              )}
            </div>

            <div className="flex flex-wrap items-center gap-2">
              {/* Compact Device-Local Personal Preferences Button */}
              <button
                type="button"
                aria-expanded={prefsPanelOpen}
                onClick={openPrefsPanel}
                className={`inline-flex h-10 shrink-0 items-center gap-1.5 rounded-2xl border px-3.5 text-xs font-semibold transition ${
                  prefsPanelOpen || hasLocalPrefs
                    ? "border-violet-400/50 bg-violet-500/15 text-violet-200"
                    : "border-white/10 bg-zinc-950/70 text-zinc-200 hover:bg-zinc-900"
                }`}
              >
                <UserCog className="h-3.5 w-3.5" />
                <span>Bedenim / Cinsiyet</span>
                {hasLocalPrefs && (
                  <span className="rounded-full bg-violet-400/25 px-1.5 py-0.5 text-[10px] font-bold text-violet-100">
                    Bu cihaz
                  </span>
                )}
                {prefsPanelOpen ? (
                  <ChevronUp className="h-3.5 w-3.5 opacity-80" />
                ) : (
                  <ChevronDown className="h-3.5 w-3.5 opacity-80" />
                )}
              </button>

              <button
                type="button"
                role="switch"
                aria-checked={prefs.onlyMySize}
                onClick={() =>
                  updatePersistedPrefs((prev) => ({
                    ...prev,
                    onlyMySize: !prev.onlyMySize,
                  }))
                }
                className={`inline-flex h-10 shrink-0 items-center gap-1.5 rounded-2xl border px-3.5 text-xs font-semibold transition ${
                  prefs.onlyMySize
                    ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-300"
                    : "border-white/10 bg-zinc-950/70 text-zinc-300 hover:bg-zinc-900"
                }`}
              >
                <Ruler className="h-3.5 w-3.5" />
                <span>
                  Bedenime Uygun
                  {selectedSizeCount > 0 ? ` (${selectedSizeCount})` : ""}
                </span>
              </button>

              <button
                type="button"
                role="switch"
                aria-checked={prefs.hideUnknownSize}
                onClick={() =>
                  updatePersistedPrefs((prev) => ({
                    ...prev,
                    hideUnknownSize: !prev.hideUnknownSize,
                  }))
                }
                className={`inline-flex h-10 shrink-0 items-center gap-1.5 rounded-2xl border px-3.5 text-xs font-semibold transition ${
                  prefs.hideUnknownSize
                    ? "border-sky-400/50 bg-sky-400/15 text-sky-200"
                    : "border-white/10 bg-zinc-950/70 text-zinc-300 hover:bg-zinc-900"
                }`}
              >
                <EyeOff className="h-3.5 w-3.5" />
                <span>
                  Beden bilgisi olmayanları gizle
                  {stats.unknownSizeCount > 0
                    ? ` (${stats.unknownSizeCount.toLocaleString("tr-TR")})`
                    : ""}
                </span>
              </button>

              <button
                type="button"
                role="switch"
                aria-checked={realOnly}
                onClick={() => setRealOnly((prev) => !prev)}
                className={`inline-flex h-10 shrink-0 items-center gap-1.5 rounded-2xl border px-3.5 text-xs font-semibold transition ${
                  realOnly
                    ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-300"
                    : "border-white/10 bg-zinc-950/70 text-zinc-300 hover:bg-zinc-900"
                }`}
              >
                <CheckCircle2 className="h-3.5 w-3.5" />
                <span>Gerçek İndirim</span>
              </button>

              <button
                type="button"
                role="switch"
                aria-checked={cartOnly}
                onClick={() => setCartOnly((prev) => !prev)}
                className={`inline-flex h-10 shrink-0 items-center gap-1.5 rounded-2xl border px-3.5 text-xs font-semibold transition ${
                  cartOnly
                    ? "border-amber-400/50 bg-amber-400/15 text-amber-200"
                    : "border-white/10 bg-zinc-950/70 text-zinc-300 hover:bg-zinc-900"
                }`}
              >
                <ShoppingCart className="h-3.5 w-3.5" />
                <span>
                  Sepette İndirimliler
                  {stats.cartPromoCount > 0
                    ? ` (${stats.cartPromoCount.toLocaleString("tr-TR")})`
                    : ""}
                </span>
              </button>

              <div className="relative flex-1 sm:flex-none">
                <SlidersHorizontal className="pointer-events-none absolute top-1/2 left-3 h-3.5 w-3.5 -translate-y-1/2 text-zinc-500" />
                <select
                  aria-label="Sıralama"
                  value={prefs.sort}
                  onChange={(e) => {
                    const nextSort = e.target.value as SortKey;
                    updatePersistedPrefs((prev) => ({ ...prev, sort: nextSort }));
                  }}
                  className="h-10 w-full appearance-none rounded-2xl border border-white/10 bg-zinc-950/80 pr-7 pl-8 text-xs font-medium text-zinc-200 focus:border-emerald-500/50 focus:outline-none"
                >
                  {SORT_OPTIONS.map((opt) => (
                    <option key={opt.value} value={opt.value}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {/* Feedback banner when saving/resetting device prefs */}
          {prefsFeedback && (
            <div className="flex items-center gap-2 rounded-2xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-2 text-[11px] font-medium text-emerald-200">
              <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-400" />
              <span>{prefsFeedback}</span>
            </div>
          )}

          {/* Collapsible Personal Size / Gender Panel (localStorage) */}
          {prefsPanelOpen && (
            <div className="space-y-4 rounded-2xl border border-violet-400/30 bg-zinc-950/90 p-4">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <h2 className="text-xs font-bold text-white">
                    Kişisel Beden &amp; Cinsiyet Filtresi (Bu Cihaz)
                  </h2>
                  <p className="mt-0.5 text-[11px] leading-relaxed text-zinc-400">
                    Tercihleriniz yalnızca bu tarayıcıda saklanır; ortak{" "}
                    <span className="text-zinc-300">Ayarlar</span> ve bildirim
                    kurallarını etkilemez.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => setPrefsPanelOpen(false)}
                  aria-label="Paneli kapat"
                  className="rounded-lg p-1 text-zinc-400 hover:bg-white/10 hover:text-white"
                >
                  <X className="h-4 w-4" />
                </button>
              </div>

              {/* Draft Gender Selection */}
              <div className="space-y-1.5">
                <span className="text-[11px] font-semibold text-zinc-300">
                  Cinsiyet Reyonları
                </span>
                <div className="flex flex-wrap gap-1.5">
                  <button
                    type="button"
                    aria-pressed={draftPrefs.genders === "all"}
                    onClick={() =>
                      setDraftPrefs((prev) => ({ ...prev, genders: "all" }))
                    }
                    className={`rounded-xl px-3 py-1.5 text-xs font-semibold transition ${
                      draftPrefs.genders === "all"
                        ? "bg-white text-zinc-950"
                        : "border border-white/10 bg-zinc-900/70 text-zinc-300 hover:bg-white/10"
                    }`}
                  >
                    Tümü
                  </button>
                  {GENDER_ORDER.map((gender) => {
                    const active =
                      draftPrefs.genders !== "all" &&
                      draftPrefs.genders.includes(gender);
                    return (
                      <button
                        key={gender}
                        type="button"
                        aria-pressed={active}
                        onClick={() => toggleDraftGender(gender)}
                        className={`inline-flex items-center gap-1 rounded-xl px-3 py-1.5 text-xs font-semibold transition ${
                          active
                            ? "bg-emerald-500 text-zinc-950"
                            : "border border-white/10 bg-zinc-900/70 text-zinc-300 hover:bg-white/10"
                        }`}
                      >
                        {active && <Check className="h-3 w-3" />}
                        <span>{GENDER_LABELS[gender]}</span>
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Draft Minimum Discount */}
              <div className="space-y-1.5">
                <span className="flex items-center gap-1 text-[11px] font-semibold text-zinc-300">
                  <Percent className="h-3 w-3 text-emerald-400" />
                  Minimum İndirim Oranı
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {MIN_DISCOUNT_PRESETS.map((pct) => {
                    const active = draftPrefs.minDiscount === pct;
                    return (
                      <button
                        key={pct}
                        type="button"
                        aria-pressed={active}
                        onClick={() =>
                          setDraftPrefs((prev) => ({
                            ...prev,
                            minDiscount: pct,
                          }))
                        }
                        className={`rounded-xl px-2.5 py-1 text-[11px] font-semibold transition ${
                          active
                            ? "bg-emerald-500 text-zinc-950"
                            : "border border-white/10 bg-zinc-900/70 text-zinc-400 hover:text-zinc-200"
                        }`}
                      >
                        {pct === 0 ? "Tümü (%0)" : `%${pct}+`}
                      </button>
                    );
                  })}
                </div>
              </div>

              {/* Draft Sizes Grid */}
              <div className="grid grid-cols-1 gap-4 border-t border-white/10 pt-3 md:grid-cols-3">
                <CompactSizePicker
                  label="Üst & Dış Giyim Bedenleri"
                  values={draftPrefs.topSizes}
                  suggestions={TOP_SIZE_SUGGESTIONS}
                  placeholder="Örn: M, L, 3XL"
                  onChange={(topSizes) =>
                    setDraftPrefs((prev) => ({ ...prev, topSizes }))
                  }
                />
                <CompactSizePicker
                  label="Alt Giyim Bedenleri"
                  values={draftPrefs.bottomSizes}
                  suggestions={BOTTOM_SIZE_SUGGESTIONS}
                  placeholder="Örn: 32, 33, M"
                  onChange={(bottomSizes) =>
                    setDraftPrefs((prev) => ({ ...prev, bottomSizes }))
                  }
                />
                <CompactSizePicker
                  label="Ayakkabı Numaraları (EU)"
                  values={draftPrefs.shoeSizes}
                  suggestions={PERSONAL_SHOE_SUGGESTIONS}
                  placeholder="Örn: 38, 42.5, 43"
                  onChange={(shoeSizes) =>
                    setDraftPrefs((prev) => ({ ...prev, shoeSizes }))
                  }
                />
              </div>

              {/* Panel Action Buttons */}
              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-white/10 pt-3">
                <button
                  type="button"
                  onClick={handleResetPersonalPrefs}
                  className="inline-flex h-9 items-center gap-1.5 rounded-xl border border-white/10 bg-white/5 px-3 text-xs font-medium text-zinc-300 transition hover:bg-white/10 hover:text-white"
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                  <span>Sıfırla</span>
                </button>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setPrefsPanelOpen(false)}
                    className="h-9 rounded-xl border border-white/10 px-3 text-xs font-medium text-zinc-400 transition hover:bg-white/5 hover:text-zinc-200"
                  >
                    Vazgeç
                  </button>
                  <button
                    type="button"
                    onClick={handleSavePrefsPanel}
                    className="inline-flex h-9 items-center gap-1.5 rounded-xl bg-emerald-500 px-4 text-xs font-semibold text-zinc-950 shadow-sm shadow-emerald-500/20 transition hover:bg-emerald-400"
                  >
                    <Check className="h-3.5 w-3.5" />
                    <span>Kaydet ve Uygula</span>
                  </button>
                </div>
              </div>
            </div>
          )}

          {/* Quick Gender & Min Discount Row */}
          <div className="space-y-1.5">
            <div className="flex items-center justify-between gap-2">
              <span className="text-[11px] font-medium text-zinc-500">
                Cinsiyet ({formatGenderSummary(prefs.genders)})
              </span>
              {prefs.minDiscount > 0 && (
                <button
                  type="button"
                  onClick={() =>
                    updatePersistedPrefs((prev) => ({ ...prev, minDiscount: 0 }))
                  }
                  className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[11px] font-semibold text-emerald-300 hover:bg-emerald-500/20"
                >
                  <span>Min %{prefs.minDiscount}+</span>
                  <X className="h-3 w-3" />
                </button>
              )}
            </div>
            <div className="no-scrollbar flex items-center gap-1.5 overflow-x-auto pb-0.5">
              <button
                type="button"
                onClick={() =>
                  updatePersistedPrefs((prev) => ({ ...prev, genders: "all" }))
                }
                className={`shrink-0 rounded-full px-3 py-1.5 text-xs font-medium transition ${
                  prefs.genders === "all"
                    ? "bg-white text-zinc-950"
                    : "border border-white/10 bg-zinc-950/70 text-zinc-300 hover:bg-white/10"
                }`}
              >
                Tümü
              </button>
              {GENDER_ORDER.map((gender) => {
                const active =
                  prefs.genders !== "all" && prefs.genders.includes(gender);
                return (
                  <button
                    key={gender}
                    type="button"
                    onClick={() => toggleQuickGender(gender)}
                    className={`inline-flex shrink-0 items-center gap-1 rounded-full px-3 py-1.5 text-xs font-medium transition ${
                      active
                        ? "bg-emerald-500 text-zinc-950"
                        : "border border-white/10 bg-zinc-950/70 text-zinc-300 hover:bg-white/10"
                    }`}
                  >
                    {active && <Check className="h-3 w-3" />}
                    <span>{GENDER_LABELS[gender]}</span>
                  </button>
                );
              })}

              <span className="mx-1 h-4 w-px shrink-0 bg-white/10" />

              {MIN_DISCOUNT_PRESETS.filter((p) => p > 0).map((pct) => {
                const active = prefs.minDiscount === pct;
                return (
                  <button
                    key={pct}
                    type="button"
                    onClick={() =>
                      updatePersistedPrefs((prev) => ({
                        ...prev,
                        minDiscount: prev.minDiscount === pct ? 0 : pct,
                      }))
                    }
                    className={`shrink-0 rounded-full px-2.5 py-1.5 text-xs font-medium transition ${
                      active
                        ? "bg-amber-400 text-zinc-950"
                        : "border border-white/10 bg-zinc-950/70 text-zinc-400 hover:bg-white/10 hover:text-zinc-200"
                    }`}
                  >
                    %{pct}+
                  </button>
                );
              })}
            </div>
          </div>

          {/* Brand Chips with Real Counts */}
          <div className="space-y-1.5">
            <span className="text-[11px] font-medium text-zinc-500">Marka</span>
            <div className="no-scrollbar flex items-center gap-1.5 overflow-x-auto pb-0.5">
              <button
                type="button"
                onClick={() => setSelectedBrand("all")}
                className={`shrink-0 rounded-full px-3 py-1.5 text-xs font-medium transition ${
                  selectedBrand === "all"
                    ? "bg-white text-zinc-950"
                    : "border border-white/10 bg-zinc-950/70 text-zinc-300 hover:bg-white/10"
                }`}
              >
                Tüm Markalar
                {allBrandsTotalCount > 0
                  ? ` (${allBrandsTotalCount.toLocaleString("tr-TR")})`
                  : ""}
              </button>
              {brands.map((brand) => {
                const count = brandCounts[brand];
                return (
                  <button
                    key={brand}
                    type="button"
                    onClick={() => setSelectedBrand(brand)}
                    className={`shrink-0 rounded-full px-3 py-1.5 text-xs font-medium transition ${
                      selectedBrand === brand
                        ? "bg-emerald-500 text-zinc-950"
                        : "border border-white/10 bg-zinc-950/70 text-zinc-300 hover:bg-white/10"
                    }`}
                  >
                    {getBrandLabel(brand)}
                    {typeof count === "number"
                      ? ` (${count.toLocaleString("tr-TR")})`
                      : ""}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Category Chips */}
          <div className="space-y-1.5">
            <span className="text-[11px] font-medium text-zinc-500">
              Kategori
            </span>
            <div className="no-scrollbar flex items-center gap-1.5 overflow-x-auto pb-0.5">
              <button
                type="button"
                onClick={() => setSelectedCategory("all")}
                className={`shrink-0 rounded-full px-3 py-1.5 text-xs font-medium transition ${
                  selectedCategory === "all"
                    ? "bg-white text-zinc-950"
                    : "border border-white/10 bg-zinc-950/70 text-zinc-300 hover:bg-white/10"
                }`}
              >
                Tümü
              </button>
              {CATEGORY_ORDER.map((category) => (
                <button
                  key={category}
                  type="button"
                  onClick={() => setSelectedCategory(category)}
                  className={`shrink-0 rounded-full px-3 py-1.5 text-xs font-medium transition ${
                    selectedCategory === category
                      ? "bg-emerald-500 text-zinc-950"
                      : "border border-white/10 bg-zinc-950/70 text-zinc-300 hover:bg-white/10"
                  }`}
                >
                  {CATEGORY_LABELS[category]}
                </button>
              ))}
            </div>
          </div>
        </section>

        {/* Active filter result count */}
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-zinc-400">
          <span>
            {totalCount > products.length ? (
              <>
                <strong className="text-zinc-200">
                  {totalCount.toLocaleString("tr-TR")}
                </strong>{" "}
                fırsattan{" "}
                <strong className="text-zinc-200">
                  {products.length.toLocaleString("tr-TR")}
                </strong>{" "}
                tanesi gösteriliyor
              </>
            ) : (
              <>
                <strong className="text-zinc-200">
                  {totalCount.toLocaleString("tr-TR")}
                </strong>{" "}
                ürün listeleniyor
              </>
            )}
          </span>
          {hasActiveFilters && (
            <button
              type="button"
              onClick={resetAllFilters}
              className="font-medium text-emerald-400 hover:text-emerald-300"
            >
              Filtreleri temizle
            </button>
          )}
        </div>

        {/* Error Alert */}
        {error && (
          <div className="rounded-2xl border border-rose-500/30 bg-rose-500/10 p-4 text-xs text-rose-200">
            {error}
          </div>
        )}

        {/* Product Grid */}
        {loading && products.length === 0 ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, idx) => (
              <div
                key={idx}
                className="h-[460px] animate-pulse rounded-3xl border border-white/5 bg-zinc-900/60"
              />
            ))}
          </div>
        ) : products.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-3xl border border-white/10 bg-zinc-900/40 px-6 py-16 text-center">
            <Radar className="h-10 w-10 text-zinc-600" />
            <h2 className="mt-3 text-base font-semibold text-zinc-200">
              Eşleşen indirim bulunamadı
            </h2>
            <p className="mt-1 max-w-sm text-xs leading-relaxed text-zinc-400">
              Seçili marka, kategori, beden veya indirim filtresine uyan ürün
              yok. Filtreleri genişletebilir veya Bedenim / Cinsiyet panelinden
              tercihlerinizi güncelleyebilirsiniz.
            </p>
            {hasActiveFilters && (
              <button
                type="button"
                onClick={resetAllFilters}
                className="mt-4 rounded-2xl bg-emerald-500 px-4 py-2 text-xs font-semibold text-zinc-950 transition hover:bg-emerald-400"
              >
                Tüm Ürünleri Göster
              </button>
            )}
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {products.map((product) => (
                <ProductCard key={product.id} product={product} />
              ))}
            </div>

            {/* Pagination / Load More Footer */}
            {hasMore && remainingCount > 0 ? (
              <div className="flex flex-col items-center gap-2 pt-2 pb-4">
                <button
                  type="button"
                  onClick={handleLoadMore}
                  disabled={loadingMore || loading}
                  className="inline-flex h-11 items-center justify-center gap-2 rounded-2xl border border-emerald-500/35 bg-emerald-500/15 px-6 text-xs font-semibold text-emerald-200 shadow-sm shadow-emerald-950/30 transition hover:bg-emerald-500/25 active:scale-[0.98] disabled:opacity-60"
                >
                  {loadingMore ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin text-emerald-300" />
                      <span>Daha fazla ürün yükleniyor...</span>
                    </>
                  ) : (
                    <>
                      <ChevronDown className="h-4 w-4" />
                      <span>
                        Daha Fazla Yükle (
                        {remainingCount.toLocaleString("tr-TR")} ürün daha)
                      </span>
                    </>
                  )}
                </button>
              </div>
            ) : (
              totalCount > PAGE_SIZE && (
                <p className="pb-4 text-center text-[11px] text-zinc-500">
                  Tüm {totalCount.toLocaleString("tr-TR")} fırsat listelendi.
                </p>
              )
            )}
          </>
        )}
      </div>
    </div>
  );
}
