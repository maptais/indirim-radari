"""Price history analysis and fake-discount detector backed by Supabase `price_history`."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from pydantic import BaseModel, Field

from .db import DatabaseClient
from ..engines.base import ProductItem


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        dt = datetime.fromisoformat(normalized)
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)


class PriceSnapshot(BaseModel):
    """Normalized record from `public.price_history`."""

    price: Decimal
    original_price: Decimal | None = None
    discount_percent: int | None = None
    recorded_at: datetime


class PriceAnalysis(BaseModel):
    """Result of evaluating a product's current price against its historical prices."""

    is_real_discount: bool = Field(default=False)
    lowest_price_days: int | None = Field(default=None, ge=0)
    historical_min_price: Decimal | None = Field(default=None, ge=Decimal("0"))
    history_count: int = Field(default=0, ge=0)
    should_record_history: bool = Field(default=True)
    reason: str = Field(default="")


class PriceAnalyzer:
    """Compares current product prices against `price_history` to detect real vs fake discounts."""

    def __init__(
        self,
        db: DatabaseClient | None = None,
        *,
        lookback_days: int = 45,
        min_snapshot_interval_hours: int = 12,
        price_tolerance_try: Decimal = Decimal("1.00"),
    ) -> None:
        self._db = db
        self.lookback_days = max(7, lookback_days)
        self.min_snapshot_interval = timedelta(hours=max(1, min_snapshot_interval_hours))
        self.price_tolerance_try = price_tolerance_try

    def analyze_from_history(
        self,
        product: ProductItem,
        history_rows: list[dict[str, Any]],
        *,
        now: datetime | None = None,
    ) -> PriceAnalysis:
        """Compute `is_real_discount` and `lowest_price_days` from raw history rows."""
        reference_now = now or datetime.now(timezone.utc)
        cutoff = reference_now - timedelta(days=self.lookback_days)

        snapshots: list[PriceSnapshot] = []
        for row in history_rows:
            price = _to_decimal(row.get("price"))
            if price is None:
                continue
            recorded_at = _parse_timestamp(row.get("recorded_at"))
            if recorded_at < cutoff:
                continue
            snapshots.append(
                PriceSnapshot(
                    price=price,
                    original_price=_to_decimal(row.get("original_price")),
                    discount_percent=row.get("discount_percent"),
                    recorded_at=recorded_at,
                )
            )

        # Newest first
        snapshots.sort(key=lambda s: s.recorded_at, reverse=True)
        current_price = product.effective_price
        discount_pct = product.discount_percent or 0
        has_store_markdown = (
            (
                (product.original_price is not None and product.original_price > current_price)
                or (product.cart_price is not None and product.cart_price < product.current_price)
            )
            and discount_pct > 0
        )

        # Case 1: Brand new product with no price history yet
        if not snapshots:
            return PriceAnalysis(
                is_real_discount=has_store_markdown,
                lowest_price_days=0 if has_store_markdown else None,
                historical_min_price=current_price,
                history_count=0,
                should_record_history=True,
                reason=(
                    "first_seen_with_store_discount"
                    if has_store_markdown
                    else "first_seen_no_discount"
                ),
            )

        latest = snapshots[0]
        oldest = snapshots[-1]
        prior_prices = [s.price for s in snapshots]
        prior_min = min(prior_prices)
        historical_min = min(prior_min, current_price)

        # Decide whether we should append a new price_history row
        price_changed = (
            latest.price != current_price
            or latest.original_price != product.original_price
        )
        interval_elapsed = (reference_now - latest.recorded_at) >= self.min_snapshot_interval
        should_record = price_changed or interval_elapsed

        # Find the most recent snapshot that was strictly cheaper (beyond tolerance)
        most_recent_cheaper: PriceSnapshot | None = None
        for snap in snapshots:
            if snap.price < (current_price - self.price_tolerance_try):
                most_recent_cheaper = snap
                break

        # Detect artificially inflated original_price:
        # Store previously sold the item at <= current_price WITHOUT any discount,
        # and now claims a discount while keeping the selling price unchanged/higher.
        was_sold_regularly_at_same_or_lower = any(
            (snap.price <= current_price + self.price_tolerance_try)
            and (snap.original_price is None or snap.original_price <= snap.price)
            and ((snap.discount_percent or 0) == 0)
            for snap in snapshots
        )

        # Case 2: There was a cheaper price within the lookback window -> NOT a real lowest discount
        if most_recent_cheaper is not None:
            days_since_cheaper = max(0, (reference_now - most_recent_cheaper.recorded_at).days)
            return PriceAnalysis(
                is_real_discount=False,
                lowest_price_days=days_since_cheaper,
                historical_min_price=historical_min,
                history_count=len(snapshots),
                should_record_history=should_record,
                reason=f"cheaper_price_seen_recently:{most_recent_cheaper.price}",
            )

        # Case 3: Current price is at or below the historical minimum in the lookback window
        span_days = min(self.lookback_days, max(0, (reference_now - oldest.recorded_at).days))

        if was_sold_regularly_at_same_or_lower and current_price >= (prior_min - self.price_tolerance_try):
            return PriceAnalysis(
                is_real_discount=False,
                lowest_price_days=span_days,
                historical_min_price=historical_min,
                history_count=len(snapshots),
                should_record_history=should_record,
                reason="inflated_original_price_suspected",
            )

        # Strictly lower than prior minimum -> genuine price drop even if store hides original_price
        dropped_below_prior_min = current_price < (prior_min - self.price_tolerance_try)
        is_real = has_store_markdown or dropped_below_prior_min

        reason = (
            "new_historical_low"
            if dropped_below_prior_min
            else ("at_historical_low_with_discount" if is_real else "no_discount")
        )

        return PriceAnalysis(
            is_real_discount=is_real,
            lowest_price_days=span_days if is_real else None,
            historical_min_price=historical_min,
            history_count=len(snapshots),
            should_record_history=should_record,
            reason=reason,
        )

    def analyze_product(
        self,
        product: ProductItem,
        *,
        existing_product_id: str | None = None,
    ) -> tuple[PriceAnalysis, str | None]:
        """Fetch product price history from Supabase (if available) and analyze it.

        Returns `(PriceAnalysis, existing_product_id)`.
        """
        if self._db is None:
            return self.analyze_from_history(product, []), existing_product_id

        product_id = existing_product_id
        if product_id is None:
            existing = self._db.get_product_by_external_id(product.brand, product.external_id)
            if existing:
                product_id = str(existing["id"])

        if product_id is None:
            return self.analyze_from_history(product, []), None

        history_rows = self._db.get_price_history(product_id, days=self.lookback_days)
        analysis = self.analyze_from_history(product, history_rows)
        return analysis, product_id
