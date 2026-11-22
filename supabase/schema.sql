-- ============================================================================
-- İndirim Radarı — Supabase / PostgreSQL Veritabanı Şeması
-- ============================================================================
-- Bu dosyayı Supabase Dashboard -> SQL Editor bölümüne yapıştırıp doğrudan
-- çalıştırabilirsiniz. Tekrar çalıştırılmaya (idempotent) uygundur.
-- ============================================================================

-- UUID üretimi için gerekli eklenti (Supabase'de varsayılan olarak aktiftir)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ----------------------------------------------------------------------------
-- Yardımcı Trigger Fonksiyonu: updated_at otomatik güncelleme
-- ----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

-- ============================================================================
-- 1. products: Tüm markalardan çekilen ve takip edilen ürünler
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.products (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    brand               TEXT NOT NULL,
    external_id         TEXT NOT NULL,
    title               TEXT NOT NULL,
    category            TEXT NOT NULL DEFAULT 'other'
                        CHECK (category IN ('shoes', 'tops', 'bottoms', 'outerwear', 'other')),
    gender              TEXT NOT NULL DEFAULT 'unisex'
                        CHECK (gender IN ('men', 'women', 'unisex')),
    url                 TEXT NOT NULL,
    image_url           TEXT,
    current_price       NUMERIC(10, 2) NOT NULL CHECK (current_price >= 0),
    original_price      NUMERIC(10, 2) CHECK (original_price IS NULL OR original_price >= 0),
    discount_percent    SMALLINT NOT NULL DEFAULT 0 CHECK (discount_percent BETWEEN 0 AND 100),
    currency            TEXT NOT NULL DEFAULT 'TRY',
    available_sizes     TEXT[] NOT NULL DEFAULT '{}',
    is_critical_stock   BOOLEAN NOT NULL DEFAULT FALSE,
    fabric_info         TEXT,
    polyester_percent   SMALLINT CHECK (polyester_percent IS NULL OR polyester_percent BETWEEN 0 AND 100),
    is_real_discount    BOOLEAN NOT NULL DEFAULT FALSE,
    lowest_price_days   INTEGER CHECK (lowest_price_days IS NULL OR lowest_price_days >= 0),
    historical_min_price NUMERIC(10, 2) CHECK (historical_min_price IS NULL OR historical_min_price >= 0),
    ai_score            SMALLINT CHECK (ai_score IS NULL OR ai_score BETWEEN 1 AND 10),
    ai_comment          TEXT,
    notified_at         TIMESTAMPTZ,
    last_notified_price NUMERIC(10, 2) CHECK (last_notified_price IS NULL OR last_notified_price >= 0),
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    first_seen_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_products_brand_external_id UNIQUE (brand, external_id)
);

CREATE INDEX IF NOT EXISTS idx_products_brand_category
    ON public.products (brand, category);

CREATE INDEX IF NOT EXISTS idx_products_active_gender
    ON public.products (is_active, gender, discount_percent DESC);

CREATE INDEX IF NOT EXISTS idx_products_active_last_seen
    ON public.products (is_active, last_seen_at DESC);

CREATE INDEX IF NOT EXISTS idx_products_real_discount_feed
    ON public.products (is_active, is_real_discount, ai_score DESC NULLS LAST, discount_percent DESC);

CREATE INDEX IF NOT EXISTS idx_products_available_sizes_gin
    ON public.products USING GIN (available_sizes);

DROP TRIGGER IF EXISTS trg_products_set_updated_at ON public.products;
CREATE TRIGGER trg_products_set_updated_at
    BEFORE UPDATE ON public.products
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

-- ============================================================================
-- 2. price_history: Sahte indirim tespiti için ürün fiyat hafızası
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.price_history (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id          UUID NOT NULL REFERENCES public.products(id) ON DELETE CASCADE,
    price               NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
    original_price      NUMERIC(10, 2) CHECK (original_price IS NULL OR original_price >= 0),
    discount_percent    SMALLINT CHECK (discount_percent IS NULL OR discount_percent BETWEEN 0 AND 100),
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_price_history_product_recorded
    ON public.price_history (product_id, recorded_at DESC);

CREATE INDEX IF NOT EXISTS idx_price_history_recorded_at
    ON public.price_history (recorded_at DESC);

-- ============================================================================
-- 3. user_settings: Tek satırlık kişisel beden, filtre, bütçe ve AI profili
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.user_settings (
    id                          SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    target_genders              TEXT[] NOT NULL DEFAULT ARRAY['men', 'unisex']
                                CHECK (
                                    cardinality(target_genders) > 0
                                    AND target_genders <@ ARRAY['men', 'women', 'unisex']::TEXT[]
                                ),
    top_sizes                   TEXT[] NOT NULL DEFAULT ARRAY['M', 'L'],
    bottom_sizes                TEXT[] NOT NULL DEFAULT ARRAY['32', 'M', 'L'],
    shoe_sizes                  TEXT[] NOT NULL DEFAULT ARRAY['42', '42.5', '43'],
    negative_keywords           TEXT[] NOT NULL DEFAULT ARRAY['skinny', 'baskılı', 'logolu', 'taşlı', 'yırtık'],
    max_polyester_percent       SMALLINT NOT NULL DEFAULT 30 CHECK (max_polyester_percent BETWEEN 0 AND 100),
    excluded_fabrics            TEXT[] NOT NULL DEFAULT ARRAY['akrilik'],
    category_thresholds         JSONB NOT NULL DEFAULT '{
        "shoes": {"min_discount_percent": 30, "max_price_try": 3500},
        "tops": {"min_discount_percent": 35, "max_price_try": 1500},
        "bottoms": {"min_discount_percent": 35, "max_price_try": 2000},
        "outerwear": {"min_discount_percent": 40, "max_price_try": 4500},
        "other": {"min_discount_percent": 40, "max_price_try": 2000}
    }'::jsonb,
    style_profile               TEXT NOT NULL DEFAULT 'Minimalist, sade, kaliteli kumaşlı, büyük logosuz ve zamansız kesimler; rahat (regular/relaxed) kalıplar, nötr ve toprak tonları.',
    min_ai_notify_score         SMALLINT NOT NULL DEFAULT 7 CHECK (min_ai_notify_score BETWEEN 1 AND 10),
    price_history_lookback_days SMALLINT NOT NULL DEFAULT 45 CHECK (price_history_lookback_days BETWEEN 7 AND 365),
    notifications_enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

DROP TRIGGER IF EXISTS trg_user_settings_set_updated_at ON public.user_settings;
CREATE TRIGGER trg_user_settings_set_updated_at
    BEFORE UPDATE ON public.user_settings
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

-- ============================================================================
-- 4. push_subscriptions: iOS Safari Web Push (VAPID) abonelik anahtarları
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.push_subscriptions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    endpoint            TEXT NOT NULL UNIQUE,
    p256dh              TEXT NOT NULL,
    auth                TEXT NOT NULL,
    user_agent          TEXT,
    device_label        TEXT,
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    last_notified_at    TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_push_subscriptions_active
    ON public.push_subscriptions (is_active)
    WHERE is_active = TRUE;

DROP TRIGGER IF EXISTS trg_push_subscriptions_set_updated_at ON public.push_subscriptions;
CREATE TRIGGER trg_push_subscriptions_set_updated_at
    BEFORE UPDATE ON public.push_subscriptions
    FOR EACH ROW
    EXECUTE FUNCTION public.set_updated_at();

-- ============================================================================
-- 5. scrape_logs: Marka bazlı tarama çalışma geçmişi ve hata takibi
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.scrape_logs (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    brand               TEXT NOT NULL,
    engine              TEXT NOT NULL,
    run_mode            TEXT NOT NULL DEFAULT 'cloud'
                        CHECK (run_mode IN ('cloud', 'local')),
    status              TEXT NOT NULL
                        CHECK (status IN ('running', 'success', 'partial', 'failed', 'blocked')),
    fetched_count       INTEGER NOT NULL DEFAULT 0 CHECK (fetched_count >= 0),
    matched_count       INTEGER NOT NULL DEFAULT 0 CHECK (matched_count >= 0),
    saved_count         INTEGER NOT NULL DEFAULT 0 CHECK (saved_count >= 0),
    notified_count      INTEGER NOT NULL DEFAULT 0 CHECK (notified_count >= 0),
    duration_ms         INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
    error_message       TEXT,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_scrape_logs_brand_started
    ON public.scrape_logs (brand, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_scrape_logs_status_started
    ON public.scrape_logs (status, started_at DESC);

-- ============================================================================
-- Row Level Security (RLS) Politikaları
-- ============================================================================
-- Python scraper ve Next.js server-side API route'ları `service_role` anahtarı
-- kullanarak RLS'yi aşar. Katalog tabloları istemciye salt-okunur açılır;
-- hassas abonelik ve ayar yazma işlemleri sunucu tarafından yapılır.
ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.price_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.push_subscriptions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scrape_logs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow public read on products" ON public.products;
CREATE POLICY "Allow public read on products"
    ON public.products FOR SELECT
    USING (TRUE);

DROP POLICY IF EXISTS "Allow public read on price_history" ON public.price_history;
CREATE POLICY "Allow public read on price_history"
    ON public.price_history FOR SELECT
    USING (TRUE);

DROP POLICY IF EXISTS "Allow public read on scrape_logs" ON public.scrape_logs;
CREATE POLICY "Allow public read on scrape_logs"
    ON public.scrape_logs FOR SELECT
    USING (TRUE);

-- ============================================================================
-- Seed: Varsayılan kullanıcı ayarları (tek satırlık başlangıç kaydı)
-- Daha sonra Supabase Table Editor veya Faz 3 Ayarlar ekranından güncelleyin.
-- ============================================================================
INSERT INTO public.user_settings (
    id,
    target_genders,
    top_sizes,
    bottom_sizes,
    shoe_sizes,
    negative_keywords,
    max_polyester_percent,
    excluded_fabrics,
    category_thresholds,
    style_profile,
    min_ai_notify_score,
    price_history_lookback_days,
    notifications_enabled
)
VALUES (
    1,
    ARRAY['men', 'unisex'],
    ARRAY['M', 'L'],
    ARRAY['32', '33', 'M', 'L'],
    ARRAY['42', '42.5', '43'],
    ARRAY['skinny', 'super skinny', 'baskılı', 'logolu', 'taşlı', 'yırtık', 'neon'],
    30,
    ARRAY['akrilik', 'naylon'],
    '{
        "shoes": {"min_discount_percent": 30, "max_price_try": 3500},
        "tops": {"min_discount_percent": 35, "max_price_try": 1500},
        "bottoms": {"min_discount_percent": 35, "max_price_try": 2000},
        "outerwear": {"min_discount_percent": 40, "max_price_try": 4500},
        "other": {"min_discount_percent": 40, "max_price_try": 2000}
    }'::jsonb,
    'Minimalist, sade, kaliteli pamuk/keten/yün ağırlıklı, büyük logosuz ve zamansız kesimler; regular veya relaxed kalıp, siyah/beyaz/lacivert/gri/bej ve toprak tonları.',
    7,
    45,
    TRUE
)
ON CONFLICT (id) DO NOTHING;
