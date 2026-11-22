-- ============================================================================
-- Migration 001: Dinamik Cinsiyet Tercihi Desteği
-- ============================================================================
-- Mevcut Supabase veritabanına güvenle (idempotent) uygulanabilir.
-- ============================================================================

-- 1. products tablosuna gender sütunu ekle
ALTER TABLE public.products
    ADD COLUMN IF NOT EXISTS gender TEXT NOT NULL DEFAULT 'unisex';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_products_gender'
          AND conrelid = 'public.products'::regclass
    ) THEN
        ALTER TABLE public.products
            ADD CONSTRAINT chk_products_gender
            CHECK (gender IN ('men', 'women', 'unisex'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_products_active_gender
    ON public.products (is_active, gender, discount_percent DESC);

-- 2. user_settings tablosuna target_genders sütunu ekle
ALTER TABLE public.user_settings
    ADD COLUMN IF NOT EXISTS target_genders TEXT[] NOT NULL DEFAULT ARRAY['men', 'unisex'];

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_user_settings_target_genders'
          AND conrelid = 'public.user_settings'::regclass
    ) THEN
        ALTER TABLE public.user_settings
            ADD CONSTRAINT chk_user_settings_target_genders
            CHECK (
                cardinality(target_genders) > 0
                AND target_genders <@ ARRAY['men', 'women', 'unisex']::TEXT[]
            );
    END IF;
END $$;

UPDATE public.user_settings
SET target_genders = ARRAY['men', 'unisex']
WHERE id = 1
  AND (target_genders IS NULL OR cardinality(target_genders) = 0);
