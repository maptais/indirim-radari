"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  Flame,
  Radar,
  RefreshCw,
  Search,
  SlidersHorizontal,
  X,
} from "lucide-react";

import { ProductCard } from "@/components/ProductCard";
import { PushControl } from "@/components/PushControl";
import {
  CATEGORY_LABELS,
  CATEGORY_ORDER,
  formatRelativeTimeTr,
  getBrandLabel,
} from "@/lib/constants";
import type {
  ProductCategory,
  ProductRow,
  ProductsApiResponse,
} from "@/types/database";

interface DealsFeedProps {
  initialData: ProductsApiResponse;
  initialError?: string | null;
}

type SortKey = "discount" | "newest" | "price_asc" | "ai_score";

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "discount", label: "En Yüksek İndirim" },
  { value: "newest", label: "En Yeni" },
  { value: "price_asc", label: "En Düşük Fiyat" },
  { value: "ai_score", label: "AI Tarz Puanı" },
];

export function DealsFeed({ initialData, initialError = null }: DealsFeedProps) {
  const [products, setProducts] = useState<ProductRow[]>(initialData.products);
  const [brands, setBrands] = useState<string[]>(initialData.brands);
  const [stats, setStats] = useState(initialData.stats);

  const [selectedBrand, setSelectedBrand] = useState<string>("all");
  const [selectedCategory, setSelectedCategory] = useState<
    ProductCategory | "all"
  >("all");
  const [realOnly, setRealOnly] = useState<boolean>(false);
  const [sortBy, setSortBy] = useState<SortKey>("discount");
  const [searchQuery, setSearchQuery] = useState<string>("");

  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(initialError);
  const hasMountedRef = useRef(false);

  const fetchProducts = useCallback(
    async (params: {
      brand: string;
      category: string;
      realOnly: boolean;
      sort: SortKey;
      q: string;
    }) => {
      setLoading(true);
      setError(null);
      try {
        const sp = new URLSearchParams();
        if (params.brand !== "all") sp.set("brand", params.brand);
        if (params.category !== "all") sp.set("category", params.category);
        if (params.realOnly) sp.set("realOnly", "true");
        if (params.sort) sp.set("sort", params.sort);
        if (params.q.trim()) sp.set("q", params.q.trim());

        const res = await fetch(`/api/products?${sp.toString()}`, {
          cache: "no-store",
        });
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data.error || "Ürünler yüklenemedi.");
        }

        setProducts(data.products || []);
        setBrands(data.brands || []);
        if (data.stats) setStats(data.stats);
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "İndirim akışı alınırken bir hata oluştu."
        );
      } finally {
        setLoading(false);
      }
    },
    []
  );

  useEffect(() => {
    if (!hasMountedRef.current) {
      hasMountedRef.current = true;
      return;
    }

    const timer = window.setTimeout(() => {
      void fetchProducts({
        brand: selectedBrand,
        category: selectedCategory,
        realOnly,
        sort: sortBy,
        q: searchQuery,
      });
    }, 180);

    return () => window.clearTimeout(timer);
  }, [
    selectedBrand,
    selectedCategory,
    realOnly,
    sortBy,
    searchQuery,
    fetchProducts,
  ]);

  const hasActiveFilters = useMemo(
    () =>
      selectedBrand !== "all" ||
      selectedCategory !== "all" ||
      realOnly ||
      searchQuery.trim().length > 0,
    [selectedBrand, selectedCategory, realOnly, searchQuery]
  );

  const resetFilters = () => {
    setSelectedBrand("all");
    setSelectedCategory("all");
    setRealOnly(false);
    setSearchQuery("");
    setSortBy("discount");
  };

  return (
    <div className="flex flex-col">
      {/* Sticky iOS Translucent Header */}
      <header className="sticky top-0 z-30 border-b border-white/10 bg-[#09090b]/85 px-4 pb-3 pt-safe backdrop-blur-xl">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-2xl bg-emerald-500/15 text-emerald-400 ring-1 ring-emerald-500/30">
              <Radar className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-base leading-tight font-bold tracking-tight text-white">
                İndirim Radarı
              </h1>
              <p className="text-[11px] text-zinc-400">
                Son tarama: {formatRelativeTimeTr(stats.latestScrapedAt)}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <PushControl variant="compact" />
            <button
              type="button"
              onClick={() =>
                void fetchProducts({
                  brand: selectedBrand,
                  category: selectedCategory,
                  realOnly,
                  sort: sortBy,
                  q: searchQuery,
                })
              }
              disabled={loading}
              aria-label="Listeyi yenile"
              className="flex h-9 w-9 items-center justify-center rounded-full border border-white/10 bg-white/5 text-zinc-300 transition hover:bg-white/10 disabled:opacity-60"
            >
              <RefreshCw
                className={`h-4 w-4 ${loading ? "animate-spin text-emerald-400" : ""}`}
              />
            </button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <div className="space-y-4 px-4 pt-4">
        {/* Compact Summary Strip */}
        <section
          aria-label="Özet istatistikler"
          className="grid grid-cols-3 gap-2.5"
        >
          <div className="rounded-2xl border border-white/10 bg-zinc-900/60 px-3 py-2.5">
            <p className="text-[11px] text-zinc-400">Aktif Fırsat</p>
            <p className="mt-0.5 text-lg font-bold text-white">
              {stats.totalActive}
            </p>
          </div>
          <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/5 px-3 py-2.5">
            <p className="flex items-center gap-1 text-[11px] text-emerald-300/80">
              <CheckCircle2 className="h-3 w-3" />
              Gerçek İndirim
            </p>
            <p className="mt-0.5 text-lg font-bold text-emerald-400">
              {stats.realDiscountCount}
            </p>
          </div>
          <div className="rounded-2xl border border-rose-500/20 bg-rose-500/5 px-3 py-2.5">
            <p className="flex items-center gap-1 text-[11px] text-rose-300/80">
              <Flame className="h-3 w-3" />
              Kritik Stok
            </p>
            <p className="mt-0.5 text-lg font-bold text-rose-400">
              {stats.criticalStockCount}
            </p>
          </div>
        </section>

        {/* Filter Bar */}
        <section
          aria-label="Filtre çubuğu"
          className="space-y-3 rounded-3xl border border-white/10 bg-zinc-900/60 p-3.5"
        >
          {/* Search + Sort Row */}
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

            <div className="flex items-center gap-2">
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
                <span>Sadece Gerçek İndirimler</span>
              </button>

              <div className="relative flex-1 sm:flex-none">
                <SlidersHorizontal className="pointer-events-none absolute top-1/2 left-3 h-3.5 w-3.5 -translate-y-1/2 text-zinc-500" />
                <select
                  aria-label="Sıralama"
                  value={sortBy}
                  onChange={(e) => setSortBy(e.target.value as SortKey)}
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

          {/* Brand Chips */}
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
              </button>
              {brands.map((brand) => (
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
                </button>
              ))}
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
        <div className="flex items-center justify-between text-xs text-zinc-400">
          <span>
            <strong className="text-zinc-200">{products.length}</strong> ürün
            listeleniyor
          </span>
          {hasActiveFilters && (
            <button
              type="button"
              onClick={resetFilters}
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
              Seçili marka, kategori veya gerçek indirim filtresine uyan ürün
              yok. Filtreleri genişletebilir veya Ayarlar ekranından eşiklerinizi
              güncelleyebilirsiniz.
            </p>
            {hasActiveFilters && (
              <button
                type="button"
                onClick={resetFilters}
                className="mt-4 rounded-2xl bg-emerald-500 px-4 py-2 text-xs font-semibold text-zinc-950 transition hover:bg-emerald-400"
              >
                Tüm Ürünleri Göster
              </button>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {products.map((product) => (
              <ProductCard key={product.id} product={product} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
