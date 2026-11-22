"""Unit tests for Phase 6 clothing scrapers (8 brands), resilience target modes, and blocked log reporting."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

from scraper.core.config import CategoryThreshold, UserSettings
from scraper.core.filters import ProductFilter
from scraper.core.notifier import BRAND_DISPLAY_NAMES
from scraper.engines.base import ProductCategory, ProductGender, ScrapeRunMode, ScrapeStatus, ScraperBlockedError
from scraper.engines.independents import (
    DeFactoScraper,
    HMScraper,
    JackJonesScraper,
    MaviScraper,
)
from scraper.engines.independents.mavi import decode_spa_transfer_state
from scraper.engines.mango import MANGO_BRAND_CONFIGS, MangoScraper
from scraper.engines.trendyol import TRENDYOL_BRAND_CONFIGS, TrendyolScraper
from scraper.main import (
    SUPPORTED_BRANDS,
    _build_scraper,
    _filter_brands_by_target_mode,
    _resolve_brands,
    get_brand_default_run_mode,
    parse_args,
    run_pipeline,
)


class TestPhase6ClothingScrapers(unittest.TestCase):
    """Verify parsers and filter integration for all 8 Phase 6 clothing brands."""

    def setUp(self) -> None:
        self.settings = UserSettings(
            top_sizes=["M", "L"],
            bottom_sizes=["32", "M", "L"],
            shoe_sizes=["42", "42.5", "43"],
            negative_keywords=["skinny", "baskılı", "logolu"],
            max_polyester_percent=35,
            excluded_fabrics=["akrilik"],
            category_thresholds={
                "tops": CategoryThreshold(min_discount_percent=30, max_price_try=Decimal("1800")),
                "bottoms": CategoryThreshold(min_discount_percent=30, max_price_try=Decimal("2200")),
                "outerwear": CategoryThreshold(min_discount_percent=30, max_price_try=Decimal("4500")),
                "shoes": CategoryThreshold(min_discount_percent=30, max_price_try=Decimal("3500")),
                "other": CategoryThreshold(min_discount_percent=30, max_price_try=Decimal("2000")),
            },
        )
        self.product_filter = ProductFilter(self.settings)

    def test_mango_man_and_outlet_rsc_parsers_and_blocked_handling(self) -> None:
        plp_chunk = json.dumps(
            {
                "dataTestId": "plp.product",
                "defaultColorId": "39",
                "product": {
                    "name": "Relaxed Fit Keten Ceket",
                    "reference": "37077887",
                    "url": "/tr/tr/p/erkek/ceket/relaxed-fit-keten-ceket/37077887",
                    "assetsDomain": "https://media.mango.com",
                    "families": [{"label": "Ceket", "labelEn": "Jackets"}],
                    "colors": [
                        {
                            "id": "39",
                            "looks": {
                                "00": {
                                    "images": {
                                        "500": {"img": "/is/image/punto/37077887-39-500"}
                                    }
                                }
                            },
                            "sizes": [{"label": "M"}, {"label": "L"}],
                        }
                    ],
                },
                "prices": {
                    "39": {
                        "default": {
                            "price": 2499.99,
                            "previousPrice": 4999.99,
                            "type": "REBAJA",
                            "currency": "TRY",
                        }
                    }
                },
            },
            ensure_ascii=False,
        )
        escaped_plp = json.dumps(plp_chunk)
        plp_html = f'<script>self.__next_f.push([1,{escaped_plp}])</script>'

        pdp_chunk = json.dumps(
            {
                "sizes": [
                    {"label": "S", "available": False, "lastUnits": False},
                    {"label": "M", "available": True, "lastUnits": True},
                    {"label": "L", "available": True, "lastUnits": False},
                ],
                "composition": "%85 pamuk, %15 keten",
            },
            ensure_ascii=False,
        )
        pdp_html = f'<script>self.__next_f.push([1,{json.dumps(pdp_chunk)}])</script>'

        entries = MangoScraper.extract_plp_products(plp_html)
        self.assertEqual(len(entries), 1)

        man_scraper = MangoScraper("mangoman", section="men", fetch_variant_details=False)
        item = man_scraper.parse_plp_entry(entries[0], category_path="/tr/tr/c/erkek/ceket/b5a3a3f6", detail_html=pdp_html)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.brand, "mangoman")
        self.assertEqual(item.category, ProductCategory.OUTERWEAR)
        self.assertEqual(item.gender, ProductGender.MEN)
        self.assertEqual(item.discount_percent, 50)
        self.assertEqual(item.available_sizes, ["M", "L"])
        self.assertTrue(item.is_critical_stock)
        self.assertEqual(item.fabric_info, "%85 pamuk, %15 keten")
        self.assertTrue(self.product_filter.evaluate(item).passed)
        self.assertEqual(man_scraper.default_run_mode, ScrapeRunMode.CLOUD)
        man_scraper.close()

        outlet_scraper = MangoScraper("mangooutlet", section="all", fetch_variant_details=False)
        self.assertEqual(outlet_scraper.default_run_mode, ScrapeRunMode.LOCAL)
        with patch.object(outlet_scraper.http, "get_text", side_effect=ScraperBlockedError("HTTP 403 WAF")):
            result = outlet_scraper.run()
        self.assertEqual(result.status, ScrapeStatus.BLOCKED)
        outlet_scraper.close()

    def test_trendyol_milla_and_man_parsers_with_pdp_variants_and_fabric(self) -> None:
        listing_props = {
            "data": {
                "products": [
                    {
                        "id": 728639328,
                        "name": "Antrasit Oversize Bisiklet Yaka Sweatshirt",
                        "brand": "TRENDYOL MAN",
                        "category": {"id": 601, "name": "Sweatshirt"},
                        "url": "/trendyolmilla-man/antrasit-oversize-sweatshirt-p-728639328",
                        "image": "https://cdn.dsmcdn.com/test-sweat.jpg",
                        "price": {
                            "old": 999.99,
                            "current": 599.99,
                            "originalPrice": 999.99,
                            "discountedPrice": 599.99,
                        },
                    }
                ]
            }
        }
        listing_html = f'<script>window["__single-search-result__PROPS"]={json.dumps(listing_props)};</script>'
        pdp_html = (
            '{"allVariants":['
            '{"value":"S","beautifiedValue":"S","inStock":false},'
            '{"value":"M","beautifiedValue":"M","inStock":true,"stock":2},'
            '{"value":"L","beautifiedValue":"L","inStock":true,"stock":9}'
            '],"attributes":['
            '{"key":{"name":"Materyal Bileşeni"},"value":{"name":"%80 Pamuk, %20 Polyester"}}'
            ']}'
        )

        raw_items = TrendyolScraper.extract_listing_products(listing_html)
        self.assertEqual(len(raw_items), 1)

        man_scraper = TrendyolScraper("trendyolman", section="men", fetch_variant_details=False)
        item = man_scraper.parse_listing_product(raw_items[0], category_path="/erkek-giyim-x-g2-c82", detail_html=pdp_html)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.brand, "trendyolman")
        self.assertEqual(item.category, ProductCategory.TOPS)
        self.assertEqual(item.discount_percent, 40)
        self.assertEqual(item.available_sizes, ["M", "L"])
        self.assertTrue(item.is_critical_stock)
        self.assertEqual(item.polyester_percent, 20)
        self.assertTrue(self.product_filter.evaluate(item).passed)
        man_scraper.close()

        milla_scraper = TrendyolScraper("trendyolmilla", section="women", fetch_variant_details=False)
        self.assertEqual(milla_scraper.brand, "trendyolmilla")
        self.assertEqual(milla_scraper.default_run_mode, ScrapeRunMode.CLOUD)
        milla_scraper.close()

    def test_hm_search_services_parser(self) -> None:
        scraper = HMScraper(section="men")
        raw = {
            "id": "1234567001",
            "productName": "Relaxed Fit Pamuklu Gömlek",
            "url": "/tr_tr/productpage.1234567001.html",
            "productImage": "https://image.hm.com/assets/hm/shirt.jpg",
            "mainCatCode": "men_shirts_longsleeved",
            "availability": {"comingSoon": False, "stockState": "FewPiecesLeft"},
            "prices": [
                {"price": 1299.0, "priceType": "whitePrice"},
                {"price": 779.0, "priceType": "redPrice"},
            ],
            "sizes": [
                {"id": "002", "label": "S", "stock": 0},
                {"id": "003", "label": "M", "stock": 2},
                {"id": "004", "label": "L", "stock": 5},
            ],
        }
        item = scraper.parse_product(raw, category_id="men_sale")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.brand, "hm")
        self.assertEqual(item.category, ProductCategory.TOPS)
        self.assertEqual(item.gender, ProductGender.MEN)
        self.assertEqual(item.current_price, Decimal("779.00"))
        self.assertEqual(item.original_price, Decimal("1299.00"))
        self.assertEqual(item.discount_percent, 40)
        self.assertEqual(item.available_sizes, ["M", "L"])
        self.assertTrue(item.is_critical_stock)
        self.assertTrue(self.product_filter.evaluate(item).passed)
        scraper.close()

    def test_defacto_data_documents_parser(self) -> None:
        doc = {
            "LongCode": "F1522AX26AUER140",
            "Name": "Standart Fit Yarım Fermuarlı Triko Kazak",
            "SeoName": "standart-fit-yarim-fermuarli-triko-kazak",
            "ProductVariantIndex": 3462136,
            "Price": 1299.99,
            "DiscountedPrice": 1299.99,
            "CampaignDiscountedPrice": 753.99,
            "CategoryName": "Kazak",
            "PictureName": "F1522AX_26AU_ER140_01_03.jpg",
            "PersonaDescriptionName": "Erkek",
            "Sizes": [
                {"SizeName": "S", "StockQuantity": 0},
                {"SizeName": "M", "StockQuantity": 2},
                {"SizeName": "L", "StockQuantity": 7},
            ],
        }
        html = f"<div data-documents='{json.dumps(doc, ensure_ascii=False)}'></div>"
        docs = DeFactoScraper.extract_documents_from_html(html)
        self.assertEqual(len(docs), 1)

        scraper = DeFactoScraper(section="men")
        item = scraper.parse_document(docs[0], category_path="/erkek-indirimli-urunler")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.brand, "defacto")
        self.assertEqual(item.category, ProductCategory.TOPS)
        self.assertEqual(item.current_price, Decimal("1299.99"))
        self.assertEqual(item.cart_price, Decimal("753.99"))
        self.assertEqual(item.effective_price, Decimal("753.99"))
        self.assertEqual(item.discount_percent, 42)
        self.assertEqual(item.promo_badge, "Sepette %42 İndirim")
        self.assertEqual(item.available_sizes, ["M", "L"])
        self.assertTrue(item.is_critical_stock)
        self.assertTrue(self.product_filter.evaluate(item).passed)
        scraper.close()

    def test_mavi_transfer_state_decoder_and_parser(self) -> None:
        # Build a minimal valid v2 __spaTransferState__ payload
        transfer_state = {
            "version": 2,
            "schemas": [
                ["products"],
                ["code", "productTitle", "name", "url", "gender", "isJean", "price", "storeSalePrice", "discountRate", "images"],
                ["value"],
                ["url"],
            ],
            "values": [
                [-2, "88351A3873", "MARCUS", "Vintage Indigo Jean Pantolon", "/marcus-jean/p/88351A3873", "Erkek", True, [-3, 999.99], [-3, 1599.99], 38, [0, [-4, "//sky-static.mavi.com/test.jpg"]]],
            ],
            "root": [-1, [0, "~0"]],
        }
        decoded = decode_spa_transfer_state(transfer_state)
        self.assertEqual(len(decoded["products"]), 1)

        listing_html = f'<script id="ng-state">{json.dumps({"__spaTransferState__": transfer_state})}</script>'
        pdp_state = {
            "code": "88351A3873",
            "fabricDefinition": "<ul><li>%86 Pamuk</li><li>%12 Polyester</li><li>%2 Elastan</li></ul>",
            "allSizeVariants": [
                {"size": "30", "length": "32", "stock": {"stockLevel": 0, "stockLevelStatus": "outOfStock"}},
                {"size": "32", "length": "32", "stock": {"stockLevel": 2, "stockLevelStatus": "inStock"}},
            ],
        }
        pdp_html = f'<script id="ng-state">{json.dumps(pdp_state)}</script>'

        raw_products = MaviScraper.extract_listing_products(listing_html)
        self.assertEqual(len(raw_products), 1)

        scraper = MaviScraper(section="men", fetch_variant_details=False)
        item = scraper.parse_listing_product(raw_products[0], category_path="/outlet/c/4?q=:relevance:gender:Erkek", detail_html=pdp_html)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.brand, "mavi")
        self.assertEqual(item.category, ProductCategory.BOTTOMS)
        self.assertEqual(item.current_price, Decimal("999.99"))
        self.assertEqual(item.original_price, Decimal("1599.99"))
        self.assertEqual(item.available_sizes, ["32/32"])
        self.assertEqual(item.polyester_percent, 12)
        self.assertTrue(item.is_critical_stock)
        f_res = self.product_filter.evaluate(item)
        self.assertTrue(f_res.passed, f_res.reasons)
        self.assertEqual(f_res.matched_sizes, ["32/32"])
        scraper.close()

    def test_jackjones_initial_state_listing_and_pdp_parser(self) -> None:
        listing_state = {
            "$getProducts-tr-tr-abc": {
                "results": [
                    {
                        "id": "12303930_2052",
                        "name": "Erkek Kapüşonlu Puffer Ceket - Galaxy",
                        "url": "/tr-tr/product/12303930_2052/ipli-kapuson-sisme-ceket",
                        "brand": "JACK&JONES",
                        "inStock": True,
                        "frontImage": "https://images.jackjones.com.tr/puffer.jpg",
                        "price": {
                            "percentage": 35,
                            "listPrice": {"amount": 3990, "currency": "TRY"},
                            "salesPrice": {"amount": 2590, "currency": "TRY"},
                        },
                    }
                ]
            }
        }
        listing_html = f"<script>window.__INITIAL_STATE__={json.dumps(listing_state)};</script>"

        pdp_state = {
            "$getProduct-12303930": {
                "materialComposition": "%75 Pamuk, %25 Polyester",
                "variants": [
                    {"size": {"name": "S"}, "inStock": False, "inStockEcom": False},
                    {"size": {"name": "M"}, "inStock": True, "inStockEcom": True},
                    {"size": {"name": "L"}, "inStock": True, "inStockEcom": True},
                ],
            }
        }
        pdp_html = f"<script>window.__INITIAL_STATE__={json.dumps(pdp_state)};</script>"

        raw_products = JackJonesScraper.extract_listing_products(listing_html)
        self.assertEqual(len(raw_products), 1)

        scraper = JackJonesScraper(section="men", fetch_variant_details=False)
        item = scraper.parse_listing_product(raw_products[0], category_path="/tr-tr/erkek-giyim", detail_html=pdp_html)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.brand, "jackjones")
        self.assertEqual(item.category, ProductCategory.OUTERWEAR)
        self.assertEqual(item.discount_percent, 35)
        self.assertEqual(item.available_sizes, ["M", "L"])
        self.assertEqual(item.polyester_percent, 25)
        self.assertTrue(self.product_filter.evaluate(item).passed)
        scraper.close()


class TestPhase6ResilienceAndGroups(unittest.TestCase):
    """Verify 22-brand registration, group shortcuts, target-mode filtering, and blocked logging."""

    def test_all_22_brands_and_phase6_groups(self) -> None:
        all_brands = _resolve_brands("all")
        self.assertEqual(len(all_brands), 22)
        self.assertEqual(len(set(all_brands)), 22)
        self.assertEqual(tuple(all_brands), SUPPORTED_BRANDS)

        self.assertEqual(_resolve_brands("mango"), ["mangoman", "mangooutlet"])
        self.assertEqual(_resolve_brands("trendyol"), ["trendyolmilla", "trendyolman"])
        self.assertEqual(
            _resolve_brands("clothing"),
            [
                "mangoman",
                "mangooutlet",
                "trendyolmilla",
                "trendyolman",
                "hm",
                "defacto",
                "mavi",
                "jackjones",
            ],
        )

        self.assertEqual(set(MANGO_BRAND_CONFIGS.keys()), {"mangoman", "mangooutlet"})
        self.assertEqual(set(TRENDYOL_BRAND_CONFIGS.keys()), {"trendyolmilla", "trendyolman"})

        for slug in all_brands:
            self.assertIn(slug, BRAND_DISPLAY_NAMES)
            built = _build_scraper(
                slug,
                section="all",
                max_categories=1,
                include_regular=False,
                only_discounted=True,
                app_settings=None,
            )
            self.assertEqual(built.brand, slug)
            self.assertEqual(built.default_run_mode, get_brand_default_run_mode(slug))
            built.close()

    def test_target_mode_filtering_splits_cloud_and_local_brands(self) -> None:
        all_brands = _resolve_brands("all")
        cloud_only, skipped_local = _filter_brands_by_target_mode(all_brands, "cloud")
        local_only, skipped_cloud = _filter_brands_by_target_mode(all_brands, "local")

        self.assertIn("adidasoutlet", local_only)
        self.assertIn("mangooutlet", local_only)
        self.assertNotIn("adidasoutlet", cloud_only)
        self.assertNotIn("mangooutlet", cloud_only)
        self.assertEqual(set(cloud_only) | set(local_only), set(all_brands))
        self.assertEqual(set(skipped_local), set(local_only))
        self.assertEqual(set(skipped_cloud), set(cloud_only))

    def test_blocked_scraper_records_blocked_status_in_scrape_logs(self) -> None:
        mock_db = MagicMock()
        mock_db.get_user_settings.return_value = UserSettings()
        mock_db.start_scrape_log.return_value = 999

        mock_settings = MagicMock()
        mock_settings.scraper_run_mode = "cloud"
        mock_settings.request_timeout_seconds = 10
        mock_settings.min_jitter_seconds = 0.0
        mock_settings.max_jitter_seconds = 0.0

        fake_scraper = MangoScraper("mangooutlet", fetch_variant_details=False)
        with (
            patch("scraper.main.get_settings", return_value=mock_settings),
            patch("scraper.main.DatabaseClient", return_value=mock_db),
            patch("scraper.main._build_scraper", return_value=fake_scraper),
            patch.object(fake_scraper.http, "get_text", side_effect=ScraperBlockedError("HTTP 403 WAF")),
        ):
            args = parse_args(["--brand", "mangooutlet", "--run-mode", "cloud", "--skip-ai", "--no-notify"])
            exit_code = run_pipeline(args)

        self.assertEqual(exit_code, 1)
        mock_db.start_scrape_log.assert_called_once_with(
            brand="mangooutlet",
            engine="mango",
            run_mode=ScrapeRunMode.CLOUD,
        )
        finish_kwargs = mock_db.finish_scrape_log.call_args.kwargs
        self.assertEqual(finish_kwargs["status"], ScrapeStatus.BLOCKED)
        self.assertIn("403", finish_kwargs["error_message"])


if __name__ == "__main__":
    unittest.main()
