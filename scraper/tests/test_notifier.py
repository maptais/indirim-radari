"""Unit and integration tests for `scraper.core.notifier`."""

from __future__ import annotations

import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from pydantic import SecretStr
from pywebpush import WebPushException

from scraper.core.config import AppSettings, UserSettings
from scraper.core.notifier import (
    PushNotifier,
    PushPayload,
    format_try_price,
    generate_vapid_keys,
    load_vapid_instance,
)
from scraper.core.price_analyzer import PriceAnalysis
from scraper.engines.base import ProductCategory, ProductItem


class FakeDatabaseClient:
    """In-memory stand-in for `DatabaseClient` used in notifier tests."""

    def __init__(self, subscriptions: list[dict[str, Any]] | None = None) -> None:
        self.subscriptions = list(subscriptions or [])
        self.deactivated_ids: list[str] = []
        self.touched_ids: list[str] = []
        self.marked_products: list[tuple[str, Decimal]] = []

    def get_active_push_subscriptions(self) -> list[dict[str, Any]]:
        return [s for s in self.subscriptions if s.get("is_active", True)]

    def deactivate_push_subscription(self, subscription_id: str) -> None:
        self.deactivated_ids.append(subscription_id)
        for sub in self.subscriptions:
            if sub["id"] == subscription_id:
                sub["is_active"] = False

    def touch_push_subscription_notified(self, subscription_id: str) -> None:
        self.touched_ids.append(subscription_id)

    def mark_product_notified(self, product_id: str, notified_price: Decimal) -> None:
        self.marked_products.append((product_id, notified_price))


def _make_settings(private_key: str | None = None, public_key: str | None = None) -> AppSettings:
    pair = generate_vapid_keys() if private_key is None else None
    priv = private_key if private_key is not None else pair.private_key  # type: ignore[union-attr]
    pub = public_key if public_key is not None else (pair.public_key if pair else None)
    return AppSettings(
        supabase_url="https://example.supabase.co",
        supabase_service_role_key=SecretStr("service-role-test-key"),
        vapid_public_key=pub,
        vapid_private_key=SecretStr(priv) if priv else None,
        vapid_claims_sub="mailto:test@indirimradari.local",
    )


def _sample_product(**overrides: Any) -> ProductItem:
    data: dict[str, Any] = {
        "brand": "pullandbear",
        "external_id": "pb-999",
        "title": "Relaxed Fit Pamuklu Ceket",
        "category": ProductCategory.OUTERWEAR,
        "url": "https://www.pullandbear.com/tr/relaxed-ceket-l0999",
        "image_url": "https://static.pullandbear.net/assets/test.jpg",
        "current_price": Decimal("899.00"),
        "original_price": Decimal("1799.00"),
        "available_sizes": ["M", "L"],
        "is_critical_stock": True,
    }
    data.update(overrides)
    return ProductItem(**data)


class TestPushNotifier(unittest.TestCase):
    def test_generate_vapid_keys_and_sign_roundtrip(self) -> None:
        pair = generate_vapid_keys()
        self.assertEqual(len(pair.public_key), 87)
        self.assertEqual(len(pair.private_key), 43)

        vapid = load_vapid_instance(pair.private_key)
        headers = vapid.sign({"sub": "mailto:test@example.com", "aud": "https://web.push.apple.com"})
        self.assertIn("Authorization", headers)
        self.assertIn("vapid t=", headers["Authorization"])

    def test_format_try_price_turkish(self) -> None:
        self.assertEqual(format_try_price(Decimal("899.00")), "899 TL")
        self.assertEqual(format_try_price(Decimal("1799.00")), "1.799 TL")
        self.assertEqual(format_try_price(Decimal("1299.50")), "1.299,50 TL")

    def test_build_product_payload_turkish_content(self) -> None:
        product = _sample_product()
        payload = PushNotifier.build_product_payload(
            product,
            product_id="uuid-123",
            matched_sizes=["M", "L"],
            lowest_price_days=45,
            ai_score=9,
        )
        self.assertIn("Kritik Stok", payload.title)
        self.assertIn("%50", payload.title)
        self.assertIn("Pull&Bear", payload.title)
        self.assertIn("899 TL", payload.body)
        self.assertIn("1.799 TL", payload.body)
        self.assertIn("Beden: M, L", payload.body)
        self.assertIn("Son 45 günün en düşüğü", payload.body)
        self.assertIn("AI Tarz: 9/10", payload.body)
        self.assertEqual(payload.url, product.url)

        parsed = json.loads(payload.to_json())
        self.assertEqual(parsed["product_id"], "uuid-123")

    def test_should_notify_product_rules(self) -> None:
        product = _sample_product()
        settings = UserSettings(notifications_enabled=True, min_ai_notify_score=7)
        real_analysis = PriceAnalysis(is_real_discount=True, lowest_price_days=30)
        fake_analysis = PriceAnalysis(is_real_discount=False, lowest_price_days=2)

        ok, reason = PushNotifier.should_notify_product(
            product, analysis=real_analysis, user_settings=settings
        )
        self.assertTrue(ok)
        self.assertEqual(reason, "eligible")

        ok_fake, _ = PushNotifier.should_notify_product(
            product, analysis=fake_analysis, user_settings=settings
        )
        self.assertFalse(ok_fake)

        ok_low_ai, _ = PushNotifier.should_notify_product(
            product, analysis=real_analysis, user_settings=settings, ai_score=5
        )
        self.assertFalse(ok_low_ai)

        ok_dup, _ = PushNotifier.should_notify_product(
            product,
            analysis=real_analysis,
            user_settings=settings,
            existing_product={"notified_at": "2026-01-01T00:00:00Z", "last_notified_price": 899.0},
        )
        self.assertFalse(ok_dup)

        # Further price drop should re-notify
        ok_drop, _ = PushNotifier.should_notify_product(
            product,
            analysis=real_analysis,
            user_settings=settings,
            existing_product={"notified_at": "2026-01-01T00:00:00Z", "last_notified_price": 1099.0},
        )
        self.assertTrue(ok_drop)

        disabled_settings = UserSettings(notifications_enabled=False)
        ok_disabled, _ = PushNotifier.should_notify_product(
            product, analysis=real_analysis, user_settings=disabled_settings
        )
        self.assertFalse(ok_disabled)

    def test_notify_product_handles_success_410_404_and_500(self) -> None:
        subscriptions = [
            {"id": "sub-ok", "endpoint": "https://push.example/ok", "p256dh": "k1", "auth": "a1", "is_active": True},
            {"id": "sub-gone", "endpoint": "https://push.example/gone", "p256dh": "k2", "auth": "a2", "is_active": True},
            {"id": "sub-missing", "endpoint": "https://push.example/missing", "p256dh": "k3", "auth": "a3", "is_active": True},
            {"id": "sub-err", "endpoint": "https://push.example/err", "p256dh": "k4", "auth": "a4", "is_active": True},
        ]
        db = FakeDatabaseClient(subscriptions)
        sent_calls: list[dict[str, Any]] = []

        def fake_webpush(**kwargs: Any) -> str:
            endpoint = kwargs["subscription_info"]["endpoint"]
            if endpoint.endswith("/gone"):
                raise WebPushException("Gone", response=SimpleNamespace(status_code=410))
            if endpoint.endswith("/missing"):
                raise WebPushException("Not Found", response=SimpleNamespace(status_code=404))
            if endpoint.endswith("/err"):
                raise WebPushException("Server Error", response=SimpleNamespace(status_code=500))
            sent_calls.append(kwargs)
            return "ok"

        notifier = PushNotifier(db, _make_settings(), sender=fake_webpush)  # type: ignore[arg-type]
        self.assertTrue(notifier.is_configured)

        product = _sample_product()
        report = notifier.notify_product(
            product,
            product_id="prod-1",
            matched_sizes=["M"],
            lowest_price_days=30,
        )

        self.assertEqual(report.attempted, 4)
        self.assertEqual(report.sent, 1)
        self.assertEqual(report.deactivated, 2)
        self.assertEqual(report.failed, 1)
        self.assertEqual(db.touched_ids, ["sub-ok"])
        self.assertEqual(db.deactivated_ids, ["sub-gone", "sub-missing"])
        self.assertEqual(db.marked_products, [("prod-1", Decimal("899.00"))])
        self.assertEqual(len(sent_calls), 1)
        self.assertIn("vapid_private_key", sent_calls[0])

    def test_unconfigured_placeholder_vapid_skips_gracefully(self) -> None:
        db = FakeDatabaseClient(
            [{"id": "sub-1", "endpoint": "https://push.example/1", "p256dh": "k1", "auth": "a1"}]
        )
        notifier = PushNotifier(
            db,  # type: ignore[arg-type]
            _make_settings(private_key="your-vapid-private-key", public_key="your-vapid-public-key"),
        )
        self.assertFalse(notifier.is_configured)
        report = notifier.notify_all(PushPayload(title="Test", body="Deneme"))
        self.assertEqual(report.attempted, 0)
        self.assertEqual(report.sent, 0)
        self.assertTrue(len(report.errors) > 0)


if __name__ == "__main__":
    unittest.main()
