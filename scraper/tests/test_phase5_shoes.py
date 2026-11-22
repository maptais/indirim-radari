"""Unit tests for Phase 5 shoe & outlet scrapers, shoe size normalization, and CLI brand resolution."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal
from unittest.mock import patch

from scraper.core.config import CategoryThreshold, UserSettings
from scraper.core.filters import ProductFilter, match_sizes, normalize_size_tokens
from scraper.core.http import infer_product_category, is_blocked_response, parse_try_price
from scraper.core.notifier import BRAND_DISPLAY_NAMES
from scraper.engines.base import ProductCategory, ScrapeStatus, ScraperBlockedError
from scraper.engines.eren import EREN_BRAND_CONFIGS, ErenScraper
from scraper.engines.independents import (
    AdidasOutletScraper,
    BarcinScraper,
    InStreetScraper,
    PumaOutletScraper,
    SneaksUpScraper,
    SportiveScraper,
)
from scraper.main import _build_scraper, _resolve_brands


class TestShoeSizeAndHttpHelpers(unittest.TestCase):
    """Verify shoe size normalization, price parsing, and WAF detection helpers."""

    def test_shoe_size_normalization_formats(self) -> None:
        self.assertIn("42.5", normalize_size_tokens("42,5", ProductCategory.SHOES))
        self.assertIn("42.5", normalize_size_tokens("42 1/2", ProductCategory.SHOES))
        self.assertIn("42.5", normalize_size_tokens("42½", ProductCategory.SHOES))
        self.assertIn("42.5", normalize_size_tokens("42 2/3", ProductCategory.SHOES))
        self.assertIn("42 2/3", normalize_size_tokens("42⅔", ProductCategory.SHOES))
        self.assertIn("43", normalize_size_tokens("43 1/3", ProductCategory.SHOES))
        self.assertIn("42.5", normalize_size_tokens("EU 42,5 / UK 8 / US 9", ProductCategory.SHOES))
        self.assertIn("42.5", normalize_size_tokens("US 9 | EU 42.5", ProductCategory.SHOES))
        self.assertNotIn("9", normalize_size_tokens("EU 42,5 / US 9", ProductCategory.SHOES))
        self.assertIn("42", normalize_size_tokens("42 EU", ProductCategory.SHOES))
        self.assertIn("42", normalize_size_tokens("42-43", ProductCategory.SHOES))
        self.assertIn("43", normalize_size_tokens("42-43", ProductCategory.SHOES))

        matched = match_sizes(
            ["40", "42 2/3", "43 1/3", "44", "EU 42,5 / US 9"],
            ["42", "42.5", "43"],
            category=ProductCategory.SHOES,
        )
        self.assertEqual(matched, ["42 2/3", "43 1/3", "EU 42,5 / US 9"])

    def test_parse_try_price_variants(self) -> None:
        self.assertEqual(parse_try_price("1.299,90 TL"), Decimal("1299.90"))
        self.assertEqual(parse_try_price("6.749,00 ₺"), Decimal("6749.00"))
        self.assertEqual(parse_try_price("2.499"), Decimal("2499.00"))
        self.assertEqual(parse_try_price("2959.90"), Decimal("2959.90"))
        self.assertEqual(parse_try_price(1799), Decimal("1799.00"))
        self.assertIsNone(parse_try_price(0))
        self.assertIsNone(parse_try_price(""))

    def test_waf_block_detection(self) -> None:
        self.assertTrue(is_blocked_response(403, "Access Denied"))
        self.assertTrue(is_blocked_response(429, ""))
        self.assertTrue(
            is_blocked_response(200, '<div id="sec-if-cpt-container"></div>')
        )
        self.assertTrue(
            is_blocked_response(200, "<html>Just a moment...</html>", {"cf-mitigated": "challenge"})
        )
        self.assertFalse(is_blocked_response(200, '{"products": []}'))

    def test_category_inference_avoids_shoe_accessories(self) -> None:
        self.assertEqual(
            infer_product_category("Nike Dunk Low Erkek Sneaker"),
            ProductCategory.SHOES,
        )
        self.assertEqual(
            infer_product_category("Puma Spor Çorap 3'lü Paket", "ayakkabi aksesuar"),
            ProductCategory.OTHER,
        )


class TestPhase5BrandParsers(unittest.TestCase):
    """Verify parsers for all 9 Phase 5 shoe & outlet scrapers and filter integration."""

    def setUp(self) -> None:
        self.settings = UserSettings(
            shoe_sizes=["42", "42.5", "43"],
            negative_keywords=["pembe", "baskılı"],
            max_polyester_percent=40,
            category_thresholds={
                "shoes": CategoryThreshold(min_discount_percent=30, max_price_try=Decimal("3500")),
                "other": CategoryThreshold(min_discount_percent=35, max_price_try=Decimal("2000")),
            },
        )
        self.product_filter = ProductFilter(self.settings)

    def test_eren_occasion_maps_us_shoe_label_to_eu_size_and_filters(self) -> None:
        scraper = ErenScraper("occasion", section="men", fetch_variant_details=False)
        raw_listing = {
            "pk": 618163,
            "sku": "6308331",
            "base_code": "A11861C",
            "name": "Converse Run Star Trainer Unisex Beyaz Deri Sneaker",
            "price": "1999",
            "retail_price": "5499",
            "absolute_url": "/converse-run-star-trainer-unisex-beyaz-deri-sneaker-a11861c-1/",
            "in_stock": True,
            "stock": 12,
            "productimage_set": [{"image": "https://cdn.example.com/converse.jpg"}],
            "attributes": {
                "integration_cinsiyet": "Unisex",
                "integration_erp_icerik": "%100 İnek Derisi",
                "integration_renk": "456",
            },
        }
        detail_payload = {
            "product": raw_listing,
            "variants": [
                {"attribute_key": "integration_renk", "attribute_name": "Renk", "options": []},
                {
                    "attribute_key": "integration_beden",
                    "attribute_name": "Beden",
                    "options": [
                        {
                            "label": "8,5",
                            "value": "8,5",
                            "in_stock": True,
                            "product": {"stock": 2, "attributes": {"integration_trBeden": "42"}},
                        },
                        {
                            "label": "9",
                            "value": "9",
                            "in_stock": True,
                            "product": {"stock": 10, "attributes": {"filterable_size": "42,5"}},
                        },
                        {
                            "label": "10",
                            "value": "10",
                            "in_stock": False,
                            "product": {"stock": 0, "attributes": {"integration_trBeden": "44"}},
                        },
                    ],
                },
            ],
        }

        item = scraper.parse_listing_product(
            raw_listing,
            category_path="/erkek-ayakkabi/",
            detail_payload=detail_payload,
        )
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.brand, "occasion")
        self.assertEqual(item.category, ProductCategory.SHOES)
        self.assertEqual(item.available_sizes, ["42", "42,5"])
        self.assertTrue(item.is_critical_stock)
        self.assertEqual(item.fabric_info, "%100 İnek Derisi")
        self.assertEqual(item.discount_percent, 64)

        f_res = self.product_filter.evaluate(item)
        self.assertTrue(f_res.passed, f_res.reasons)
        self.assertEqual(f_res.matched_sizes, ["42", "42,5"])
        scraper.close()

    def test_barcin_and_sportive_akinon_parsers(self) -> None:
        barcin = BarcinScraper(section="men", fetch_variant_details=False)
        raw = {
            "pk": 288103,
            "sku": "200100843277",
            "base_code": "NKE45",
            "name": "adidas Runfalcon 5 Running Erkek Spor Ayakkabı",
            "price": "2499.90",
            "retail_price": "3949",
            "absolute_url": "/adidas-runfalcon-5-running-erkek-spor-ayakkabi/",
            "in_stock": True,
            "productimage_set": [{"image": "https://cdn.example.com/runfalcon.jpg"}],
            "attributes": {
                "integration_gender": "Erkek",
                "integration_category": "Ayakkabı",
                "integration_size": "42.5",
                "integration_color_hash": "Siyah-Beyaz",
            },
        }
        item = barcin.parse_listing_product(raw, category_path="/indirim/")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.brand, "barcin")
        self.assertEqual(item.category, ProductCategory.SHOES)
        self.assertTrue(self.product_filter.evaluate(item).passed)
        barcin.close()

        sportive = SportiveScraper(section="men", fetch_variant_details=False)
        self.assertEqual(sportive.brand, "sportive")
        self.assertEqual(sportive.engine_name, "sportive")
        sportive.close()

    def test_instreet_webservice_parser(self) -> None:
        scraper = InStreetScraper(section="men")
        raw = {
            "brandName": "Reebok",
            "productId": "36311885104",
            "sku": "102697682",
            "price": 1799,
            "oldPrice": 3299.99,
            "imageUrl": "https://floimages.mncdn.com/test.jpg",
            "name": "Aero Surge I Beyaz Erkek Sneaker",
            "relativeUrl": "/urun/reebok-aero-surge-i-beyaz-erkek-sneaker-102697682",
            "isInStock": True,
            "breadcrumb": [{"name": "Ayakkabı"}, {"name": "Sneaker"}],
            "options": [
                {"sku": "1", "option_value": "41", "is_in_stock": False, "qty": 0},
                {"sku": "2", "option_value": "42", "is_in_stock": True, "qty": 2},
                {"sku": "3", "option_value": "43", "is_in_stock": True, "qty": 5},
            ],
        }
        item = scraper.parse_product(raw)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.brand, "instreet")
        self.assertEqual(item.title, "Reebok Aero Surge I Beyaz Erkek Sneaker")
        self.assertEqual(item.available_sizes, ["42", "43"])
        self.assertTrue(item.is_critical_stock)
        self.assertEqual(item.discount_percent, 45)
        f_res = self.product_filter.evaluate(item)
        self.assertTrue(f_res.passed, f_res.reasons)
        self.assertEqual(f_res.matched_sizes, ["42", "43"])
        scraper.close()

    def test_sneaksup_next_data_parser(self) -> None:
        scraper = SneaksUpScraper(section="men")
        next_payload = {
            "props": {
                "pageProps": {
                    "productList": {
                        "products": {
                            "data": [
                                {
                                    "variantId": 66843,
                                    "productId": 7575,
                                    "productName": "Nike Dunk Low Retro",
                                    "brandName": "NIKE",
                                    "slug": "nike-dunk-low-retro-p-66843",
                                    "breadcrumb": [{"name": "ERKEK"}, {"name": "AYAKKABI"}, {"name": "Sneaker"}],
                                    "price": {"oldPrice": 5499, "newPrice": 3299.4, "specialPrice": 0},
                                    "pictures": [{"url": "https://img.example.com/dunk.jpg"}],
                                    "variants": [
                                        {"specName": "size", "specValueName": "41", "quantity": 0},
                                        {"specName": "size", "specValueName": "42.5", "quantity": 3},
                                    ],
                                }
                            ]
                        }
                    }
                }
            }
        }
        html = f'<html><script id="__NEXT_DATA__" type="application/json">{json.dumps(next_payload)}</script></html>'
        raw_items = scraper.extract_next_data_products(html)
        self.assertEqual(len(raw_items), 1)
        item = scraper.parse_product(raw_items[0], category_path="/sneaks-out-erkek")
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.brand, "sneaksup")
        self.assertEqual(item.available_sizes, ["42.5"])
        self.assertTrue(item.is_critical_stock)
        self.assertEqual(item.discount_percent, 40)
        self.assertTrue(self.product_filter.evaluate(item).passed)
        scraper.close()

    def test_puma_outlet_listing_and_spconfig_parsers(self) -> None:
        listing_html = """
        <script type="application/ld+json">
        {"@type": "Product", "name": "Puma RS-X Efekt Erkek Ayakkabı", "sku": "390776_01",
         "url": "https://tr.puma.com/rs-x-efekt-390776-01.html",
         "image": "https://images.puma.net/rsx.jpg",
         "offers": {"price": "2799"}}
        </script>
        <div class="grid__item">
          <div class="product-item" data-product-sku="390776_01">
            <a class="product-item__name" href="https://tr.puma.com/rs-x-efekt-390776-01.html">Puma RS-X Efekt Erkek Ayakkabı</a>
            <div data-product-id="367781">
              <span data-price-amount="2799" data-price-type="finalPrice"></span>
              <span data-price-amount="4999" data-price-type="oldPrice"></span>
            </div>
          </div>
        </div>
        """
        cards = PumaOutletScraper.parse_listing_html(listing_html)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].current_price, Decimal("2799.00"))
        self.assertEqual(cards[0].original_price, Decimal("4999.00"))

        detail_html = """
        <script type="text/x-magento-init">
        {"#product": {"configurable": {"spConfig": {
          "attributes": {
            "141": {
              "id": "141", "code": "size", "label": "BEDEN",
              "options": [
                {"id": "1", "label": "41", "products": [], "out_of_stock": ["100"]},
                {"id": "2", "label": "42.5", "products": ["101"], "out_of_stock": []},
                {"id": "3", "label": "43", "products": ["102"], "out_of_stock": []}
              ]
            }
          }
        }}}}
        </script>
        """
        sizes, critical = PumaOutletScraper.extract_sizes_from_detail_html(detail_html)
        self.assertEqual(sizes, ["42.5", "43"])
        self.assertTrue(critical)

    def test_adidas_outlet_parser_and_blocked_status_handling(self) -> None:
        scraper = AdidasOutletScraper(section="men", fetch_availability=False)
        raw_item = {
            "productId": "IG1024",
            "displayName": "adidas Samba OG Erkek Spor Ayakkabı",
            "price": 5499,
            "salePrice": 3299,
            "link": "/samba-og-ayakkabi/IG1024.html",
            "image": {"src": "https://assets.adidas.com/samba.jpg"},
            "subTitle": "Erkek Originals",
        }
        avail_payload = {
            "variation_list": [
                {"size": "41 1/3", "availability": 0, "availability_status": "NOT_AVAILABLE"},
                {"size": "42 2/3", "availability": 2, "availability_status": "IN_STOCK"},
                {"size": "43 1/3", "availability": 10, "availability_status": "IN_STOCK"},
            ]
        }
        item = scraper.parse_plp_item(raw_item, query_slug="erkek-outlet-ayakkabi", availability_payload=avail_payload)
        self.assertIsNotNone(item)
        assert item is not None
        self.assertEqual(item.brand, "adidasoutlet")
        self.assertEqual(item.available_sizes, ["42 2/3", "43 1/3"])
        self.assertTrue(item.is_critical_stock)
        f_res = self.product_filter.evaluate(item)
        self.assertTrue(f_res.passed, f_res.reasons)
        self.assertEqual(f_res.matched_sizes, ["42 2/3", "43 1/3"])

        # Verify Akamai block surfaces cleanly as ScrapeStatus.BLOCKED without crashing
        with patch.object(scraper.http, "get_json", side_effect=ScraperBlockedError("HTTP 403 Akamai block")):
            result = scraper.run()
        self.assertEqual(result.status, ScrapeStatus.BLOCKED)
        self.assertIn("Akamai", result.error_message or "")
        scraper.close()

    def test_brand_resolution_groups_and_slug_consistency(self) -> None:
        self.assertEqual(_resolve_brands("eren"), ["superstep", "occasion", "fashfed"])
        shoes_group = _resolve_brands("shoes")
        self.assertEqual(
            shoes_group,
            [
                "superstep",
                "occasion",
                "fashfed",
                "instreet",
                "sneaksup",
                "barcin",
                "sportive",
                "adidasoutlet",
                "pumaoutlet",
            ],
        )
        all_brands = _resolve_brands("all")
        self.assertGreaterEqual(len(all_brands), 14)
        self.assertEqual(len(all_brands), 22)
        self.assertEqual(_resolve_brands("eren,instreet,superstep"), ["superstep", "occasion", "fashfed", "instreet"])

        for slug in shoes_group:
            self.assertIn(slug, BRAND_DISPLAY_NAMES)
            built = _build_scraper(
                slug,
                section="men",
                max_categories=2,
                include_regular=False,
                only_discounted=True,
                app_settings=None,
            )
            self.assertEqual(built.brand, slug)
            built.close()
        self.assertEqual(set(EREN_BRAND_CONFIGS.keys()), {"superstep", "occasion", "fashfed"})


if __name__ == "__main__":
    unittest.main()
