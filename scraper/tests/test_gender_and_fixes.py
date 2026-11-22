"""Unit tests for dynamic gender preferences, brand skipping, and cloud brand repairs."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from pydantic import SecretStr

from scraper.core.config import AppSettings, CategoryThreshold, UserSettings
from scraper.core.filters import ProductFilter
from scraper.core.http import infer_product_gender, is_kids_product
from scraper.core.notifier import PushNotifier
from scraper.core.price_analyzer import PriceAnalysis
from scraper.engines.base import ProductCategory, ProductGender, ProductItem, ScrapeResult, ScrapeStatus
from scraper.engines.eren import EREN_BRAND_CONFIGS
from scraper.engines.independents.jackjones import JACKJONES_CATEGORY_PATHS
from scraper.engines.independents.mavi import MAVI_CATEGORY_PATHS
from scraper.engines.independents.sneaksup import SNEAKSUP_CATEGORY_PATHS
from scraper.engines.inditex import INDITEX_BRAND_CONFIGS, InditexScraper, _CategoryNode
from scraper.engines.mango import MangoScraper
import scraper.main as main_mod


def _make_app_settings() -> AppSettings:
    return AppSettings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key=SecretStr("service-role-test-key"),
        gemini_api_key=SecretStr("AIzaSyTestFakeKey1234567890"),
        gemini_model="gemini-2.5-flash",
    )


class TestGenderInferenceAndFiltering(unittest.TestCase):
    """Verify gender inference, kids filtering, UserSettings mapping, and brand skipping."""

    def test_infer_product_gender_and_boundaries(self) -> None:
        self.assertEqual(infer_product_gender("Erkek Relaxed Fit Ceket"), ProductGender.MEN)
        self.assertEqual(infer_product_gender("Kadın Saten Elbise"), ProductGender.WOMEN)
        # Ensure "woman" never falsely triggers "man"
        self.assertEqual(infer_product_gender("WOMAN > Special Prices"), ProductGender.WOMEN)
        self.assertEqual(infer_product_gender("Unisex Beyaz Sneaker", "/erkek-ayakkabi/"), ProductGender.UNISEX)
        self.assertEqual(infer_product_gender("", "men_shirts_longsleeved"), ProductGender.MEN)
        self.assertEqual(infer_product_gender("", "ladies_sale"), ProductGender.WOMEN)
        self.assertEqual(infer_product_gender("Basic Pamuk Tişört"), ProductGender.UNISEX)

    def test_is_kids_product_detects_turkish_and_english_markers(self) -> None:
        self.assertTrue(is_kids_product("Nike Dunk Low Çocuk Sneaker"))
        self.assertTrue(is_kids_product("Bebek Pamuklu Tulum"))
        self.assertTrue(is_kids_product("Jordan 1 Mid (GS) Basketbol Ayakkabısı"))
        self.assertTrue(is_kids_product("KIDS > Special Prices"))
        self.assertFalse(is_kids_product("Nike Air Force 1 Erkek Sneaker"))

    def test_user_settings_target_genders_and_section_mapping(self) -> None:
        men_unisex = UserSettings(target_genders=["men", "unisex"])
        self.assertEqual(men_unisex.to_scraper_section(), "men")

        women_only = UserSettings(target_genders=["women"])
        self.assertEqual(women_only.to_scraper_section(), "women")

        both = UserSettings(target_genders=["men", "women", "unisex"])
        self.assertEqual(both.to_scraper_section(), "all")

        unisex_only = UserSettings(target_genders=["unisex"])
        self.assertEqual(unisex_only.to_scraper_section(), "all")

        # Invalid/empty values safely fall back to ["men", "unisex"]
        fallback = UserSettings(target_genders=["invalid"])
        self.assertEqual(fallback.target_genders, ["men", "unisex"])

    def test_filter_brands_by_target_genders_skips_exclusive_brands(self) -> None:
        all_brands = list(main_mod.SUPPORTED_BRANDS)

        selected_men, skipped_men = main_mod._filter_brands_by_target_genders(all_brands, ["men", "unisex"])
        self.assertEqual(skipped_men, ["trendyolmilla"])
        self.assertIn("trendyolman", selected_men)
        self.assertIn("mangoman", selected_men)

        selected_women, skipped_women = main_mod._filter_brands_by_target_genders(all_brands, ["women", "unisex"])
        self.assertEqual(set(skipped_women), {"mangoman", "trendyolman"})
        self.assertIn("trendyolmilla", selected_women)

        selected_unisex, skipped_unisex = main_mod._filter_brands_by_target_genders(all_brands, ["unisex"])
        self.assertEqual(set(skipped_unisex), {"mangoman", "trendyolman", "trendyolmilla"})
        self.assertIn("zara", selected_unisex)

    def test_product_filter_and_notifier_gate_gender(self) -> None:
        settings = UserSettings(
            target_genders=["men", "unisex"],
            top_sizes=["M", "L"],
            category_thresholds={
                "tops": CategoryThreshold(min_discount_percent=30, max_price_try=Decimal("2000")),
            },
        )
        pf = ProductFilter(settings)

        men_item = ProductItem(
            brand="zara",
            external_id="m1",
            title="Relaxed Fit Pamuklu Gömlek",
            category=ProductCategory.TOPS,
            gender=ProductGender.MEN,
            url="https://www.zara.com/tr/tr/m1.html",
            current_price=Decimal("800.00"),
            original_price=Decimal("1600.00"),
            available_sizes=["M"],
        )
        women_item = men_item.model_copy(update={"external_id": "w1", "gender": ProductGender.WOMEN})
        unisex_item = men_item.model_copy(update={"external_id": "u1", "gender": ProductGender.UNISEX})

        self.assertTrue(pf.evaluate(men_item).passed)
        self.assertTrue(pf.evaluate(unisex_item).passed)
        women_res = pf.evaluate(women_item)
        self.assertFalse(women_res.passed)
        self.assertIn("gender_mismatch:women", women_res.reasons)

        # Defense-in-depth inside PushNotifier
        analysis = PriceAnalysis(is_real_discount=True, lowest_price_days=30)
        ok_w, reason_w = PushNotifier.should_notify_product(
            women_item, analysis=analysis, user_settings=settings, ai_score=9
        )
        self.assertFalse(ok_w)
        self.assertIn("gender_mismatch", reason_w)

    def test_pipeline_excludes_wrong_gender_from_ai_db_and_push(self) -> None:
        men_item = ProductItem(
            brand="pullandbear",
            external_id="men-1",
            title="Erkek Keten Ceket",
            category=ProductCategory.OUTERWEAR,
            gender=ProductGender.MEN,
            url="https://www.pullandbear.com/tr/men-1",
            current_price=Decimal("1000.00"),
            original_price=Decimal("2000.00"),
            available_sizes=["M"],
        )
        women_item = ProductItem(
            brand="pullandbear",
            external_id="women-1",
            title="Kadın Keten Ceket",
            category=ProductCategory.OUTERWEAR,
            gender=ProductGender.WOMEN,
            url="https://www.pullandbear.com/tr/women-1",
            current_price=Decimal("1000.00"),
            original_price=Decimal("2000.00"),
            available_sizes=["M"],
        )

        class FakeDB:
            def __init__(self) -> None:
                self.upserted_ids: list[str] = []

            def get_user_settings(self) -> UserSettings:
                return UserSettings(
                    target_genders=["men", "unisex"],
                    top_sizes=["M"],
                    category_thresholds={
                        "outerwear": CategoryThreshold(min_discount_percent=30, max_price_try=Decimal("3000"))
                    },
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
                return {"id": f"uuid-{product.external_id}", "ai_score": 9}

            def record_price_history(self, **_kwargs: Any) -> dict[str, Any]:
                return {"id": 1}

            def get_active_push_subscriptions(self) -> list[dict[str, Any]]:
                return []

        class FakeScraper:
            engine_name = "inditex"

            def __enter__(self) -> FakeScraper:
                return self

            def __exit__(self, *_args: Any) -> None:
                return None

            def run(self) -> ScrapeResult:
                return ScrapeResult(
                    brand="pullandbear",
                    engine="inditex",
                    status=ScrapeStatus.SUCCESS,
                    products=[men_item, women_item],
                    fetched_count=2,
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

        fake_db = FakeDB()
        args = main_mod.parse_args(["--brand", "pullandbear", "--run-mode", "cloud"])
        self.assertEqual(args.section, "auto")

        with (
            patch.object(main_mod, "get_settings", return_value=_make_app_settings()),
            patch.object(main_mod, "DatabaseClient", return_value=fake_db),
            patch.object(main_mod, "_build_scraper", return_value=FakeScraper()),
            patch.object(main_mod, "AIStyleScorer", FakeScorer),
            patch.object(main_mod, "PushNotifier", FakeNotifier),
        ):
            rc = main_mod.run_pipeline(args)

        self.assertEqual(rc, 0)
        self.assertEqual(fake_db.upserted_ids, ["men-1"])
        self.assertEqual(scored_ids, ["men-1"])
        self.assertEqual(notified_ids, ["men-1"])


class TestCloudBrandFixes(unittest.TestCase):
    """Verify structural fixes made to previously empty cloud brand scrapers."""

    def test_zara_pdp_ajax_extracts_sizes_and_detailed_composition(self) -> None:
        scraper = InditexScraper("zara", section="men")
        comp = {
            "id": 552289445,
            "name": "YIKAMALI SUNİ SÜET CEKET",
            "price": 219000,
            "oldPrice": 429000,
            "sectionName": "MAN",
            "seo": {"keyword": "yikanmis-suni-suet-ceket", "seoProductId": "03046402"},
            "detail": {"colors": [{"id": "710"}]},
        }
        pdp_payload = {
            "product": {
                "detail": {
                    "colors": [
                        {
                            "id": "710",
                            "sizes": [
                                {"name": "S (US S)", "availability": "out_of_stock"},
                                {"name": "M (US M)", "availability": "low_on_stock"},
                                {"name": "L (US L)", "availability": "in_stock"},
                            ],
                        }
                    ],
                    "detailedComposition": {
                        "parts": [
                            {
                                "description": "DIŞ",
                                "areas": [
                                    {
                                        "components": [
                                            {"material": "pamuk", "percentage": "80%"},
                                            {"material": "polyester", "percentage": "20%"},
                                        ]
                                    }
                                ],
                            }
                        ]
                    },
                }
            }
        }
        cat = _CategoryNode(
            category_id=2436824,
            name="ÖZEL FİYATLAR",
            path="ERKEK (MAN) > ÖZEL FİYATLAR",
            key="I2024-HOMBRE-PVP_GENERAL",
            is_promo=True,
            section_hint="MAN",
        )
        item = scraper._parse_zara_component(comp, cat, pdp_payload=pdp_payload)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.gender, ProductGender.MEN)
        self.assertEqual(item.available_sizes, ["M (US M)", "L (US L)"])
        self.assertTrue(item.is_critical_stock)
        self.assertEqual(item.polyester_percent, 20)
        self.assertIn("pamuk", (item.fabric_info or "").lower())
        scraper.close()

    def test_mango_previous_prices_dict_schema_parsing(self) -> None:
        cur, orig = MangoScraper._extract_prices(
            {
                "30": {
                    "default": {
                        "price": 1799.99,
                        "previousPrices": {"originalShop": 3699.99},
                        "discountRate": 51,
                        "type": "PROMOTION",
                    }
                }
            },
            "30",
        )
        self.assertEqual(cur, Decimal("1799.99"))
        self.assertEqual(orig, Decimal("3699.99"))

    def test_repaired_brand_configs_and_query_params(self) -> None:
        self.assertEqual(INDITEX_BRAND_CONFIGS["bershka"].store_id, 44109521)
        self.assertIn("/indirim/", EREN_BRAND_CONFIGS["superstep"].category_paths["men"])
        self.assertTrue(any(":sale" in p for p in MAVI_CATEGORY_PATHS["men"]))
        self.assertTrue(any("sorting=highest_discount" in p for p in JACKJONES_CATEGORY_PATHS["men"]))
        self.assertIn("/sneaks-out", SNEAKSUP_CATEGORY_PATHS["men"])


if __name__ == "__main__":
    unittest.main()
