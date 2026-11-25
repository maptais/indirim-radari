"""Unit tests for AI top-15/brand capping, batch Supabase I/O, and soft per-brand time budgets."""

from __future__ import annotations

import json
import time
import unittest
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from pydantic import SecretStr

import scraper.main as main_mod
from scraper.core.ai_scorer import AIStyleScorer
from scraper.core.config import AppSettings, CategoryThreshold, UserSettings
from scraper.core.db import DatabaseClient
from scraper.core.price_analyzer import PriceAnalysis
from scraper.engines.base import (
    ProductCategory,
    ProductGender,
    ProductItem,
    ScrapeResult,
    ScrapeStatus,
)
from scraper.engines.independents.defacto import DeFactoScraper


def _make_app_settings() -> AppSettings:
    return AppSettings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key=SecretStr("service-role-test-key"),
        gemini_api_key=SecretStr("AIzaSyTestFakeKey1234567890"),
        gemini_model="gemini-2.5-flash",
    )


def _make_top(
    external_id: str,
    *,
    current_price: Decimal,
    original_price: Decimal,
) -> ProductItem:
    return ProductItem(
        brand="defacto",
        external_id=external_id,
        title=f"Relaxed Fit Pamuklu Kazak {external_id}",
        category=ProductCategory.TOPS,
        gender=ProductGender.MEN,
        url=f"https://www.defacto.com.tr/kazak-{external_id}",
        current_price=current_price,
        original_price=original_price,
        available_sizes=["M", "L"],
        fabric_info="%100 Pamuk",
    )


class TestBatchDatabaseOperations(unittest.TestCase):
    """Verify DatabaseClient bulk read/write chunking and NULL-safe AI column separation."""

    def test_upsert_products_batch_chunks_and_separates_ai_keysets(self) -> None:
        mock_client = MagicMock()
        seen_chunks: list[list[dict[str, Any]]] = []

        def fake_upsert(chunk: list[dict[str, Any]], on_conflict: str = "") -> Any:
            seen_chunks.append(chunk)
            exec_mock = MagicMock()
            exec_mock.execute.return_value = SimpleNamespace(
                data=[{"id": f"id-{r['external_id']}", "external_id": r["external_id"]} for r in chunk]
            )
            return exec_mock

        mock_client.table.return_value.upsert.side_effect = fake_upsert

        db = DatabaseClient.__new__(DatabaseClient)
        db._client = mock_client

        payloads: list[dict[str, Any]] = []
        # 5 scored items (have ai_score & ai_comment)
        for i in range(5):
            payloads.append(
                _make_top(f"scored-{i}", current_price=Decimal("500"), original_price=Decimal("1000")).to_db_payload(
                    is_real_discount=True,
                    ai_score=8,
                    ai_comment="Harika.",
                )
            )
        # 125 unscored items (omit ai_score & ai_comment) + 1 duplicate to test dedupe
        for i in range(125):
            payloads.append(
                _make_top(f"plain-{i}", current_price=Decimal("600"), original_price=Decimal("1000")).to_db_payload(
                    is_real_discount=True,
                )
            )
        payloads.append(
            _make_top("plain-0", current_price=Decimal("550"), original_price=Decimal("1000")).to_db_payload(
                is_real_discount=True,
            )
        )

        saved = db.upsert_products_batch(payloads, chunk_size=100)
        # 5 scored + 125 unique plain = 130 rows total
        self.assertEqual(len(saved), 130)
        # Chunked into: [5 scored], [100 plain], [25 plain] = 3 batch calls (NOT 130!)
        self.assertEqual(len(seen_chunks), 3)
        chunk_sizes = sorted(len(c) for c in seen_chunks)
        self.assertEqual(chunk_sizes, [5, 25, 100])
        # Verify no chunk mixes rows with ai_score and rows without ai_score
        for chunk in seen_chunks:
            keysets = {frozenset(r.keys()) for r in chunk}
            self.assertEqual(len(keysets), 1)

    def test_record_price_history_batch_chunks_properly(self) -> None:
        mock_client = MagicMock()
        insert_sizes: list[int] = []

        def fake_insert(chunk: list[dict[str, Any]]) -> Any:
            insert_sizes.append(len(chunk))
            exec_mock = MagicMock()
            exec_mock.execute.return_value = SimpleNamespace(data=[{"id": i} for i in range(len(chunk))])
            return exec_mock

        mock_client.table.return_value.insert.side_effect = fake_insert
        db = DatabaseClient.__new__(DatabaseClient)
        db._client = mock_client

        records = [{"product_id": f"pid-{i}", "price": 500.0, "original_price": 1000.0, "discount_percent": 50} for i in range(260)]
        inserted = db.record_price_history_batch(records, chunk_size=120)
        self.assertEqual(inserted, 260)
        self.assertEqual(insert_sizes, [120, 120, 20])


class TestAIScoringTop15CapAndBatchPipeline(unittest.TestCase):
    """Verify pipeline batches Supabase I/O and caps Gemini AI scoring to top-15 new/price-dropped strict matches."""

    def test_pipeline_uses_batch_io_and_scores_only_top_15_new_or_price_dropped(self) -> None:
        # 20 brand-new strict products with discounts ranging from 40% to 78%
        products: list[ProductItem] = []
        for idx in range(20):
            disc_pct = 40 + idx * 2  # 40, 42, ..., 78
            cur = Decimal(str(1000 - disc_pct * 10))
            products.append(_make_top(f"new-{disc_pct}", current_price=cur, original_price=Decimal("1000")))

        # Existing product that ALREADY has an ai_score=9 (discount 85% -> must NOT be re-scored)
        products.append(_make_top("existing-scored", current_price=Decimal("150"), original_price=Decimal("1000")))
        # Existing product with NO ai_score and UNCHANGED price (discount 80% -> must NOT be scored)
        products.append(_make_top("existing-unchanged", current_price=Decimal("200"), original_price=Decimal("1000")))
        # Existing product with NO ai_score whose price DROPPED from 400 -> 180 (discount 82% -> MUST be scored!)
        products.append(_make_top("existing-dropped", current_price=Decimal("180"), original_price=Decimal("1000")))

        class BatchFakeDB:
            def __init__(self) -> None:
                self.batch_lookup_calls = 0
                self.bulk_history_calls = 0
                self.batch_upsert_calls = 0
                self.batch_history_insert_calls = 0
                self.upserted_payloads: list[dict[str, Any]] = []
                self.inserted_history_count = 0
                self.cleanup_calls: list[list[str]] = []

            def get_user_settings(self) -> UserSettings:
                return UserSettings(
                    target_genders=["men", "unisex"],
                    top_sizes=["M", "L"],
                    category_thresholds={
                        "tops": CategoryThreshold(min_discount_percent=35, max_price_try=Decimal("1500")),
                    },
                    min_ai_notify_score=7,
                    notifications_enabled=False,
                )

            def start_scrape_log(self, **_kwargs: Any) -> int:
                return 1

            def finish_scrape_log(self, *_args: Any, **_kwargs: Any) -> None:
                return None

            def get_products_by_external_ids(
                self, _brand: str, external_ids: list[str], *, chunk_size: int = 150
            ) -> dict[str, dict[str, Any]]:
                self.batch_lookup_calls += 1
                existing: dict[str, dict[str, Any]] = {
                    "existing-scored": {
                        "id": "uuid-existing-scored",
                        "external_id": "existing-scored",
                        "current_price": 150.0,
                        "ai_score": 9,
                        "ai_comment": "Zaten puanlandı.",
                    },
                    "existing-unchanged": {
                        "id": "uuid-existing-unchanged",
                        "external_id": "existing-unchanged",
                        "current_price": 200.0,
                        "ai_score": None,
                    },
                    "existing-dropped": {
                        "id": "uuid-existing-dropped",
                        "external_id": "existing-dropped",
                        "current_price": 400.0,
                        "ai_score": None,
                    },
                }
                return {k: v for k, v in existing.items() if k in external_ids}

            def get_price_history_bulk(
                self, product_ids: list[str], *, days: int = 45, chunk_size: int = 50
            ) -> dict[str, list[dict[str, Any]]]:
                self.bulk_history_calls += 1
                return {pid: [] for pid in product_ids}

            def upsert_products_batch(
                self, payloads: list[dict[str, Any]], *, chunk_size: int = 150
            ) -> list[dict[str, Any]]:
                self.batch_upsert_calls += 1
                self.upserted_payloads.extend(payloads)
                return [{"id": f"uuid-{p['external_id']}", **p} for p in payloads]

            def record_price_history_batch(
                self, records: list[dict[str, Any]], *, chunk_size: int = 150
            ) -> int:
                self.batch_history_insert_calls += 1
                self.inserted_history_count += len(records)
                return len(records)

            def cleanup_stale_products(self, brands: list[str], *, max_age_hours: int = 72) -> int:
                self.cleanup_calls.append(list(brands))
                return 0

        class FakeScraper:
            engine_name = "defacto"
            budget_exceeded = False

            def __enter__(self) -> FakeScraper:
                return self

            def __exit__(self, *_args: Any) -> None:
                return None

            def run(self) -> ScrapeResult:
                return ScrapeResult(
                    brand="defacto",
                    engine="defacto",
                    status=ScrapeStatus.SUCCESS,
                    products=products,
                    fetched_count=len(products),
                )

        scored_ids: list[str] = []

        class FakeScorer:
            is_configured = True
            model = "gemini-2.5-flash"

            def __init__(self, *_a: Any, **_kw: Any) -> None:
                pass

            @staticmethod
            def should_score_product(
                analysis: PriceAnalysis, *, existing_product: dict[str, Any] | None = None
            ) -> tuple[bool, str]:
                return AIStyleScorer.should_score_product(analysis, existing_product=existing_product)

            def score_product(self, product: ProductItem, *, style_profile: str) -> Any:
                scored_ids.append(product.external_id)
                return SimpleNamespace(ai_score=8, ai_comment="Uygun.", status="scored", error_message=None)

        fake_db = BatchFakeDB()
        args = main_mod.parse_args(["--brand", "defacto", "--run-mode", "cloud", "--no-notify"])

        with (
            patch.object(main_mod, "get_settings", return_value=_make_app_settings()),
            patch.object(main_mod, "DatabaseClient", return_value=fake_db),
            patch.object(main_mod, "_build_scraper", return_value=FakeScraper()),
            patch.object(main_mod, "AIStyleScorer", FakeScorer),
        ):
            rc = main_mod.run_pipeline(args)

        self.assertEqual(rc, 0)
        # Exactly 1 batch call per stage (zero N+1 queries!)
        self.assertEqual(fake_db.batch_lookup_calls, 1)
        self.assertEqual(fake_db.bulk_history_calls, 1)
        self.assertEqual(fake_db.batch_upsert_calls, 1)
        self.assertEqual(fake_db.batch_history_insert_calls, 1)
        self.assertEqual(len(fake_db.upserted_payloads), 23)
        self.assertEqual(fake_db.inserted_history_count, 23)

        # Capped strictly to top-15 highest discounts among new + price-dropped items!
        self.assertEqual(len(scored_ids), 15)
        self.assertEqual(scored_ids[0], "existing-dropped")  # 82% off price-dropped item came first!
        self.assertNotIn("existing-scored", scored_ids)
        self.assertNotIn("existing-unchanged", scored_ids)
        # Lowest discounts (new-40 .. new-50) were skipped by the top-15 cap
        self.assertNotIn("new-40", scored_ids)
        self.assertIn("new-78", scored_ids)
        self.assertEqual(fake_db.cleanup_calls, [["defacto"]])


class TestSoftBrandTimeBudget(unittest.TestCase):
    """Verify soft time budget stops scraping gracefully while preserving collected products."""

    def test_defacto_soft_budget_preserves_collected_items_and_skips_stale_cleanup(self) -> None:
        scraper = DeFactoScraper(
            section="men",
            max_categories=2,
            max_pages_per_category=5,
            min_jitter_seconds=0.0,
            max_jitter_seconds=0.0,
            max_duration_seconds=60.0,
        )

        doc1 = {
            "LongCode": "DF-BUDGET-1",
            "Name": "Regular Fit Erkek Kazak",
            "SeoName": "regular-fit-erkek-kazak",
            "ProductVariantIndex": 1001,
            "Price": 1000.0,
            "DiscountedPrice": 500.0,
            "PersonaDescriptionName": "Erkek",
            "Sizes": [{"SizeName": "M", "StockQuantity": 5}],
        }
        page1_html = f"<div data-documents='{json.dumps(doc1)}'></div>"

        calls: list[str] = []

        def fake_get_text(url: str, **_kwargs: Any) -> str:
            calls.append(url)
            # Simulate soft budget expiring right after page 1 returns!
            scraper._budget_deadline = time.monotonic() - 1.0
            return page1_html

        with patch.object(scraper.http, "get_text", side_effect=fake_get_text):
            result = scraper.run()

        self.assertEqual(result.status, ScrapeStatus.SUCCESS)
        self.assertTrue(scraper.budget_exceeded)
        # Stopped after page 1 instead of fetching all 10 pages
        self.assertEqual(len(calls), 1)
        # Preserved the product collected before budget expired!
        self.assertEqual(result.fetched_count, 1)
        self.assertEqual(result.products[0].external_id, "1001")
        scraper.close()


if __name__ == "__main__":
    unittest.main()
