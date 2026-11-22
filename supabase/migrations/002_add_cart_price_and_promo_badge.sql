-- ============================================================================
-- Migration 002: Sepette İndirim (cart_price) ve Kampanya Rozeti (promo_badge)
-- ============================================================================
-- Mevcut Supabase veritabanına güvenle (idempotent) uygulanabilir.
-- ============================================================================

ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS cart_price NUMERIC(10, 2);

ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS promo_badge TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_products_cart_price_nonneg'
          AND conrelid = 'public.products'::regclass
    ) THEN
        ALTER TABLE public.products
            ADD CONSTRAINT chk_products_cart_price_nonneg
            CHECK (cart_price IS NULL OR cart_price >= 0);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_products_cart_promo
    ON public.products (is_active, discount_percent DESC)
    WHERE cart_price IS NOT NULL OR promo_badge IS NOT NULL;
