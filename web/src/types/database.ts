export type ProductCategory = "shoes" | "tops" | "bottoms" | "outerwear" | "other";

export type ProductGender = "men" | "women" | "unisex";

export interface ProductRow {
  id: string;
  brand: string;
  external_id: string;
  title: string;
  category: ProductCategory;
  gender: ProductGender;
  url: string;
  image_url: string | null;
  current_price: number;
  original_price: number | null;
  discount_percent: number;
  currency: string;
  available_sizes: string[];
  is_critical_stock: boolean;
  fabric_info: string | null;
  polyester_percent: number | null;
  is_real_discount: boolean;
  lowest_price_days: number | null;
  historical_min_price: number | null;
  ai_score: number | null;
  ai_comment: string | null;
  notified_at: string | null;
  last_notified_price: number | null;
  is_active: boolean;
  first_seen_at: string;
  last_seen_at: string;
  created_at: string;
  updated_at: string;
}

export interface CategoryThreshold {
  min_discount_percent: number;
  max_price_try: number;
}

export type CategoryThresholds = Record<ProductCategory, CategoryThreshold>;

export interface UserSettingsRow {
  id: number;
  target_genders: ProductGender[];
  top_sizes: string[];
  bottom_sizes: string[];
  shoe_sizes: string[];
  negative_keywords: string[];
  max_polyester_percent: number;
  excluded_fabrics: string[];
  category_thresholds: CategoryThresholds;
  style_profile: string;
  min_ai_notify_score: number;
  price_history_lookback_days: number;
  notifications_enabled: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface PushSubscriptionRow {
  id: string;
  endpoint: string;
  p256dh: string;
  auth: string;
  user_agent: string | null;
  device_label: string | null;
  is_active: boolean;
  last_notified_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProductsApiResponse {
  products: ProductRow[];
  brands: string[];
  targetGenders: ProductGender[];
  stats: {
    totalActive: number;
    realDiscountCount: number;
    criticalStockCount: number;
    latestScrapedAt: string | null;
  };
}

export type ScrapeRunMode = "cloud" | "local";

export type ScrapeStatus =
  | "running"
  | "success"
  | "partial"
  | "failed"
  | "blocked";

export interface ScrapeLogRow {
  id: number;
  brand: string;
  engine: string;
  run_mode: ScrapeRunMode;
  status: ScrapeStatus;
  fetched_count: number;
  matched_count: number;
  saved_count: number;
  notified_count: number;
  duration_ms: number | null;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
}

export interface BrandStatusItem {
  brand: string;
  label: string;
  group: string;
  default_run_mode: ScrapeRunMode;
  latest_log: ScrapeLogRow | null;
}

export interface StatusApiResponse {
  brands: BrandStatusItem[];
  recentLogs: ScrapeLogRow[];
  summary: {
    totalBrands: number;
    successCount: number;
    partialCount: number;
    blockedCount: number;
    failedCount: number;
    runningCount: number;
    neverRunCount: number;
    latestRunAt: string | null;
  };
}
