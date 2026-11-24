"""Unit tests for relaxed catalog filtering, strict push gating, 72h stale cleanup, deep pagination early-exit, and disjoint matrix groups."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from pydantic import SecretStr

import scraper.main as main_mod
from scraper.core.config import AppSettings, CategoryThreshold, UserSettings
from scraper.core.db import DatabaseClient
from scraper.core.filters import ProductFilter
from scraper.core.notifier import PushNotifier
from scraper.core.price_analyzer import PriceAnalysis
from scraper.engines.base import (
    ProductCategory,
    ProductGender,
    ProductItem,
    ScrapeResult,
    ScrapeStatus,
)
from scraper.engines.eren import EREN_BRAND_CONFIGS, ErenScraper
from scraper.engines.independents import DeFactoScraper, HMScraper, InStreetScraper


def _make_app_settings() -> AppSettings:
    return AppSettings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key=SecretStr("service-role-test-key"),
        gemini_api_key=SecretStr("AIzaSyTestFakeKey1234567890"),
        gemini_model="gemini-2.5-flash",
    )


class TestRelaxedCatalogVsStrictPush(unittest.TestCase):
    """Verify relaxed DB catalog acceptance while keeping strict Web Push filtering."""

    def setUp(self) -> None:
        self.settings = UserSettings(
            target_genders=["men", "unisex"],
            top_sizes=["M", "L"],
            bottom_sizes=["32", "M"],
            shoe_sizes=["42", "42.5", "43"],
            negative_keywords=["skinny", "baskılı"],
            max_polyester_percent=30,
            excluded_fabrics=["akrilik"],
            category_thresholds={
                "tops": CategoryThreshold(min_discount_percent=35, max_price_try=Decimal("1500")),
                "outerwear": CategoryThreshold(min_discount_percent=40, max_price_try=Decimal("4000")),
            },
            min_ai_notify_score=7,
            notifications_enabled=True,
        )
        self.pf = ProductFilter(self.settings)

    def test_catalog_filter_keeps_unknown_sizes_and_base_discounts(self) -> None:
        # 20% off, empty sizes, high polyester -> passes relaxed catalog filter, FAILS strict push filter
        unknown_size_item = ProductItem(
            brand="defacto",
            external_id="df-nosize",
            title="Regular Fit Mont",
            category=ProductCategory.OUTERWEAR,
            gender=ProductGender.MEN,
            url="https://www.defacto.com.tr/regular-fit-mont-1",
            current_price=Decimal("1600.00"),
            original_price=Decimal("2000.00"),
            available_sizes=[],
            fabric_info="%80 Polyester, %20 Pamuk",
        )
        cat_res = self.pf.evaluate_for_catalog(unknown_size_item)
        self.assertTrue(cat_res.passed, cat_res.reasons)

        strict_res = self.pf.evaluate(unknown_size_item)
        self.assertFalse(strict_res.passed)
        self.assertIn("no_sizes_in_stock", strict_res.reasons)
        self.assertTrue(any(r.startswith("discount_below_min") for r in strict_res.reasons))
        self.assertTrue(any(r.startswith("polyester_above_max") for r in strict_res.reasons))

        # PushNotifier MUST reject this product even if it's a real discount with AI score 9
        ok_push, reason = PushNotifier.should_notify_product(
            unknown_size_item,
            analysis=PriceAnalysis(is_real_discount=True, lowest_price_days=30),
            user_settings=self.settings,
            ai_score=9,
        )
        self.assertFalse(ok_push)
        self.assertIn("strict_filter", reason)

    def test_catalog_filter_keeps_cart_or_promo_badge_and_rejects_low_discount_or_wrong_gender(self) -> None:
        promo_only = ProductItem(
            brand="superstep",
            external_id="ss-promo",
            title="Nike Erkek Tişört",
            category=ProductCategory.TOPS,
            gender=ProductGender.MEN,
            url="https://www.superstep.com.tr/test-1",
            current_price=Decimal("900.00"),
            original_price=Decimal("950.00"),  # only 5% list off
            promo_badge="2. Ürüne %50 İndirim",
            available_sizes=[],
        )
        self.assertTrue(self.pf.evaluate_for_catalog(promo_only).passed)

        too_low_discount = ProductItem(
            brand="defacto",
            external_id="df-low",
            title="Basic Erkek Tişört",
            category=ProductCategory.TOPS,
            gender=ProductGender.MEN,
            url="https://www.defacto.com.tr/basic-1",
            current_price=Decimal("900.00"),
            original_price=Decimal("1000.00"),  # 10% < 15%
            available_sizes=["M"],
        )
        low_res = self.pf.evaluate_for_catalog(too_low_discount)
        self.assertFalse(low_res.passed)
        self.assertTrue(any(r.startswith("catalog_discount_below_min") for r in low_res.reasons))

        wrong_gender = ProductItem(
            brand="defacto",
            external_id="df-women",
            title="Kadın Triko Kazak",
            category=ProductCategory.TOPS,
            gender=ProductGender.WOMEN,
            url="https://www.defacto.com.tr/kadin-kazak",
            current_price=Decimal("400.00"),
            original_price=Decimal("1000.00"),
            available_sizes=["M"],
        )
        gender_res = self.pf.evaluate_for_catalog(wrong_gender)
        self.assertFalse(gender_res.passed)
        self.assertIn("gender_mismatch:women", gender_res.reasons)

    def test_pipeline_saves_relaxed_catalog_but_only_scores_and_notifies_strict_matches(self) -> None:
        strict_ok = ProductItem(
            brand="defacto",
            external_id="strict-ok",
            title="Relaxed Fit Pamuklu Kazak",
            category=ProductCategory.TOPS,
            gender=ProductGender.MEN,
            url="https://www.defacto.com.tr/strict-ok",
            current_price=Decimal("600.00"),
            original_price=Decimal("1200.00"),  # 50% >= 35%
            available_sizes=["M", "L"],
            fabric_info="%100 Pamuk",
        )
        catalog_only = ProductItem(
            brand="defacto",
            external_id="catalog-only-nosize",
            title="Regular Fit Triko Kazak",
            category=ProductCategory.TOPS,
            gender=ProductGender.MEN,
            url="https://www.defacto.com.tr/catalog-only",
            current_price=Decimal("800.00"),
            original_price=Decimal("1000.00"),  # 20% >= 15%, < 35%, empty sizes
            available_sizes=[],
        )

        class FakeDB:
            def __init__(self) -> None:
                self.upserted_ids: list[str] = []
                self.cleanup_calls: list[tuple[list[str], int]] = []

            def get_user_settings(self) -> UserSettings:
                return UserSettings(
                    target_genders=["men", "unisex"],
                    top_sizes=["M", "L"],
                    category_thresholds={
                        "tops": CategoryThreshold(min_discount_percent=35, max_price_try=Decimal("1500")),
                    },
                    min_ai_notify_score=7,
                    notifications_enabled=True,
                )

            def start_scrape_log(self, **_kwargs: Any) -> int:
                return 1

            def finish_scrape_log(self, *_args: Any, **_kwargs: Any) -> None:
                return None

            def get_product_by_external_id(self, _b: str, _e: str) -> None:
                return None

            def get_price_history(self, _pid: str, days: int = 45) -> list[dict[str, Any]]:
                return []

            def upsert_product(self, product: ProductItem, **_kwargs: Any) -> dict[str, Any]:
                self.upserted_ids.append(product.external_id)
                return {"id": f"uuid-{product.external_id}", "ai_score": _kwargs.get("ai_score")}

            def record_price_history(self, **_kwargs: Any) -> dict[str, Any]:
                return {"id": 1}

            def cleanup_stale_products(self, brands: list[str], *, max_age_hours: int = 72) -> int:
                self.cleanup_calls.append((list(brands), max_age_hours))
                return 5

        class FakeScraperSuccess:
            engine_name = "defacto"

            def __enter__(self) -> FakeScraperSuccess:
                return self

            def __exit__(self, *_args: Any) -> None:
                return None

            def run(self) -> ScrapeResult:
                return ScrapeResult(
                    brand="defacto",
                    engine="defacto",
                    status=ScrapeStatus.SUCCESS,
                    products=[strict_ok, catalog_only],
                    fetched_count=2,
                )

        class FakeScraperBlocked:
            engine_name = "mavi"

            def __enter__(self) -> FakeScraperBlocked:
                return self

            def __exit__(self, *_args: Any) -> None:
                return None

            def run(self) -> ScrapeResult:
                return ScrapeResult(
                    brand="mavi",
                    engine="mavi",
                    status=ScrapeStatus.BLOCKED,
                    products=[],
                    fetched_count=0,
                    error_message="HTTP 403 WAF",
                )

        scored_ids: list[str] = []
        notified_ids: list[str] = []

        class FakeScorer:
            is_configured = True
            model = "gemini-2.5-flash"

            def __init__(self, *_a: Any, **_kw: Any) -> None:
                pass

            @staticmethod
            def should_score_product(analysis: PriceAnalysis, *, existing_product: Any = None) -> tuple[bool, str]:
                return True, "eligible"

            def score_product(self, product: ProductItem, *, style_profile: str) -> Any:
                scored_ids.append(product.external_id)
                return SimpleNamespace(ai_score=9, ai_comment="Harika.", status="scored", error_message=None)

        class FakeNotifier:
            is_configured = True

            def __init__(self, *_a: Any, **_kw: Any) -> None:
                pass

            @staticmethod
            def should_notify_product(product: ProductItem, **kwargs: Any) -> tuple[bool, str]:
                return PushNotifier.should_notify_product(product, **kwargs)

            def notify_product(self, product: ProductItem, **_kwargs: Any) -> Any:
                notified_ids.append(product.external_id)
                return SimpleNamespace(sent=1)

        def _fake_build(brand: str, **_kwargs: Any) -> Any:
            return FakeScraperSuccess() if brand == "defacto" else FakeScraperBlocked()

        fake_db = FakeDB()
        args = main_mod.parse_args(["--brand", "defacto,mavi", "--run-mode", "cloud"])

        with (
            patch.object(main_mod, "get_settings", return_value=_make_app_settings()),
            patch.object(main_mod, "DatabaseClient", return_value=fake_db),
            patch.object(main_mod, "_build_scraper", side_effect=_fake_build),
            patch.object(main_mod, "AIStyleScorer", FakeScorer),
            patch.object(main_mod, "PushNotifier", FakeNotifier),
        ):
            rc = main_mod.run_pipeline(args)

        self.assertEqual(rc, 0)
        # Both strict_ok AND catalog_only_nosize are saved to Supabase products
        self.assertEqual(fake_db.upserted_ids, ["strict-ok", "catalog-only-nosize"])
        # Only strict_ok triggered Gemini AI scoring & Web Push notification
        self.assertEqual(scored_ids, ["strict-ok"])
        self.assertEqual(notified_ids, ["strict-ok"])
        # 72h stale cleanup ran ONLY for succeeded brand 'defacto', NOT blocked 'mavi'
        self.assertEqual(fake_db.cleanup_calls, [(["defacto"], 72)])


class TestDatabaseStaleCleanupAndMatrixGroups(unittest.TestCase):
    """Verify DatabaseClient.cleanup_stale_products query scoping and disjoint matrix partition."""

    def test_cleanup_stale_products_scopes_delete_to_brands_and_cutoff(self) -> None:
        mock_client = MagicMock()
        chain = mock_client.table.return_value.delete.return_value.in_.return_value.lt.return_value
        chain.execute.return_value = SimpleNamespace(data=[{"id": "1"}, {"id": "2"}])

        db = DatabaseClient.__new__(DatabaseClient)
        db._client = mock_client

        deleted = db.cleanup_stale_products(["DeFacto", "mavi", "defacto"], max_age_hours=72)
        self.assertEqual(deleted, 2)
        mock_client.table.assert_called_once_with("products")
        mock_client.table.return_value.delete.return_value.in_.assert_called_once_with(
            "brand", ["defacto", "mavi"]
        )
        # Empty brand list should short-circuit without touching DB
        mock_client.reset_mock()
        self.assertEqual(db.cleanup_stale_products([]), 0)
        mock_client.table.assert_not_called()

    def test_github_actions_matrix_targets_partition_all_cloud_brands_without_overlap(self) -> None:
        matrix_targets = [
            "inditex",
            "eren",
            "mango,trendyol",
            "independent_clothing",
            "independent_shoes",
        ]
        seen: list[str] = []
        for target in matrix_targets:
            resolved = main_mod._resolve_brands(target)
            cloud_only, _ = main_mod._filter_brands_by_target_mode(resolved, "cloud")
            seen.extend(cloud_only)

        # No duplicate brands across parallel matrix workers
        self.assertEqual(len(seen), len(set(seen)))

        expected_cloud, _ = main_mod._filter_brands_by_target_mode(
            list(main_mod.SUPPORTED_BRANDS), "cloud"
        )
        self.assertEqual(set(seen), set(expected_cloud))
        self.assertEqual(len(seen), 20)


class TestPaginationEarlyExitAndDetailFallback(unittest.TestCase):
    """Verify 2-consecutive-empty-page early exit and listing fallback after max_detail_fetches."""

    def test_akinon_early_exit_after_two_consecutive_empty_pages_and_keeps_candidates_past_detail_cap(self) -> None:
        scraper = ErenScraper(
            "superstep",
            section="men",
            max_categories=1,
            max_pages_per_category=10,
            max_detail_fetches=1,
            fetch_variant_details=True,
            min_jitter_seconds=0.0,
            max_jitter_seconds=0.0,
        )
        self.assertEqual(EREN_BRAND_CONFIGS["superstep"].default_sorter, "number")

        page_calls: list[str] = []

        def fake_get_json(url: str, **_kwargs: Any) -> dict[str, Any]:
            page_calls.append(url)
            if "/indirim/" in url:
                if "page=1" in url:
                    return {
                        "pagination": {"num_pages": 10},
                        "products": [
                            {
                                "pk": 1,
                                "name": "Nike Erkek Sneaker A",
                                "price": "2000",
                                "retail_price": "4000",
                                "absolute_url": "/nike-a/",
                                "in_stock": True,
                                "attributes": {"integration_cinsiyet": "Erkek"},
                            },
                            {
                                "pk": 2,
                                "name": "Nike Erkek Sneaker B",
                                "price": "2500",
                                "retail_price": "4000",
                                "absolute_url": "/nike-b/",
                                "in_stock": True,
                                "attributes": {"integration_cinsiyet": "Erkek"},
                            },
                        ],
                    }
                # Page 2 and Page 3 have no discounted items -> should stop after page 3 (2 consecutive empty)
                return {
                    "pagination": {"num_pages": 10},
                    "products": [
                        {
                            "pk": 99,
                            "name": "Full Price Item",
                            "price": "4000",
                            "retail_price": "4000",
                            "absolute_url": "/full-price/",
                            "in_stock": True,
                            "attributes": {"integration_cinsiyet": "Erkek"},
                        }
                    ],
                }
            # Detail fetch for nike-a
            return {
                "product": {"attributes": {}},
                "variants": [
                    {
                        "attribute_key": "integration_beden",
                        "attribute_name": "Beden",
                        "options": [{"label": "42", "in_stock": True, "product": {"stock": 5, "attributes": {}}}],
                    }
                ],
            }

        with patch.object(scraper.http, "get_json", side_effect=fake_get_json):
            items = scraper.fetch_products()

        listing_calls = [u for u in page_calls if "/indirim/" in u]
        # Stopped cleanly at page 3 (pages 2 & 3 yielded 0 new discounted items)
        self.assertEqual(len(listing_calls), 3)
        # BOTH discounted items returned even though max_detail_fetches=1 (nike-b keeps listing fallback)
        self.assertEqual(len(items), 2)
        by_id = {item.external_id: item for item in items}
        self.assertEqual(by_id["1"].available_sizes, ["42"])
        self.assertEqual(by_id["2"].available_sizes, [])
        scraper.close()

    def test_defacto_keeps_product_when_listing_sizes_are_empty(self) -> None:
        scraper = DeFactoScraper(section="men")
        doc = {
            "LongCode": "DF-EMPTY-SIZES",
            "Name": "Regular Fit Erkek Mont",
            "SeoName": "regular-fit-erkek-mont",
            "ProductVariantIndex": 777111,
            "Price": 2000.0,
            "DiscountedPrice": 1200.0,
            "PersonaDescriptionName": "Erkek",
            "Sizes": [],
        }
        item = scraper.parse_document(doc, category_path="/erkek-indirimli-urunler")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.available_sizes, [])
        self.assertEqual(item.discount_percent, 40)
        scraper.close()

    def test_hm_and_instreet_keep_item_when_listing_omits_sizes_but_reject_explicit_oos(self) -> None:
        hm = HMScraper(section="men")
        raw_no_sizes = {
            "id": "hm-1",
            "productName": "Relaxed Fit Erkek Gömlek",
            "url": "/tr_tr/productpage.hm1.html",
            "mainCatCode": "men_shirts",
            "availability": {"comingSoon": False, "stockState": "Available"},
            "prices": [
                {"price": 1000.0, "priceType": "whitePrice"},
                {"price": 600.0, "priceType": "redPrice"},
            ],
        }
        item_hm = hm.parse_product(raw_no_sizes, category_id="men_sale")
        self.assertIsNotNone(item_hm)
        assert item_hm is not None
        self.assertEqual(item_hm.available_sizes, [])

        raw_oos = {**raw_no_sizes, "id": "hm-2", "sizes": [{"label": "M", "stock": 0}]}
        self.assertIsNone(hm.parse_product(raw_oos, category_id="men_sale"))
        hm.close()

        instreet = InStreetScraper(section="men")
        raw_is_no_opts = {
            "brandName": "Puma",
            "sku": "IS-NO-OPT",
            "price": 1500,
            "oldPrice": 2500,
            "name": "Erkek Sneaker",
            "relativeUrl": "/urun/puma-erkek-sneaker",
            "isInStock": True,
        }
        item_is = instreet.parse_product(raw_is_no_opts)
        self.assertIsNotNone(item_is)
        assert item_is is not None
        self.assertEqual(item_is.available_sizes, [])

        raw_is_oos = {**raw_is_no_opts, "sku": "IS-OOS", "options": [{"option_value": "42", "is_in_stock": False, "qty": 0}]}
        self.assertIsNone(instreet.parse_product(raw_is_oos))
        instreet.close()


if __name__ == "__main__":
    unittest.main()
