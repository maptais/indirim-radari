"""Unit tests for cart discount (`cart_price`), promo badges (`promo_badge`), and effective discount logic."""

from __future__ import annotations

import unittest
from decimal import Decimal

from scraper.core.config import CategoryThreshold, UserSettings
from scraper.core.filters import ProductFilter
from scraper.core.http import (
    is_conditional_promo,
    parse_cart_price_from_badge,
    resolve_cart_promo,
)
from scraper.core.notifier import PushNotifier
from scraper.core.price_analyzer import PriceAnalyzer
from scraper.engines.base import ProductCategory, ProductGender, ProductItem
from scraper.engines.eren import ErenScraper
from scraper.engines.independents.defacto import DeFactoScraper
from scraper.engines.independents.instreet import InStreetScraper
from scraper.engines.independents.sneaksup import SneaksUpScraper
from scraper.engines.trendyol import TrendyolScraper


class TestCartPromoBadgeParsing(unittest.TestCase):
    """Verify Turkish cart discount parsing and multi-buy/conditional exclusion rules."""

    def test_single_item_cart_percentage_and_price_parsing(self) -> None:
        self.assertEqual(
            parse_cart_price_from_badge(Decimal("1000.00"), "Sepette %20 İndirim"),
            Decimal("800.00"),
        )
        self.assertEqual(
            parse_cart_price_from_badge(Decimal("1000.00"), "Sepette Ek %15"),
            Decimal("850.00"),
        )
        self.assertEqual(
            parse_cart_price_from_badge(Decimal("1200.00"), "Sepette 25% İndirim"),
            Decimal("900.00"),
        )
        self.assertEqual(
            parse_cart_price_from_badge(Decimal("1299.00"), "Sepette 899 TL"),
            Decimal("899.00"),
        )
        self.assertEqual(
            parse_cart_price_from_badge(Decimal("1599.90"), "Sepette Fiyat: 1.299,90 TL"),
            Decimal("1299.90"),
        )
        self.assertEqual(
            parse_cart_price_from_badge(Decimal("1000.00"), "Sepette 200 TL İndirim"),
            Decimal("800.00"),
        )

    def test_multi_buy_and_conditional_promos_are_excluded_from_cart_price(self) -> None:
        conditional_badges = [
            "2. Ürüne %50",
            "2. Ürüne Sepette %50 İndirim",
            "İkinci Ürüne Sepette %40",
            "3 Al 2 Öde",
            "2 Al 1 Öde",
            "2 Adet ve Üzeri Sepette %20",
            "2000 TL ve Üzeri Sepette %20 İndirim",
            "1.500 TL Üzeri Sepette %15",
            "Sepette %50'ye Varan İndirim",
            "Sepette %40'a Kadar İndirim",
            "Kupon Kodu ile Sepette %15",
            "Uygulamaya Özel Sepette %10",
            "İlk Siparişe Özel Sepette %20",
            "Kargo Bedava",
        ]
        for badge in conditional_badges:
            self.assertTrue(is_conditional_promo(badge), f"Expected conditional: {badge}")
            cart_price, kept_badge = resolve_cart_promo(Decimal("1000.00"), promo_badge=badge)
            self.assertIsNone(cart_price, f"Multi-buy/conditional badge must not set cart_price: {badge}")
            self.assertEqual(kept_badge, badge)

    def test_explicit_cart_price_synthesizes_badge_when_missing(self) -> None:
        cart_price, badge = resolve_cart_promo(
            Decimal("1000.00"),
            explicit_cart_price=Decimal("750.00"),
            promo_badge=None,
        )
        self.assertEqual(cart_price, Decimal("750.00"))
        self.assertEqual(badge, "Sepette %25 İndirim")

    def test_effective_discount_filter_price_analyzer_and_push_notification(self) -> None:
        # Storefront markdown is only 21% (1649 -> 1299), below 35% threshold,
        # but cart_price=899 brings total effective discount to 45% (and price <= 1500 TL).
        product = ProductItem(
            brand="defacto",
            external_id="df-cart-1",
            title="Relaxed Fit Pamuklu Kazak",
            category=ProductCategory.TOPS,
            gender=ProductGender.MEN,
            url="https://www.defacto.com.tr/test-kazak",
            current_price=Decimal("1299.00"),
            original_price=Decimal("1649.00"),
            cart_price=Decimal("899.00"),
            promo_badge="Sepette 899 TL",
            available_sizes=["M", "L"],
        )
        self.assertEqual(product.effective_price, Decimal("899.00"))
        self.assertEqual(product.discount_percent, 45)
        payload_db = product.to_db_payload()
        self.assertEqual(payload_db["cart_price"], 899.0)
        self.assertEqual(payload_db["promo_badge"], "Sepette 899 TL")

        settings = UserSettings(
            target_genders=["men", "unisex"],
            top_sizes=["M", "L"],
            category_thresholds={
                "tops": CategoryThreshold(min_discount_percent=35, max_price_try=Decimal("1000")),
            },
        )
        pf = ProductFilter(settings)
        f_res = pf.evaluate(product)
        self.assertTrue(f_res.passed, f_res.reasons)

        # PriceAnalyzer uses effective_price and recognizes real cart markdown
        analyzer = PriceAnalyzer(db=None)
        analysis = analyzer.analyze_from_history(product, [])
        self.assertTrue(analysis.is_real_discount)
        self.assertEqual(analysis.historical_min_price, Decimal("899.00"))

        # Web Push explicitly states cart price & total discount percent
        push_payload = PushNotifier.build_product_payload(
            product,
            product_id="uuid-cart-1",
            matched_sizes=["M", "L"],
            lowest_price_days=30,
            ai_score=8,
        )
        self.assertIn("Sepette 899 TL (%45 indirim", push_payload.body)
        self.assertIn("1.649 TL", push_payload.body)

        # Cart-only discount (no list original_price, only cart_price < current_price)
        cart_only_item = ProductItem(
            brand="superstep",
            external_id="ss-cart-only",
            title="New Balance Erkek Sneaker",
            category=ProductCategory.SHOES,
            gender=ProductGender.MEN,
            url="https://www.superstep.com.tr/test",
            current_price=Decimal("5000.00"),
            original_price=None,
            cart_price=Decimal("3000.00"),
            promo_badge="SEPETTE %40 İNDİRİM",
            available_sizes=["42"],
        )
        self.assertEqual(cart_only_item.discount_percent, 40)
        self.assertTrue(analyzer.analyze_from_history(cart_only_item, []).is_real_discount)


class TestEngineCartPromoExtraction(unittest.TestCase):
    """Verify cart_price & promo_badge extraction across major brand engines."""

    def test_akinon_single_vs_multibuy_basket_offers(self) -> None:
        scraper = ErenScraper("superstep", section="men", fetch_variant_details=False)

        # Single-item basket offer (quantity=1) even when price == retail_price
        raw_single = {
            "pk": 1001,
            "name": "Nike Erkek Sneaker",
            "price": "5000",
            "retail_price": "5000",
            "absolute_url": "/nike-erkek-sneaker/",
            "in_stock": True,
            "basket_offers": [
                {
                    "label": "SEPETTE %40 İNDİRİM",
                    "listing_kwargs": {"quantity": 1, "discounted_total_price": 3000.0},
                }
            ],
            "attributes": {"integration_cinsiyet": "Erkek", "integration_size": "42"},
        }
        item_single = scraper.parse_listing_product(raw_single, category_path="/indirim/")
        self.assertIsNotNone(item_single)
        assert item_single is not None
        self.assertEqual(item_single.current_price, Decimal("5000.00"))
        self.assertEqual(item_single.cart_price, Decimal("3000.00"))
        self.assertEqual(item_single.promo_badge, "SEPETTE %40 İNDİRİM")
        self.assertEqual(item_single.discount_percent, 40)

        # Multi-buy offer (quantity=2 / "2. Ürüne %50") -> keep promo_badge, NO cart_price
        raw_multibuy = {
            "pk": 1002,
            "name": "Puma Erkek Tişört",
            "price": "800",
            "retail_price": "1200",
            "absolute_url": "/puma-erkek-tisort/",
            "in_stock": True,
            "basket_offers": [
                {
                    "label": "2. Ürüne %50 İndirim",
                    "listing_kwargs": {"quantity": 2, "discounted_total_price": 600.0},
                }
            ],
            "attributes": {"integration_cinsiyet": "Erkek", "integration_size": "M"},
        }
        item_multi = scraper.parse_listing_product(raw_multibuy, category_path="/indirim/")
        self.assertIsNotNone(item_multi)
        assert item_multi is not None
        self.assertIsNone(item_multi.cart_price)
        self.assertEqual(item_multi.promo_badge, "2. Ürüne %50 İndirim")
        self.assertEqual(item_multi.discount_percent, 33)
        scraper.close()

    def test_defacto_campaign_badge_and_cart_price(self) -> None:
        scraper = DeFactoScraper(section="men")
        doc = {
            "LongCode": "DF999",
            "Name": "Regular Fit Pamuklu Sweatshirt",
            "SeoName": "regular-fit-sweatshirt",
            "ProductVariantIndex": 999,
            "Price": 1299.99,
            "DiscountedPrice": 1299.99,
            "CampaignDiscountedPrice": 753.99,
            "CampaignBadge": {
                "CampaignDescription": "Sepette",
                "DiscountAmount": 42,
                "DiscountPrice": 753.99,
            },
            "PersonaDescriptionName": "Erkek",
            "Sizes": [{"SizeName": "M", "StockQuantity": 5}],
        }
        item = scraper.parse_document(doc, category_path="/erkek-indirimli-urunler")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.current_price, Decimal("1299.99"))
        self.assertEqual(item.cart_price, Decimal("753.99"))
        self.assertEqual(item.promo_badge, "Sepette %42 İndirim")
        self.assertEqual(item.discount_percent, 42)
        scraper.close()

    def test_trendyol_basket_price_and_promotion_badge(self) -> None:
        scraper = TrendyolScraper("trendyolman", section="men", fetch_variant_details=False)
        raw = {
            "id": 123456,
            "name": "Erkek Relaxed Fit Gömlek",
            "url": "/trendyol-man/erkek-gomlek-p-123456",
            "price": {
                "originalPrice": 1200.0,
                "sellingPrice": 1000.0,
                "discountedPrice": 750.0,
            },
            "promotions": [{"name": "Sepette %25 İndirim"}],
            "variants": [{"value": "M", "inStock": True}],
        }
        item = scraper.parse_listing_product(raw, category_path="/erkek-giyim-x-g2-c82")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.current_price, Decimal("1000.00"))
        self.assertEqual(item.original_price, Decimal("1200.00"))
        self.assertEqual(item.cart_price, Decimal("750.00"))
        self.assertEqual(item.promo_badge, "Sepette %25 İndirim")
        self.assertEqual(item.discount_percent, 38)
        scraper.close()

    def test_instreet_ignores_shipping_badges_and_parses_cart_badge(self) -> None:
        scraper = InStreetScraper(section="men")
        raw = {
            "brandName": "Nike",
            "productId": "1",
            "sku": "IS-1",
            "price": 2000,
            "oldPrice": 2500,
            "name": "Erkek Sneaker",
            "relativeUrl": "/urun/nike-erkek-sneaker",
            "isInStock": True,
            "animatedBadgeList": [
                {"badgeText": "Kargo Bedava"},
                {"badgeText": "Yeni Sezon"},
                {"badgeText": "Sepette Ek %20 İndirim"},
            ],
            "options": [{"option_value": "42", "is_in_stock": True, "qty": 4}],
        }
        item = scraper.parse_product(raw)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.cart_price, Decimal("1600.00"))
        self.assertEqual(item.promo_badge, "Sepette Ek %20 İndirim")
        self.assertEqual(item.discount_percent, 36)
        scraper.close()

    def test_sneaksup_special_price_maps_to_cart_price(self) -> None:
        scraper = SneaksUpScraper(section="men")
        raw = {
            "variantId": 9001,
            "productName": "Nike Air Max Erkek Sneaker",
            "slug": "nike-air-max-p-9001",
            "breadcrumb": [{"name": "ERKEK"}, {"name": "AYAKKABI"}],
            "price": {"oldPrice": 5000, "newPrice": 4000, "specialPrice": 3000},
            "variants": [{"specValueName": "42", "quantity": 5}],
        }
        item = scraper.parse_product(raw, category_path="/sneaks-out-erkek")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.current_price, Decimal("4000.00"))
        self.assertEqual(item.original_price, Decimal("5000.00"))
        self.assertEqual(item.cart_price, Decimal("3000.00"))
        self.assertEqual(item.discount_percent, 40)
        scraper.close()


if __name__ == "__main__":
    unittest.main()
