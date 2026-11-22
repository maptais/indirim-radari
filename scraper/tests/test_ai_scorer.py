"""Unit tests for `scraper.core.ai_scorer`."""

from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from pydantic import SecretStr

from scraper.core.ai_scorer import (
    AIStyleScorer,
    StyleScoreResponse,
    _sanitize_comment,
)
from scraper.core.config import AppSettings, UserSettings
from scraper.core.notifier import PushNotifier
from scraper.core.price_analyzer import PriceAnalysis
from scraper.engines.base import ProductCategory, ProductItem


def _make_settings(api_key: str | None = "AIzaSyTestFakeKey1234567890") -> AppSettings:
    return AppSettings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key=SecretStr("service-role-test-key"),
        gemini_api_key=SecretStr(api_key) if api_key is not None else None,
        gemini_model="gemini-2.5-flash",
    )


def _sample_product(**overrides: Any) -> ProductItem:
    data: dict[str, Any] = {
        "brand": "pullandbear",
        "external_id": "pb-404",
        "title": "Relaxed Fit %100 Pamuk Overshirt Ceket",
        "category": ProductCategory.OUTERWEAR,
        "url": "https://www.pullandbear.com/tr/overshirt-ceket-l0404",
        "image_url": "https://static.pullandbear.net/assets/overshirt.jpg",
        "current_price": Decimal("999.00"),
        "original_price": Decimal("1999.00"),
        "available_sizes": ["M", "L"],
        "fabric_info": "%100 pamuk",
        "polyester_percent": 0,
    }
    data.update(overrides)
    return ProductItem(**data)


class FakeModelsAPI:
    def __init__(self, handler: Any) -> None:
        self._handler = handler
        self.calls: list[dict[str, Any]] = []

    def generate_content(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._handler(**kwargs)


class FakeGenAIClient:
    def __init__(self, handler: Any) -> None:
        self.models = FakeModelsAPI(handler)


class TestAIStyleScorer(unittest.TestCase):
    def test_unconfigured_or_placeholder_key_skips_gracefully(self) -> None:
        for key in (None, "", "your-gemini-api-key", "placeholder"):
            scorer = AIStyleScorer(_make_settings(api_key=key))
            self.assertFalse(scorer.is_configured)
            result = scorer.score_product(_sample_product(), style_profile="Minimalist sade tarz")
            self.assertEqual(result.status, "skipped_unconfigured")
            self.assertIsNone(result.ai_score)
            self.assertIsNone(result.ai_comment)

    def test_should_score_product_eligibility_rules(self) -> None:
        real_discount = PriceAnalysis(is_real_discount=True, lowest_price_days=30)
        fake_discount = PriceAnalysis(is_real_discount=False, lowest_price_days=2)

        ok_new, reason_new = AIStyleScorer.should_score_product(real_discount, existing_product=None)
        self.assertTrue(ok_new)
        self.assertEqual(reason_new, "eligible")

        ok_unscored, _ = AIStyleScorer.should_score_product(
            real_discount,
            existing_product={"id": "p1", "ai_score": None},
        )
        self.assertTrue(ok_unscored)

        ok_already, reason_already = AIStyleScorer.should_score_product(
            real_discount,
            existing_product={"id": "p1", "ai_score": 8, "ai_comment": "Tarzına çok uygun."},
        )
        self.assertFalse(ok_already)
        self.assertEqual(reason_already, "already_scored")

        ok_fake, reason_fake = AIStyleScorer.should_score_product(
            fake_discount,
            existing_product=None,
        )
        self.assertFalse(ok_fake)
        self.assertEqual(reason_fake, "not_real_discount")

    def test_multimodal_structured_scoring_success(self) -> None:
        def fake_generate(**kwargs: Any) -> Any:
            return SimpleNamespace(
                parsed=StyleScoreResponse(
                    ai_score=9,
                    ai_comment="Minimalist ve relaxed kesimli pamuklu overshirt tarz profilinle birebir örtüşüyor.",
                ),
                text=None,
            )

        fake_client = FakeGenAIClient(fake_generate)
        scorer = AIStyleScorer(
            _make_settings(),
            client=fake_client,
            image_fetcher=lambda _url: (b"\xff\xd8\xff\xe0fake-jpeg-bytes", "image/jpeg"),
            min_request_interval_seconds=0.0,
            retry_backoff_seconds=0.0,
        )
        self.assertTrue(scorer.is_configured)

        result = scorer.score_product(
            _sample_product(),
            style_profile="Minimalist, sade, pamuklu, relaxed kesimler.",
        )

        self.assertEqual(result.status, "scored")
        self.assertEqual(result.ai_score, 9)
        self.assertIn("pamuklu", result.ai_comment or "")
        self.assertTrue(result.used_image)
        self.assertEqual(len(fake_client.models.calls), 1)
        sent_contents = fake_client.models.calls[0]["contents"]
        self.assertEqual(len(sent_contents), 2)  # [Part(image), prompt_text]

    def test_falls_back_to_text_only_when_image_fetch_fails(self) -> None:
        def fake_generate(**kwargs: Any) -> Any:
            return SimpleNamespace(
                parsed=None,
                text='```json\n{"ai_score": 7, "ai_comment": "Sade tasarımı ve pamuklu kumaşı günlük kombinlere çok uygun."}\n```',
            )

        fake_client = FakeGenAIClient(fake_generate)
        scorer = AIStyleScorer(
            _make_settings(),
            client=fake_client,
            image_fetcher=lambda _url: None,
            min_request_interval_seconds=0.0,
            retry_backoff_seconds=0.0,
        )

        result = scorer.score_product(_sample_product(), style_profile="Sade ve zamansız kesimler")
        self.assertEqual(result.status, "scored")
        self.assertEqual(result.ai_score, 7)
        self.assertFalse(result.used_image)
        self.assertEqual(len(fake_client.models.calls[0]["contents"]), 1)

    def test_rate_limit_429_returns_safe_fallback_without_raising(self) -> None:
        class QuotaError(RuntimeError):
            code = 429

        def fake_generate(**kwargs: Any) -> Any:
            raise QuotaError("429 RESOURCE_EXHAUSTED: Quota exceeded for Gemini API")

        fake_client = FakeGenAIClient(fake_generate)
        scorer = AIStyleScorer(
            _make_settings(),
            client=fake_client,
            use_images=False,
            max_retries=1,
            retry_backoff_seconds=0.0,
            min_request_interval_seconds=0.0,
        )

        result = scorer.score_product(_sample_product(), style_profile="Minimalist")
        self.assertEqual(result.status, "rate_limited")
        self.assertIsNone(result.ai_score)
        self.assertIsNone(result.ai_comment)
        self.assertIn("RESOURCE_EXHAUSTED", result.error_message or "")
        self.assertEqual(len(fake_client.models.calls), 2)  # 1 initial + 1 retry

    def test_generic_error_returns_safe_fallback(self) -> None:
        def fake_generate(**kwargs: Any) -> Any:
            raise RuntimeError("Transient TLS handshake failure")

        fake_client = FakeGenAIClient(fake_generate)
        scorer = AIStyleScorer(
            _make_settings(),
            client=fake_client,
            use_images=False,
            max_retries=0,
            min_request_interval_seconds=0.0,
        )

        result = scorer.score_product(_sample_product(), style_profile="Minimalist")
        self.assertEqual(result.status, "error")
        self.assertIsNone(result.ai_score)
        self.assertIsNone(result.ai_comment)

    def test_ai_score_gates_push_notification_threshold(self) -> None:
        product = _sample_product()
        analysis = PriceAnalysis(is_real_discount=True, lowest_price_days=45)
        user_settings = UserSettings(notifications_enabled=True, min_ai_notify_score=7)

        allow_low, reason_low = PushNotifier.should_notify_product(
            product,
            analysis=analysis,
            user_settings=user_settings,
            ai_score=5,
        )
        self.assertFalse(allow_low)
        self.assertIn("ai_score_below_threshold", reason_low)

        allow_high, reason_high = PushNotifier.should_notify_product(
            product,
            analysis=analysis,
            user_settings=user_settings,
            ai_score=8,
        )
        self.assertTrue(allow_high)
        self.assertEqual(reason_high, "eligible")

    def test_sanitize_comment_trims_long_multiline_text(self) -> None:
        multiline = "  Harika bir parça.\n   Çok kaliteli pamuklu kumaşa sahip.  "
        self.assertEqual(
            _sanitize_comment(multiline),
            "Harika bir parça. Çok kaliteli pamuklu kumaşa sahip.",
        )
        long_text = "kelime " * 60
        sanitized = _sanitize_comment(long_text)
        self.assertLessEqual(len(sanitized), 240)
        self.assertTrue(sanitized.endswith("..."))


class TestMainPipelineAIIntegration(unittest.TestCase):
    """Verify `scraper.main.run_pipeline` wires AI scoring and push gating properly."""

    def test_pipeline_scores_only_new_real_discounts_and_gates_push(self) -> None:
        from unittest.mock import patch

        import scraper.main as main_mod
        from scraper.core.config import CategoryThreshold
        from scraper.engines.base import ScrapeResult, ScrapeStatus

        new_high_product = _sample_product(
            external_id="new-high",
            title="Relaxed Fit Pamuklu Ceket",
            current_price=Decimal("900.00"),
            original_price=Decimal("1800.00"),
            available_sizes=["M"],
        )
        new_low_product = _sample_product(
            external_id="new-low",
            title="Parlak Sentetik Ceket",
            current_price=Decimal("900.00"),
            original_price=Decimal("1800.00"),
            available_sizes=["M"],
        )
        already_scored_product = _sample_product(
            external_id="already-scored",
            title="Sade Keten Ceket",
            current_price=Decimal("950.00"),
            original_price=Decimal("1900.00"),
            available_sizes=["M"],
        )

        class FakeDB:
            def __init__(self) -> None:
                self.upserts: list[dict[str, Any]] = []
                self.notified: list[str] = []

            def get_user_settings(self) -> UserSettings:
                return UserSettings(
                    top_sizes=["M"],
                    category_thresholds={
                        "outerwear": CategoryThreshold(
                            min_discount_percent=30, max_price_try=Decimal("3000")
                        )
                    },
                    style_profile="Minimalist pamuklu tarz",
                    min_ai_notify_score=7,
                    notifications_enabled=True,
                )

            def start_scrape_log(self, **_kwargs: Any) -> int:
                return 1

            def finish_scrape_log(self, *_args: Any, **_kwargs: Any) -> None:
                return None

            def get_product_by_external_id(self, _brand: str, external_id: str) -> dict[str, Any] | None:
                if external_id == "already-scored":
                    return {
                        "id": "uuid-already",
                        "ai_score": 8,
                        "ai_comment": "Önceden puanlandı.",
                        "notified_at": "2026-01-01T00:00:00Z",
                        "last_notified_price": 950.0,
                    }
                return None

            def get_price_history(self, _product_id: str, days: int = 45) -> list[dict[str, Any]]:
                return []

            def upsert_product(self, product: ProductItem, **kwargs: Any) -> dict[str, Any]:
                record = {
                    "id": f"uuid-{product.external_id}",
                    "external_id": product.external_id,
                    **kwargs,
                }
                if product.external_id == "already-scored" and kwargs.get("ai_score") is None:
                    record["ai_score"] = 8
                    record["ai_comment"] = "Önceden puanlandı."
                self.upserts.append(record)
                return record

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
                    products=[new_high_product, new_low_product, already_scored_product],
                    fetched_count=3,
                )

        scored_ids: list[str] = []

        class FakeScorer:
            is_configured = True
            model = "gemini-2.5-flash"

            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                pass

            @staticmethod
            def should_score_product(analysis: PriceAnalysis, *, existing_product: dict[str, Any] | None = None) -> tuple[bool, str]:
                return AIStyleScorer.should_score_product(analysis, existing_product=existing_product)

            def score_product(self, product: ProductItem, *, style_profile: str) -> Any:
                scored_ids.append(product.external_id)
                if product.external_id == "new-high":
                    return SimpleNamespace(
                        ai_score=9,
                        ai_comment="Tarz profilinle birebir uyumlu.",
                        status="scored",
                        error_message=None,
                    )
                return SimpleNamespace(
                    ai_score=4,
                    ai_comment="Parlak dokusu sade profilinle uyuşmuyor.",
                    status="scored",
                    error_message=None,
                )

        notified_ids: list[tuple[str, int | None]] = []

        class FakeNotifier:
            is_configured = True

            def __init__(self, *_args: Any, **_kwargs: Any) -> None:
                pass

            @staticmethod
            def should_notify_product(
                product: ProductItem,
                *,
                analysis: PriceAnalysis,
                user_settings: UserSettings,
                existing_product: dict[str, Any] | None = None,
                ai_score: int | None = None,
            ) -> tuple[bool, str]:
                return PushNotifier.should_notify_product(
                    product,
                    analysis=analysis,
                    user_settings=user_settings,
                    existing_product=existing_product,
                    ai_score=ai_score,
                )

            def notify_product(self, product: ProductItem, **kwargs: Any) -> Any:
                notified_ids.append((product.external_id, kwargs.get("ai_score")))
                return SimpleNamespace(sent=1)

        fake_db = FakeDB()
        args = main_mod.parse_args(["--brand", "pullandbear", "--run-mode", "cloud"])

        with (
            patch.object(main_mod, "get_settings", return_value=_make_settings()),
            patch.object(main_mod, "DatabaseClient", return_value=fake_db),
            patch.object(main_mod, "_build_scraper", return_value=FakeScraper()),
            patch.object(main_mod, "AIStyleScorer", FakeScorer),
            patch.object(main_mod, "PushNotifier", FakeNotifier),
        ):
            rc = main_mod.run_pipeline(args)

        self.assertEqual(rc, 0)
        # Already-scored product was NOT re-sent to Gemini
        self.assertEqual(scored_ids, ["new-high", "new-low"])
        # Upserts carry new AI scores only for newly scored items
        upsert_map = {row["external_id"]: row for row in fake_db.upserts}
        self.assertEqual(upsert_map["new-high"]["ai_score"], 9)
        self.assertEqual(upsert_map["new-low"]["ai_score"], 4)
        self.assertEqual(upsert_map["already-scored"]["ai_score"], 8)
        # Only new-high (ai_score=9 >= 7) triggered a Web Push notification
        self.assertEqual(notified_ids, [("new-high", 9)])


if __name__ == "__main__":
    unittest.main()
