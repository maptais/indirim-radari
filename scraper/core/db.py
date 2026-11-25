"""Supabase PostgreSQL database access layer for the scraper pipeline."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from supabase import Client, create_client

from .config import AppSettings, UserSettings, get_settings
from ..engines.base import ProductItem, ScrapeRunMode, ScrapeStatus


class DatabaseClient:
    """Typed repository wrapper around the Supabase client."""

    def __init__(self, settings: AppSettings | None = None) -> None:
        self._settings = settings or get_settings()
        self._client: Client = create_client(
            self._settings.supabase_url,
            self._settings.supabase_service_role_key.get_secret_value(),
        )

    @property
    def client(self) -> Client:
        """Expose underlying Supabase client for advanced queries if needed."""
        return self._client

    # ------------------------------------------------------------------
    # user_settings
    # ------------------------------------------------------------------
    def get_user_settings(self) -> UserSettings:
        """Fetch the singleton user settings row (`id = 1`)."""
        response = (
            self._client.table("user_settings")
            .select("*")
            .eq("id", 1)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise RuntimeError(
                "No row found in `public.user_settings` (id=1). "
                "Please run `supabase/schema.sql` including the default seed."
            )
        return UserSettings.model_validate(rows[0])

    # ------------------------------------------------------------------
    # products
    # ------------------------------------------------------------------
    def get_products_by_external_ids(
        self,
        brand: str,
        external_ids: list[str],
        *,
        chunk_size: int = 150,
    ) -> dict[str, dict[str, Any]]:
        """Batch-fetch existing products for a brand keyed by `external_id`."""
        brand_slug = brand.strip().lower()
        unique_ids = list(dict.fromkeys(eid.strip() for eid in external_ids if eid and eid.strip()))
        if not brand_slug or not unique_ids:
            return {}

        step = max(1, min(200, chunk_size))
        found: dict[str, dict[str, Any]] = {}
        for offset in range(0, len(unique_ids), step):
            chunk = unique_ids[offset : offset + step]
            response = (
                self._client.table("products")
                .select("*")
                .eq("brand", brand_slug)
                .in_("external_id", chunk)
                .execute()
            )
            for row in response.data or []:
                ext_id = str(row.get("external_id") or "").strip()
                if ext_id:
                    found[ext_id] = row
        return found

    def upsert_products_batch(
        self,
        payloads: list[dict[str, Any]],
        *,
        chunk_size: int = 150,
    ) -> list[dict[str, Any]]:
        """Batch upsert serialized `products` payloads in 100-200 row chunks.

        Payloads are grouped by key-set before sending so optional columns such as
        `ai_score`/`ai_comment` are never padded with `NULL` by PostgREST when absent.
        """
        if not payloads:
            return []

        now_iso = datetime.now(timezone.utc).isoformat()
        # Deduplicate on (brand, external_id) keeping latest payload so ON CONFLICT never collides
        deduped: dict[tuple[str, str], dict[str, Any]] = {}
        for raw in payloads:
            row = dict(raw)
            row.setdefault("last_seen_at", now_iso)
            key = (
                str(row.get("brand") or "").strip().lower(),
                str(row.get("external_id") or "").strip(),
            )
            if not key[0] or not key[1]:
                continue
            deduped[key] = row

        if not deduped:
            return []

        # Partition by identical column sets to avoid NULL-overwriting omitted AI fields
        buckets: dict[frozenset[str], list[dict[str, Any]]] = {}
        for row in deduped.values():
            buckets.setdefault(frozenset(row.keys()), []).append(row)

        step = max(1, min(200, chunk_size))
        saved_rows: list[dict[str, Any]] = []
        for rows_group in buckets.values():
            for offset in range(0, len(rows_group), step):
                chunk = rows_group[offset : offset + step]
                response = (
                    self._client.table("products")
                    .upsert(chunk, on_conflict="brand,external_id")
                    .execute()
                )
                saved_rows.extend(response.data or [])
        return saved_rows

    def get_product_by_external_id(self, brand: str, external_id: str) -> dict[str, Any] | None:
        """Look up an existing product by `(brand, external_id)`."""
        response = (
            self._client.table("products")
            .select("*")
            .eq("brand", brand.strip().lower())
            .eq("external_id", external_id.strip())
            .limit(1)
            .execute()
        )
        rows = response.data or []
        return rows[0] if rows else None

    def upsert_product(
        self,
        product: ProductItem,
        *,
        is_real_discount: bool = False,
        lowest_price_days: int | None = None,
        historical_min_price: Decimal | None = None,
        ai_score: int | None = None,
        ai_comment: str | None = None,
    ) -> dict[str, Any]:
        """Insert or update a product keyed on `(brand, external_id)`."""
        payload = product.to_db_payload(
            is_real_discount=is_real_discount,
            lowest_price_days=lowest_price_days,
            historical_min_price=historical_min_price,
            ai_score=ai_score,
            ai_comment=ai_comment,
        )
        payload["last_seen_at"] = datetime.now(timezone.utc).isoformat()

        response = (
            self._client.table("products")
            .upsert(payload, on_conflict="brand,external_id")
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise RuntimeError(
                f"Failed to upsert product ({product.brand}, {product.external_id})."
            )
        return rows[0]

    def mark_product_notified(self, product_id: str, notified_price: Decimal) -> None:
        """Record notification timestamp and price to prevent duplicate push alerts."""
        (
            self._client.table("products")
            .update(
                {
                    "notified_at": datetime.now(timezone.utc).isoformat(),
                    "last_notified_price": float(notified_price),
                }
            )
            .eq("id", product_id)
            .execute()
        )

    def cleanup_stale_products(
        self,
        brands: list[str],
        *,
        max_age_hours: int = 72,
    ) -> int:
        """Delete products not seen within `max_age_hours` for the given succeeded brands.

        Scoped strictly to brands successfully scraped by the current job so parallel
        GitHub Actions matrix workers never conflict or wipe failed/blocked brands.
        Linked `price_history` rows cascade-delete via FK to protect Supabase 500 MB quota.
        """
        normalized_brands = sorted({b.strip().lower() for b in brands if b and b.strip()})
        if not normalized_brands:
            return 0

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=max(1, max_age_hours))).isoformat()
        response = (
            self._client.table("products")
            .delete()
            .in_("brand", normalized_brands)
            .lt("last_seen_at", cutoff)
            .execute()
        )
        rows = response.data or []
        return len(rows)

    # ------------------------------------------------------------------
    # price_history
    # ------------------------------------------------------------------
    def record_price_history_batch(
        self,
        records: list[dict[str, Any]],
        *,
        chunk_size: int = 150,
    ) -> int:
        """Batch insert `price_history` rows in 100-200 row chunks."""
        if not records:
            return 0

        step = max(1, min(200, chunk_size))
        inserted = 0
        for offset in range(0, len(records), step):
            chunk = records[offset : offset + step]
            response = self._client.table("price_history").insert(chunk).execute()
            rows = response.data or []
            inserted += len(rows) if rows else len(chunk)
        return inserted

    def get_price_history_bulk(
        self,
        product_ids: list[str],
        *,
        days: int = 60,
        chunk_size: int = 50,
    ) -> dict[str, list[dict[str, Any]]]:
        """Batch-fetch `price_history` snapshots grouped by `product_id`."""
        unique_ids = list(dict.fromkeys(pid.strip() for pid in product_ids if pid and pid.strip()))
        if not unique_ids:
            return {}

        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        step = max(1, min(150, chunk_size))
        page_size = 1000
        grouped: dict[str, list[dict[str, Any]]] = {}

        for offset in range(0, len(unique_ids), step):
            chunk = unique_ids[offset : offset + step]
            range_start = 0
            while True:
                response = (
                    self._client.table("price_history")
                    .select("id, product_id, price, original_price, discount_percent, recorded_at")
                    .in_("product_id", chunk)
                    .gte("recorded_at", since)
                    .order("recorded_at", desc=True)
                    .range(range_start, range_start + page_size - 1)
                    .execute()
                )
                rows = response.data or []
                for row in rows:
                    pid = str(row.get("product_id") or "").strip()
                    if pid:
                        grouped.setdefault(pid, []).append(row)
                if len(rows) < page_size:
                    break
                range_start += page_size

        return grouped

    def record_price_history(
        self,
        product_id: str,
        price: Decimal,
        original_price: Decimal | None = None,
        discount_percent: int | None = None,
    ) -> dict[str, Any]:
        """Append a price snapshot for fake-discount detection."""
        payload: dict[str, Any] = {
            "product_id": product_id,
            "price": float(price),
            "original_price": float(original_price) if original_price is not None else None,
            "discount_percent": discount_percent,
        }
        response = self._client.table("price_history").insert(payload).execute()
        rows = response.data or []
        if not rows:
            raise RuntimeError(f"Failed to insert price_history for product_id={product_id}.")
        return rows[0]

    def get_price_history(self, product_id: str, days: int = 60) -> list[dict[str, Any]]:
        """Retrieve price snapshots for a product over the last `days` days."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        response = (
            self._client.table("price_history")
            .select("id, price, original_price, discount_percent, recorded_at")
            .eq("product_id", product_id)
            .gte("recorded_at", since)
            .order("recorded_at", desc=True)
            .execute()
        )
        return response.data or []

    # ------------------------------------------------------------------
    # push_subscriptions
    # ------------------------------------------------------------------
    def get_active_push_subscriptions(self) -> list[dict[str, Any]]:
        """Fetch all active Web Push subscriptions."""
        response = (
            self._client.table("push_subscriptions")
            .select("id, endpoint, p256dh, auth, device_label")
            .eq("is_active", True)
            .execute()
        )
        return response.data or []

    def deactivate_push_subscription(self, subscription_id: str) -> None:
        """Mark an expired/410 push subscription inactive."""
        (
            self._client.table("push_subscriptions")
            .update({"is_active": False})
            .eq("id", subscription_id)
            .execute()
        )

    def touch_push_subscription_notified(self, subscription_id: str) -> None:
        """Update `last_notified_at` after a successful push send."""
        (
            self._client.table("push_subscriptions")
            .update({"last_notified_at": datetime.now(timezone.utc).isoformat()})
            .eq("id", subscription_id)
            .execute()
        )

    # ------------------------------------------------------------------
    # scrape_logs
    # ------------------------------------------------------------------
    def start_scrape_log(
        self,
        brand: str,
        engine: str,
        run_mode: ScrapeRunMode | str = ScrapeRunMode.CLOUD,
    ) -> int:
        """Create a `running` scrape log entry and return its numeric ID."""
        mode_value = run_mode.value if isinstance(run_mode, ScrapeRunMode) else run_mode
        response = (
            self._client.table("scrape_logs")
            .insert(
                {
                    "brand": brand.strip().lower(),
                    "engine": engine.strip().lower(),
                    "run_mode": mode_value,
                    "status": ScrapeStatus.RUNNING.value,
                }
            )
            .execute()
        )
        rows = response.data or []
        if not rows:
            raise RuntimeError(f"Failed to start scrape_log for brand={brand}.")
        return int(rows[0]["id"])

    def finish_scrape_log(
        self,
        log_id: int,
        *,
        status: ScrapeStatus | str,
        fetched_count: int = 0,
        matched_count: int = 0,
        saved_count: int = 0,
        notified_count: int = 0,
        duration_ms: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Finalize an existing scrape log entry with metrics and optional error."""
        status_value = status.value if isinstance(status, ScrapeStatus) else status
        (
            self._client.table("scrape_logs")
            .update(
                {
                    "status": status_value,
                    "fetched_count": fetched_count,
                    "matched_count": matched_count,
                    "saved_count": saved_count,
                    "notified_count": notified_count,
                    "duration_ms": duration_ms,
                    "error_message": error_message,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            .eq("id", log_id)
            .execute()
        )
