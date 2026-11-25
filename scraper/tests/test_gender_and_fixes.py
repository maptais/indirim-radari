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
from scraper.core.http import infer_product_gender, interleave_by_gender, is_kids_product
from scraper.core.notifier import PushNotifier
from scraper.core.price_analyzer import PriceAnalysis
from scraper.engines.base import ProductCategory, ProductGender, ProductItem, ScrapeResult, ScrapeStatus
from scraper.engines.eren import EREN_BRAND_CONFIGS
from scraper.engines.independents.adidasoutlet import ADIDAS_OUTLET_QUERIES
from scraper.engines.independents.barcin import BARCIN_CONFIG
from scraper.engines.independents.defacto import DEFACTO_CATEGORY_PATHS
from scraper.engines.independents.hm import HM_CATEGORY_SPECS
from scraper.engines.independents.instreet import InStreetScraper
from scraper.engines.independents.jackjones import JACKJONES_CATEGORY_PATHS
from scraper.engines.independents.mavi import MAVI_CATEGORY_PATHS
from scraper.engines.independents.pumaoutlet import PUMA_OUTLET_PATHS
from scraper.engines.independents.sneaksup import SNEAKSUP_CATEGORY_PATHS
from scraper.engines.independents.sportive import SPORTIVE_CONFIG
from scraper.engines.inditex import INDITEX_BRAND_CONFIGS, InditexScraper, _CategoryNode
from scraper.engines.mango import MANGO_BRAND_CONFIGS, MangoScraper
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
        self.assertEqual(infer_product_gender("Nike Wmns Dunk Low Sneaker"), ProductGender.WOMEN)
        self.assertEqual(infer_product_gender("Puma Cali Dream Wns"), ProductGender.WOMEN)
        # Percent-encoded Turkish path segment (`Kad%C4%B1n`) must resolve cleanly to WOMEN
        self.assertEqual(
            infer_product_gender("", "/outlet/c/4?q=:sale:gender:Kad%C4%B1n"),
            ProductGender.WOMEN,
        )
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
        self.assertEqual(set(skipped_women), {"trendyolman"})
        self.assertIn("trendyolmilla", selected_women)
        self.assertIn("mangoman", selected_women)

        selected_unisex, skipped_unisex = main_mod._filter_brands_by_target_genders(all_brands, ["unisex"])
        self.assertEqual(set(skipped_unisex), {"trendyolman", "trendyolmilla"})
        self.assertIn("zara", selected_unisex)
        self.assertIn("mangoman", selected_unisex)

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

    def test_pipeline_saves_all_genders_to_db_but_gates_ai_and_push_by_target_genders(self) -> None:
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
                self.upserted_genders: dict[str, str] = {}

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
                payload = product.to_db_payload()
                self.upserted_ids.append(product.external_id)
                self.upserted_genders[product.external_id] = str(payload["gender"])
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
        built_calls: list[tuple[str, str]] = []

        def fake_build_scraper(brand: str, **kwargs: Any) -> FakeScraper:
            built_calls.append((brand, str(kwargs.get("section"))))
            return FakeScraper()

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
        args = main_mod.parse_args(["--brand", "pullandbear,trendyolmilla", "--run-mode", "cloud"])
        self.assertEqual(args.section, "all")

        with (
            patch.object(main_mod, "get_settings", return_value=_make_app_settings()),
            patch.object(main_mod, "DatabaseClient", return_value=fake_db),
            patch.object(main_mod, "_build_scraper", side_effect=fake_build_scraper),
            patch.object(main_mod, "AIStyleScorer", FakeScorer),
            patch.object(main_mod, "PushNotifier", FakeNotifier),
        ):
            rc = main_mod.run_pipeline(args)

        self.assertEqual(rc, 0)
        # Both brands (including women-exclusive trendyolmilla) ran with section="all"
        # even though user_settings.target_genders is ["men", "unisex"]!
        self.assertEqual(built_calls, [("pullandbear", "all"), ("trendyolmilla", "all")])
        # Both Men and Women items are persisted in Supabase with canonical gender values
        self.assertEqual(fake_db.upserted_genders["men-1"], "men")
        self.assertEqual(fake_db.upserted_genders["women-1"], "women")
        self.assertIn("men-1", fake_db.upserted_ids)
        self.assertIn("women-1", fake_db.upserted_ids)
        # Only user_settings.target_genders ("men", "unisex") triggered AI scoring and Web Push
        self.assertEqual(set(scored_ids), {"men-1"})
        self.assertEqual(set(notified_ids), {"men-1"})

    def test_dual_gender_balancing_across_inditex_mango_instreet_and_brand_configs(self) -> None:
        # 1. interleave_by_gender keeps per-gender order while alternating men & women
        sample = [
            ("m1", ProductGender.MEN),
            ("m2", ProductGender.MEN),
            ("m3", ProductGender.MEN),
            ("w1", ProductGender.WOMEN),
            ("w2", ProductGender.WOMEN),
        ]
        interleaved = interleave_by_gender(sample, lambda pair: pair[1])
        self.assertEqual([x[0] for x in interleaved[:4]], ["m1", "w1", "m2", "w2"])

        # 2. Inditex _collect_categories interleaves women & men promo nodes within max_categories
        zara = InditexScraper("zara", section="all", max_categories=4)
        raw_tree = [
            {
                "id": 1,
                "name": "ERKEK",
                "nameEn": "MAN",
                "subcategories": [
                    {"id": 101, "name": "Özel Fiyatlar A", "key": "MAN-SPECIAL_PRICES-A", "subcategories": []},
                    {"id": 102, "name": "Özel Fiyatlar B", "key": "MAN-SPECIAL_PRICES-B", "subcategories": []},
                    {"id": 103, "name": "Özel Fiyatlar C", "key": "MAN-SPECIAL_PRICES-C", "subcategories": []},
                    {"id": 104, "name": "Özel Fiyatlar D", "key": "MAN-SPECIAL_PRICES-D", "subcategories": []},
                ],
            },
            {
                "id": 2,
                "name": "KADIN",
                "nameEn": "WOMAN",
                "subcategories": [
                    {"id": 201, "name": "Özel Fiyatlar Elbise", "key": "WOMAN-SPECIAL_PRICES-A", "subcategories": []},
                    {"id": 202, "name": "Özel Fiyatlar Ceket", "key": "WOMAN-SPECIAL_PRICES-B", "subcategories": []},
                ],
            },
        ]
        chosen = zara._collect_categories(raw_tree)
        self.assertEqual(len(chosen), 4)
        chosen_genders = [infer_product_gender(c.section_hint, c.path) for c in chosen]
        self.assertEqual(chosen_genders.count(ProductGender.MEN), 2)
        self.assertEqual(chosen_genders.count(ProductGender.WOMEN), 2)
        zara.close()

        # 3. Mango (`mangoman` on shop.mango.com) discovers & interleaves both erkek and kadin
        self.assertIn("/tr/tr/h/erkek", MANGO_BRAND_CONFIGS["mangoman"].home_paths["all"])
        self.assertIn("/tr/tr/h/kadin", MANGO_BRAND_CONFIGS["mangoman"].home_paths["all"])
        mango = MangoScraper("mangoman", section="all", max_categories=4, fetch_variant_details=False)
        fake_erkek_html = '"/tr/tr/c/erkek/indirim/111111" "/tr/tr/c/erkek/ceket/222222" "/tr/tr/c/erkek/kazak/333333"'
        fake_kadin_html = '"/tr/tr/c/kadin/indirim/444444" "/tr/tr/c/kadin/elbise/555555" "/tr/tr/c/kadin/ceket/666666"'
        with patch.object(
            mango.http,
            "get_text",
            side_effect=lambda url, **_kw: fake_kadin_html if "/kadin" in url else fake_erkek_html,
        ):
            discovered = mango._discover_category_paths()
        self.assertEqual(len(discovered), 4)
        self.assertTrue(any("/erkek/" in p for p in discovered))
        self.assertTrue(any("/kadin/" in p for p in discovered))
        # Women product parsed from Mango gets ProductGender.WOMEN (never forced to MEN)
        women_entry = {
            "defaultColorId": "01",
            "product": {
                "name": "Keten Midi Elbise",
                "reference": "99999999",
                "url": "/tr/tr/p/kadin/elbise/keten-midi-elbise/99999999",
                "colors": [{"id": "01", "sizes": [{"label": "S"}]}],
            },
            "prices": {"01": {"default": {"price": 1200.0, "previousPrice": 2400.0}}},
        }
        women_item = mango.parse_plp_entry(women_entry, category_path="/tr/tr/c/kadin/elbise/555555")
        self.assertIsNotNone(women_item)
        assert women_item is not None
        self.assertEqual(women_item.gender, ProductGender.WOMEN)
        mango.close()

        # 4. In Street (`section="all"`) explicitly queries both `erkek` and `kadin`
        instreet = InStreetScraper(section="all", max_pages=2, min_jitter_seconds=0.0, max_jitter_seconds=0.0)
        queried_genders: list[str] = []

        def fake_instreet_json(_url: str, *, params: dict[str, Any] | None = None, **_kw: Any) -> dict[str, Any]:
            g = str((params or {}).get("cinsiyet") or "")
            queried_genders.append(g)
            return {
                "pagination": {"totalPages": 1},
                "products": [
                    {
                        "brandName": "Nike",
                        "sku": f"IS-{g}-1",
                        "name": "Air Sneaker",
                        "price": 1500,
                        "oldPrice": 3000,
                        "relativeUrl": f"/urun/nike-air-{g}",
                        "isInStock": True,
                        "options": [{"option_value": "38" if g == "kadin" else "42", "is_in_stock": True, "qty": 4}],
                    }
                ],
            }

        with patch.object(instreet.http, "get_json", side_effect=fake_instreet_json):
            is_items = instreet.fetch_products()
        self.assertEqual(queried_genders, ["erkek", "kadin"])
        self.assertEqual({item.gender for item in is_items}, {ProductGender.MEN, ProductGender.WOMEN})
        instreet.close()

        # 5. Every dual-gender brand config explicitly includes both men and women paths under "all"
        for slug, cfg in EREN_BRAND_CONFIGS.items():
            joined = " ".join(cfg.category_paths["all"])
            self.assertIn("erkek", joined, f"{slug} missing erkek in all")
            self.assertIn("kadin", joined, f"{slug} missing kadin in all")

        for name, paths in (
            ("barcin", BARCIN_CONFIG.category_paths["all"]),
            ("sportive", SPORTIVE_CONFIG.category_paths["all"]),
            ("defacto", DEFACTO_CATEGORY_PATHS["all"]),
            ("mavi", MAVI_CATEGORY_PATHS["all"]),
            ("jackjones", JACKJONES_CATEGORY_PATHS["all"]),
            ("sneaksup", SNEAKSUP_CATEGORY_PATHS["all"]),
            ("adidasoutlet", ADIDAS_OUTLET_QUERIES["all"]),
            ("pumaoutlet", PUMA_OUTLET_PATHS["all"]),
        ):
            joined = " ".join(paths)
            self.assertIn("erkek", joined.lower(), f"{name} missing erkek in all")
            self.assertTrue(
                "kadin" in joined.lower() or "kad%c4%b1n" in joined.lower(),
                f"{name} missing kadin in all",
            )

        hm_joined = " ".join(spec[0] for spec in HM_CATEGORY_SPECS["all"])
        self.assertIn("/men/", hm_joined)
        self.assertIn("/ladies/", hm_joined)


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
