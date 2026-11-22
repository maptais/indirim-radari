"""Web Push (VAPID) notification engine backed by `pywebpush` and Supabase."""

from __future__ import annotations

import argparse
import base64
import json
import logging
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives import serialization
from pydantic import BaseModel, Field
from py_vapid import Vapid02
from pywebpush import WebPushException, webpush

from .config import AppSettings, UserSettings, get_settings
from .db import DatabaseClient
from .price_analyzer import PriceAnalysis
from ..engines.base import ProductItem

logger = logging.getLogger(__name__)

BRAND_DISPLAY_NAMES: dict[str, str] = {
    "zara": "Zara",
    "pullandbear": "Pull&Bear",
    "bershka": "Bershka",
    "lefties": "Lefties",
    "massimodutti": "Massimo Dutti",
    "superstep": "SuperStep",
    "occasion": "Occasion",
    "fashfed": "FashFed",
    "mangoman": "Mango Man",
    "mangooutlet": "Mango Outlet",
    "trendyolmilla": "Trendyol Milla",
    "trendyolman": "Trendyol Man",
    "hm": "H&M",
    "defacto": "DeFacto",
    "mavi": "Mavi",
    "jackjones": "Jack & Jones",
    "instreet": "In Street",
    "sneaksup": "Sneaks Up",
    "barcin": "Barçın",
    "sportive": "Sportive",
    "adidasoutlet": "Adidas Outlet",
    "pumaoutlet": "Puma Outlet",
}

EXPIRED_HTTP_STATUS_CODES = {404, 410}
DEFAULT_REPRICE_TOLERANCE_TRY = Decimal("1.00")


def _is_placeholder(value: str | None) -> bool:
    if not value:
        return True
    cleaned = value.strip()
    if not cleaned:
        return True
    lowered = cleaned.lower()
    return lowered.startswith("your-") or lowered in {"changeme", "placeholder", "none"}


def format_try_price(amount: Decimal | float | int) -> str:
    """Format a TRY numeric amount in Turkish style (e.g. `1.299 TL` or `1.299,50 TL`)."""
    dec = Decimal(str(amount)).quantize(Decimal("0.01"))
    if dec == dec.to_integral_value():
        formatted = f"{int(dec):,}".replace(",", ".")
    else:
        int_part, frac_part = f"{dec:,.2f}".split(".")
        formatted = f"{int_part.replace(',', '.')},{frac_part}"
    return f"{formatted} TL"


def format_brand_name(brand_slug: str) -> str:
    """Convert canonical lowercase brand slug into display name."""
    cleaned = brand_slug.strip().lower()
    return BRAND_DISPLAY_NAMES.get(cleaned, brand_slug.strip().title())


class VapidKeyPair(BaseModel):
    """RFC 8292 URL-safe base64 VAPID keypair compatible with `pywebpush` and `web-push`."""

    public_key: str = Field(..., min_length=60)
    private_key: str = Field(..., min_length=32)


def generate_vapid_keys() -> VapidKeyPair:
    """Generate a fresh P-256 VAPID keypair in standard base64url raw format."""
    vapid = Vapid02()
    vapid.generate_keys()
    raw_private = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
    raw_public = vapid.public_key.public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    private_b64 = base64.urlsafe_b64encode(raw_private).rstrip(b"=").decode("ascii")
    public_b64 = base64.urlsafe_b64encode(raw_public).rstrip(b"=").decode("ascii")
    return VapidKeyPair(public_key=public_b64, private_key=private_b64)


def load_vapid_instance(private_key_value: str) -> Vapid02:
    """Load a `Vapid02` instance from a base64url raw string, PEM string, or PEM file path."""
    cleaned = private_key_value.strip()
    if "BEGIN" in cleaned:
        return Vapid02.from_pem(cleaned.encode("utf-8"))
    potential_path = Path(cleaned)
    if len(cleaned) < 260 and potential_path.is_file():
        return Vapid02.from_file(str(potential_path))
    return Vapid02.from_string(cleaned)


class PushPayload(BaseModel):
    """JSON payload delivered to `web/public/sw.js`."""

    title: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    url: str = Field(default="/")
    icon: str = Field(default="/icons/icon-192.png")
    badge: str = Field(default="/icons/icon-192.png")
    image: str | None = Field(default=None)
    tag: str = Field(default="indirim-radari-alert")
    product_id: str | None = Field(default=None)
    brand: str | None = Field(default=None)
    discount_percent: int | None = Field(default=None)

    def to_json(self) -> str:
        return json.dumps(self.model_dump(exclude_none=True), ensure_ascii=False)


class PushDeliveryReport(BaseModel):
    """Summary of sending a notification across active push subscriptions."""

    attempted: int = Field(default=0, ge=0)
    sent: int = Field(default=0, ge=0)
    deactivated: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    errors: list[str] = Field(default_factory=list)


class PushNotifier:
    """Sends Turkish Web Push alerts to active iOS/desktop PWA subscriptions."""

    def __init__(
        self,
        db: DatabaseClient,
        settings: AppSettings | None = None,
        *,
        sender: Callable[..., Any] = webpush,
        ttl_seconds: int = 12 * 3600,
    ) -> None:
        self._db = db
        self._settings = settings or get_settings()
        self._sender = sender
        self._ttl_seconds = max(60, ttl_seconds)
        self._vapid: Vapid02 | None = None

        raw_private = (
            self._settings.vapid_private_key.get_secret_value()
            if self._settings.vapid_private_key is not None
            else None
        )
        if not _is_placeholder(raw_private):
            assert raw_private is not None
            self._vapid = load_vapid_instance(raw_private)

        claims_sub = (self._settings.vapid_claims_sub or "").strip()
        self._claims_sub = (
            claims_sub
            if claims_sub.startswith(("mailto:", "https://"))
            else "mailto:indirim-radari@example.com"
        )

    @property
    def is_configured(self) -> bool:
        """Return True when a valid VAPID private key is loaded."""
        return self._vapid is not None

    @staticmethod
    def should_notify_product(
        product: ProductItem,
        *,
        analysis: PriceAnalysis,
        user_settings: UserSettings,
        existing_product: dict[str, Any] | None = None,
        ai_score: int | None = None,
        price_drop_tolerance_try: Decimal = DEFAULT_REPRICE_TOLERANCE_TRY,
    ) -> tuple[bool, str]:
        """Determine whether a scraped product should trigger a push notification."""
        if not user_settings.notifications_enabled:
            return False, "notifications_disabled_in_settings"

        if user_settings.target_genders and product.gender.value not in user_settings.target_genders:
            return False, f"gender_mismatch:{product.gender.value}"

        if not analysis.is_real_discount:
            return False, "not_real_discount"

        if ai_score is not None and ai_score < user_settings.min_ai_notify_score:
            return False, f"ai_score_below_threshold:{ai_score}<{user_settings.min_ai_notify_score}"

        if existing_product and existing_product.get("notified_at"):
            last_notified_raw = existing_product.get("last_notified_price")
            if last_notified_raw is not None:
                last_notified = Decimal(str(last_notified_raw))
                if product.effective_price >= (last_notified - price_drop_tolerance_try):
                    return False, "already_notified_at_same_or_lower_price"

        return True, "eligible"

    @staticmethod
    def build_product_payload(
        product: ProductItem,
        *,
        product_id: str | None = None,
        matched_sizes: list[str] | None = None,
        lowest_price_days: int | None = None,
        ai_score: int | None = None,
    ) -> PushPayload:
        """Build a Turkish Web Push notification payload for a discounted product."""
        brand_label = format_brand_name(product.brand)
        discount_pct = product.discount_percent or 0
        prefix = "Kritik Stok · " if product.is_critical_stock else ""
        title = f"{prefix}%{discount_pct} İndirim · {brand_label}"

        has_cart_price = (
            product.cart_price is not None
            and product.cart_price < product.current_price
        )
        if has_cart_price and product.cart_price is not None:
            ref_old = (
                product.original_price
                if (product.original_price is not None and product.original_price > product.cart_price)
                else product.current_price
            )
            price_part = (
                f"Sepette {format_try_price(product.cart_price)} (%{discount_pct} indirim"
                f", Eski: {format_try_price(ref_old)})"
            )
        else:
            price_part = format_try_price(product.current_price)
            if product.original_price is not None and product.original_price > product.current_price:
                price_part = f"{price_part} (Eski: {format_try_price(product.original_price)})"

        detail_segments: list[str] = [f"{product.title} — {price_part}"]

        if product.promo_badge and not has_cart_price:
            detail_segments.append(product.promo_badge)

        sizes_to_show = matched_sizes or product.available_sizes
        if sizes_to_show:
            sizes_preview = ", ".join(sizes_to_show[:4])
            if len(sizes_to_show) > 4:
                sizes_preview += f" +{len(sizes_to_show) - 4}"
            detail_segments.append(f"Beden: {sizes_preview}")

        if lowest_price_days is not None and lowest_price_days > 0:
            detail_segments.append(f"Son {lowest_price_days} günün en düşüğü")
        else:
            detail_segments.append("Gerçek indirim")

        if ai_score is not None:
            detail_segments.append(f"AI Tarz: {ai_score}/10")

        body = " · ".join(detail_segments)
        tag = f"product-{product.brand}-{product.external_id}"

        return PushPayload(
            title=title,
            body=body,
            url=product.url,
            image=product.image_url,
            tag=tag,
            product_id=product_id,
            brand=product.brand,
            discount_percent=discount_pct,
        )

    def send_to_subscription(
        self,
        subscription: dict[str, Any],
        payload: PushPayload,
    ) -> tuple[str, str | None]:
        """Send a payload to a single subscription record.

        Returns `(outcome, error_message)` where `outcome` is one of:
        `"sent"`, `"deactivated"`, `"failed"`.
        """
        if self._vapid is None:
            return "failed", "VAPID anahtarları yapılandırılmamış."

        sub_id = str(subscription["id"])
        subscription_info = {
            "endpoint": subscription["endpoint"],
            "keys": {
                "p256dh": subscription["p256dh"],
                "auth": subscription["auth"],
            },
        }

        try:
            self._sender(
                subscription_info=subscription_info,
                data=payload.to_json(),
                vapid_private_key=self._vapid,
                vapid_claims={"sub": self._claims_sub},
                ttl=self._ttl_seconds,
                timeout=self._settings.request_timeout_seconds,
            )
            self._db.touch_push_subscription_notified(sub_id)
            return "sent", None
        except WebPushException as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code in EXPIRED_HTTP_STATUS_CODES:
                logger.info(
                    "Push subscription %s returned HTTP %s; deactivating.",
                    sub_id,
                    status_code,
                )
                try:
                    self._db.deactivate_push_subscription(sub_id)
                except Exception as db_exc:  # noqa: BLE001
                    logger.warning("Failed to deactivate subscription %s: %s", sub_id, db_exc)
                return "deactivated", f"HTTP {status_code}: expired subscription deactivated"

            err_msg = f"WebPushException (HTTP {status_code}): {exc}"
            logger.warning("Push delivery failed for subscription %s: %s", sub_id, err_msg)
            return "failed", err_msg
        except Exception as exc:  # noqa: BLE001
            err_msg = f"{type(exc).__name__}: {exc}"
            logger.warning("Unexpected push error for subscription %s: %s", sub_id, err_msg)
            return "failed", err_msg

    def notify_all(self, payload: PushPayload) -> PushDeliveryReport:
        """Broadcast a push payload to all active subscriptions in Supabase."""
        report = PushDeliveryReport()
        if not self.is_configured:
            report.errors.append("VAPID_PRIVATE_KEY tanımlı değil veya geçersiz.")
            return report

        subscriptions = self._db.get_active_push_subscriptions()
        if not subscriptions:
            logger.info("No active push subscriptions found; skipping push delivery.")
            return report

        for subscription in subscriptions:
            report.attempted += 1
            outcome, error_message = self.send_to_subscription(subscription, payload)
            if outcome == "sent":
                report.sent += 1
            elif outcome == "deactivated":
                report.deactivated += 1
                if error_message:
                    report.errors.append(error_message)
            else:
                report.failed += 1
                if error_message:
                    report.errors.append(error_message)

        return report

    def notify_product(
        self,
        product: ProductItem,
        *,
        product_id: str,
        matched_sizes: list[str] | None = None,
        lowest_price_days: int | None = None,
        ai_score: int | None = None,
    ) -> PushDeliveryReport:
        """Send a Turkish deal alert for a product and mark it notified on success."""
        payload = self.build_product_payload(
            product,
            product_id=product_id,
            matched_sizes=matched_sizes,
            lowest_price_days=lowest_price_days,
            ai_score=ai_score,
        )
        report = self.notify_all(payload)
        if report.sent > 0:
            try:
                self._db.mark_product_notified(product_id, product.effective_price)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to mark product %s as notified: %s", product_id, exc)
        return report

    def send_test_notification(self, *, url: str = "/") -> PushDeliveryReport:
        """Send a Turkish test push notification to all active subscriptions."""
        payload = PushPayload(
            title="İndirim Radarı · Test Bildirimi",
            body="Web Push bağlantısı başarıyla çalışıyor. Gerçek indirimler yakalandığında anında haber vereceğiz.",
            url=url,
            tag="indirim-radari-test",
        )
        return self.notify_all(payload)


def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="İndirim Radarı Web Push & VAPID Aracı")
    parser.add_argument(
        "--generate-vapid",
        action="store_true",
        help="pywebpush ve web-push uyumlu yeni bir VAPID anahtar çifti üretir.",
    )
    parser.add_argument(
        "--test-push",
        action="store_true",
        help="Supabase'deki tüm aktif aboneliklere Türkçe test bildirimi gönderir.",
    )
    args = parser.parse_args(argv)

    if args.generate_vapid:
        pair = generate_vapid_keys()
        print("VAPID_PUBLIC_KEY=" + pair.public_key)
        print("VAPID_PRIVATE_KEY=" + pair.private_key)
        return 0

    if args.test_push:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
        settings = get_settings()
        db = DatabaseClient(settings)
        notifier = PushNotifier(db, settings)
        if not notifier.is_configured:
            print("Hata: scraper/.env içinde geçerli VAPID_PRIVATE_KEY tanımlı değil.")
            return 1
        report = notifier.send_test_notification()
        print(
            f"Test bildirimi tamamlandı | Denenen: {report.attempted} | "
            f"Gönderilen: {report.sent} | Pasifleştirilen: {report.deactivated} | Hatalı: {report.failed}"
        )
        if report.errors:
            for err in report.errors:
                print(f"  - {err}")
        return 0 if report.failed == 0 else 1

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
