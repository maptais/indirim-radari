import type {
  CategoryThresholds,
  ProductCategory,
  ScrapeRunMode,
} from "@/types/database";

export interface BrandMeta {
  slug: string;
  label: string;
  group: string;
  defaultRunMode: ScrapeRunMode;
}

export const BRAND_CATALOG: BrandMeta[] = [
  // Inditex (5)
  { slug: "zara", label: "Zara", group: "Inditex", defaultRunMode: "cloud" },
  { slug: "pullandbear", label: "Pull&Bear", group: "Inditex", defaultRunMode: "cloud" },
  { slug: "bershka", label: "Bershka", group: "Inditex", defaultRunMode: "cloud" },
  { slug: "lefties", label: "Lefties", group: "Inditex", defaultRunMode: "cloud" },
  { slug: "massimodutti", label: "Massimo Dutti", group: "Inditex", defaultRunMode: "cloud" },
  // Eren Perakende (3)
  { slug: "superstep", label: "SuperStep", group: "Eren Perakende", defaultRunMode: "cloud" },
  { slug: "occasion", label: "Occasion", group: "Eren Perakende", defaultRunMode: "cloud" },
  { slug: "fashfed", label: "FashFed", group: "Eren Perakende", defaultRunMode: "cloud" },
  // Mango (2)
  { slug: "mangoman", label: "Mango Man", group: "Mango", defaultRunMode: "cloud" },
  { slug: "mangooutlet", label: "Mango Outlet", group: "Mango", defaultRunMode: "local" },
  // Trendyol (2)
  { slug: "trendyolmilla", label: "Trendyol Milla", group: "Trendyol", defaultRunMode: "cloud" },
  { slug: "trendyolman", label: "Trendyol Man", group: "Trendyol", defaultRunMode: "cloud" },
  // Bağımsız Giyim (4)
  { slug: "hm", label: "H&M", group: "Bağımsız Giyim", defaultRunMode: "cloud" },
  { slug: "defacto", label: "DeFacto", group: "Bağımsız Giyim", defaultRunMode: "cloud" },
  { slug: "mavi", label: "Mavi", group: "Bağımsız Giyim", defaultRunMode: "cloud" },
  { slug: "jackjones", label: "Jack & Jones", group: "Bağımsız Giyim", defaultRunMode: "cloud" },
  // Bağımsız Ayakkabı & Outlet (6)
  { slug: "instreet", label: "In Street", group: "Ayakkabı & Outlet", defaultRunMode: "cloud" },
  { slug: "sneaksup", label: "Sneaks Up", group: "Ayakkabı & Outlet", defaultRunMode: "cloud" },
  { slug: "barcin", label: "Barçın", group: "Ayakkabı & Outlet", defaultRunMode: "cloud" },
  { slug: "sportive", label: "Sportive", group: "Ayakkabı & Outlet", defaultRunMode: "cloud" },
  { slug: "adidasoutlet", label: "Adidas Outlet", group: "Ayakkabı & Outlet", defaultRunMode: "local" },
  { slug: "pumaoutlet", label: "Puma Outlet", group: "Ayakkabı & Outlet", defaultRunMode: "cloud" },
];

export const CATEGORY_ORDER: ProductCategory[] = [
  "tops",
  "bottoms",
  "outerwear",
  "shoes",
  "other",
];

export const CATEGORY_LABELS: Record<ProductCategory, string> = {
  shoes: "Ayakkabı",
  tops: "Üst Giyim",
  bottoms: "Alt Giyim",
  outerwear: "Dış Giyim",
  other: "Diğer",
};

export const BRAND_LABELS: Record<string, string> = {
  zara: "Zara",
  pullandbear: "Pull&Bear",
  bershka: "Bershka",
  lefties: "Lefties",
  massimodutti: "Massimo Dutti",
  superstep: "SuperStep",
  occasion: "Occasion",
  fashfed: "FashFed",
  mangoman: "Mango Man",
  mangooutlet: "Mango Outlet",
  trendyolmilla: "Trendyol Milla",
  trendyolman: "Trendyol Man",
  hm: "H&M",
  defacto: "DeFacto",
  mavi: "Mavi",
  jackjones: "Jack & Jones",
  instreet: "In Street",
  sneaksup: "Sneaks Up",
  barcin: "Barçın",
  sportive: "Sportive",
  adidasoutlet: "Adidas Outlet",
  pumaoutlet: "Puma Outlet",
};

export const DEFAULT_CATEGORY_THRESHOLDS: CategoryThresholds = {
  shoes: { min_discount_percent: 30, max_price_try: 3500 },
  tops: { min_discount_percent: 35, max_price_try: 1500 },
  bottoms: { min_discount_percent: 35, max_price_try: 2000 },
  outerwear: { min_discount_percent: 40, max_price_try: 4500 },
  other: { min_discount_percent: 40, max_price_try: 2000 },
};

export const TOP_SIZE_SUGGESTIONS = ["XS", "S", "M", "L", "XL", "XXL"];
export const BOTTOM_SIZE_SUGGESTIONS = [
  "28",
  "29",
  "30",
  "31",
  "32",
  "33",
  "34",
  "36",
  "S",
  "M",
  "L",
  "XL",
];
export const SHOE_SIZE_SUGGESTIONS = [
  "39",
  "40",
  "41",
  "42",
  "42.5",
  "43",
  "44",
  "44.5",
  "45",
  "46",
];

export function getBrandLabel(slug: string): string {
  const normalized = slug.trim().toLowerCase();
  if (BRAND_LABELS[normalized]) {
    return BRAND_LABELS[normalized];
  }
  return slug
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function getCategoryLabel(category: ProductCategory | string): string {
  return CATEGORY_LABELS[category as ProductCategory] || "Diğer";
}

export function formatPriceTry(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  const numeric = typeof value === "string" ? Number(value) : value;
  if (!Number.isFinite(numeric)) {
    return "—";
  }
  const hasDecimals = Math.abs(numeric % 1) > 0.001;
  return new Intl.NumberFormat("tr-TR", {
    style: "currency",
    currency: "TRY",
    minimumFractionDigits: hasDecimals ? 2 : 0,
    maximumFractionDigits: hasDecimals ? 2 : 0,
  }).format(numeric);
}

export function formatRelativeTimeTr(isoDate: string | null | undefined): string {
  if (!isoDate) return "Henüz yok";
  const timestamp = Date.parse(isoDate);
  if (Number.isNaN(timestamp)) return "Bilinmiyor";

  const diffSeconds = Math.max(0, Math.floor((Date.now() - timestamp) / 1000));
  if (diffSeconds < 60) return "Az önce";
  const diffMinutes = Math.floor(diffSeconds / 60);
  if (diffMinutes < 60) return `${diffMinutes} dk önce`;
  const diffHours = Math.floor(diffMinutes / 60);
  if (diffHours < 24) return `${diffHours} sa önce`;
  const diffDays = Math.floor(diffHours / 24);
  if (diffDays < 30) return `${diffDays} gün önce`;

  return new Intl.DateTimeFormat("tr-TR", {
    day: "numeric",
    month: "short",
  }).format(new Date(timestamp));
}

export function formatDurationMsTr(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) {
    return "—";
  }
  if (ms < 1000) {
    return `${Math.round(ms)} ms`;
  }
  const seconds = ms / 1000;
  if (seconds < 60) {
    return `${seconds.toFixed(1)} sn`;
  }
  const mins = Math.floor(seconds / 60);
  const remSec = Math.round(seconds % 60);
  return `${mins} dk ${remSec} sn`;
}
