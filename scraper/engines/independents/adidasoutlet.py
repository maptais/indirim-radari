"""Adidas Turkey Outlet (`adidas.com.tr`) scraper backed by Adidas Glass PLP & Availability APIs."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urljoin

from ..base import (
    DEFAULT_BRAND_TIME_BUDGET_SECONDS,
    BaseScraper,
    ProductCategory,
    ProductGender,
    ProductItem,
    ScrapeRunMode,
    ScraperBlockedError,
)
from ...core.http import (
    HttpClient,
    infer_product_category,
    infer_product_gender,
    is_kids_product,
    parse_try_price,
)

logger = logging.getLogger(__name__)

_CRITICAL_STOCK_THRESHOLD = 3
_KIDS_MARKERS = ("çocuk", "cocuk", "kids", "junior", "bebek", "infant", "toddler")

ADIDAS_OUTLET_QUERIES: dict[str, tuple[str, ...]] = {
    "all": ("erkek-outlet-ayakkabi", "kadin-outlet-ayakkabi", "outlet"),
    "men": ("erkek-outlet-ayakkabi", "erkek-outlet"),
    "women": ("kadin-outlet-ayakkabi", "kadin-outlet"),
}


class AdidasOutletScraper(BaseScraper):
    """Scraper for Adidas TR Outlet (`www.adidas.com.tr`).

    Uses the Adidas Glass PLP (`api/plp/content-engine`) and per-product availability
    (`api/products/{id}/availability`) JSON endpoints. Because Adidas TR is protected by
    Akamai Bot Manager (`sec-if-cpt-container` / HTTP 403), blocked runs raise
    `ScraperBlockedError` cleanly so `scrape_logs` records `blocked` without disrupting
    other brands.
    """

    brand = "adidasoutlet"
    engine_name = "adidasoutlet"
    default_run_mode = ScrapeRunMode.LOCAL

    def __init__(
        self,
        *,
        section: Literal["all", "men", "women"] = "all",
        only_discounted: bool = True,
        max_categories: int = 3,
        max_pages_per_category: int = 8,
        page_size: int = 48,
        max_detail_fetches: int = 28,
        fetch_availability: bool = True,
        max_retries: int = 1,
        impersonate: str | None = None,
        timeout_seconds: int = 15,
        min_jitter_seconds: float = 0.4,
        max_jitter_seconds: float = 1.0,
        max_duration_seconds: float | None = DEFAULT_BRAND_TIME_BUDGET_SECONDS,
    ) -> None:
        super().__init__(
            timeout_seconds=timeout_seconds,
            min_jitter_seconds=min_jitter_seconds,
            max_jitter_seconds=max_jitter_seconds,
            max_duration_seconds=max_duration_seconds,
        )
        self.section = section
        self.only_discounted = only_discounted
        self.max_categories = max(1, max_categories)
        self.max_pages_per_category = max(1, max_pages_per_category)
        self.page_size = max(12, min(48, page_size))
        self.max_detail_fetches = max(0, max_detail_fetches)
        self.fetch_availability = fetch_availability

        self.base_url = "https://www.adidas.com.tr"
        self.http = HttpClient(
            self.brand,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            impersonate=impersonate,
            default_headers={
                "Referer": f"{self.base_url}/tr/outlet",
                "Origin": self.base_url,
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-origin",
            },
        )

    def close(self) -> None:
        self.http.close()

    @staticmethod
    def _infer_gender(raw_item: dict[str, Any], query_slug: str) -> ProductGender:
        return infer_product_gender(
            str(raw_item.get("gender") or ""),
            str(raw_item.get("subTitle") or ""),
            str(raw_item.get("displayName") or ""),
            str(raw_item.get("category") or ""),
            query_slug,
            default=ProductGender.UNISEX,
        )

    def _matches_section(self, raw_item: dict[str, Any], query_slug: str) -> bool:
        if is_kids_product(
            str(raw_item.get("displayName") or ""),
            str(raw_item.get("subTitle") or ""),
            str(raw_item.get("category") or ""),
            str(raw_item.get("gender") or ""),
            query_slug,
        ):
            return False
        if self.section == "all":
            return True
        gender = self._infer_gender(raw_item, query_slug)
        if gender == ProductGender.UNISEX:
            return True
        if self.section == "men":
            return gender == ProductGender.MEN
        if self.section == "women":
            return gender == ProductGender.WOMEN
        return True

    @staticmethod
    def parse_availability_payload(payload: dict[str, Any]) -> tuple[list[str], bool]:
        """Extract in-stock EU sizes and critical stock flag from Adidas availability JSON."""
        available_sizes: list[str] = []
        is_critical_stock = False

        for var in payload.get("variation_list") or []:
            if not isinstance(var, dict):
                continue
            status = str(var.get("availability_status") or "").strip().upper()
            avail_qty = var.get("availability")
            qty_int: int | None = None
            if avail_qty is not None:
                try:
                    qty_int = int(avail_qty)
                except (TypeError, ValueError):
                    qty_int = None

            in_stock = status in {"IN_STOCK", "PREVIEW", "LOW_STOCK"} or (qty_int is not None and qty_int > 0)
            if not in_stock or (qty_int is not None and qty_int <= 0):
                continue

            if status == "LOW_STOCK" or (qty_int is not None and 0 < qty_int <= _CRITICAL_STOCK_THRESHOLD):
                is_critical_stock = True

            size_label = str(var.get("size") or "").strip()
            if size_label:
                available_sizes.append(size_label)

        return available_sizes, is_critical_stock

    def parse_plp_item(
        self,
        raw_item: dict[str, Any],
        *,
        query_slug: str = "",
        availability_payload: dict[str, Any] | None = None,
    ) -> ProductItem | None:
        """Convert an Adidas Glass PLP item (+ optional availability payload) to `ProductItem`."""
        product_id = str(raw_item.get("productId") or raw_item.get("modelId") or "").strip()
        title = str(raw_item.get("displayName") or raw_item.get("title") or "").strip()
        link = str(raw_item.get("link") or "").strip()
        if not product_id or not title or not link:
            return None

        sale_price = parse_try_price(raw_item.get("salePrice"))
        base_price = parse_try_price(raw_item.get("price"))
        current_price = sale_price or base_price
        if current_price is None:
            return None

        original_price: Decimal | None = None
        if base_price is not None and base_price > current_price:
            original_price = base_price

        if self.only_discounted and original_price is None:
            return None

        image_url: str | None = None
        image_obj = raw_item.get("image")
        if isinstance(image_obj, dict):
            src = image_obj.get("src")
            if isinstance(src, str) and src.startswith("http"):
                image_url = src
        elif isinstance(image_obj, str) and image_obj.startswith("http"):
            image_url = image_obj

        available_sizes: list[str] = []
        is_critical_stock = False
        if availability_payload is not None:
            available_sizes, is_critical_stock = self.parse_availability_payload(availability_payload)
        else:
            raw_sizes = raw_item.get("availableSizes") or raw_item.get("sizes") or []
            if isinstance(raw_sizes, list):
                available_sizes = [str(s).strip() for s in raw_sizes if str(s).strip()]

        inferred_cat = infer_product_category(
            title,
            str(raw_item.get("subTitle") or ""),
            str(raw_item.get("category") or ""),
            str(raw_item.get("division") or ""),
            query_slug,
            default=ProductCategory.SHOES,
        )

        inferred_gender = self._infer_gender(raw_item, query_slug)

        return ProductItem(
            brand=self.brand,
            external_id=product_id,
            title=title,
            category=inferred_cat,
            gender=inferred_gender,
            url=urljoin(f"{self.base_url}/", link),
            image_url=image_url,
            current_price=current_price,
            original_price=original_price,
            currency="TRY",
            available_sizes=available_sizes,
            is_critical_stock=is_critical_stock,
            raw_metadata={
                "model_id": raw_item.get("modelId"),
                "sub_title": raw_item.get("subTitle"),
                "query_slug": query_slug,
            },
        )

    @staticmethod
    def extract_plp_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract product item dicts from Adidas Glass PLP content-engine payload."""
        raw = payload.get("raw") if isinstance(payload.get("raw"), dict) else payload
        item_list = raw.get("itemList") if isinstance(raw, dict) else None
        if isinstance(item_list, dict):
            items = item_list.get("items") or []
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
        items = payload.get("items") or []
        return [item for item in items if isinstance(item, dict)]

    def fetch_products(self) -> list[ProductItem]:
        """Fetch discounted products from Adidas TR Outlet PLP API."""
        queries = ADIDAS_OUTLET_QUERIES.get(self.section) or ADIDAS_OUTLET_QUERIES["all"]
        selected_queries = list(queries[: self.max_categories])

        candidates: list[tuple[dict[str, Any], str]] = []
        seen_ids: set[str] = set()
        query_failures = 0

        for q_idx, query_slug in enumerate(selected_queries):
            if self.is_budget_exceeded(reserve_seconds=25.0):
                break
            if q_idx > 0:
                self.sleep_jitter()

            q_failed = False
            consecutive_empty_pages = 0
            for page_idx in range(self.max_pages_per_category):
                if self.is_budget_exceeded(reserve_seconds=25.0):
                    break
                if page_idx > 0:
                    self.sleep_jitter()

                start = page_idx * self.page_size
                url = f"{self.base_url}/api/plp/content-engine"
                params = {
                    "sitePath": "tr",
                    "query": query_slug,
                    "start": start,
                }
                try:
                    payload = self.http.get_json(url, params=params)
                except ScraperBlockedError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] PLP error on %s start=%d: %s", self.brand, query_slug, start, exc)
                    if page_idx == 0:
                        q_failed = True
                    break

                raw_items = self.extract_plp_items(payload)
                if not raw_items:
                    break

                new_in_page = 0
                for raw_item in raw_items:
                    pid = str(raw_item.get("productId") or raw_item.get("modelId") or "").strip()
                    if not pid or pid in seen_ids:
                        continue
                    if not self._matches_section(raw_item, query_slug):
                        continue
                    sale_p = parse_try_price(raw_item.get("salePrice"))
                    base_p = parse_try_price(raw_item.get("price"))
                    if self.only_discounted and (sale_p is None or base_p is None or base_p <= sale_p):
                        continue
                    seen_ids.add(pid)
                    candidates.append((raw_item, query_slug))
                    new_in_page += 1

                if new_in_page == 0:
                    consecutive_empty_pages += 1
                    if consecutive_empty_pages >= 2:
                        break
                else:
                    consecutive_empty_pages = 0

                if len(raw_items) < self.page_size:
                    break

            if q_failed:
                query_failures += 1

        if query_failures == len(selected_queries) and not candidates:
            raise RuntimeError(f"All {len(selected_queries)} Adidas Outlet queries failed")

        products: list[ProductItem] = []
        detail_count = 0
        detail_timeout = min(self.timeout_seconds, 10)
        for raw_item, query_slug in candidates:
            avail_payload: dict[str, Any] | None = None
            pid = str(raw_item.get("productId") or "").strip()
            if (
                self.fetch_availability
                and pid
                and detail_count < self.max_detail_fetches
                and not self.is_budget_exceeded()
            ):
                if detail_count > 0:
                    self.sleep_jitter(scale=0.6)
                try:
                    avail_payload = self.http.get_json(
                        f"{self.base_url}/api/products/{pid}/availability",
                        timeout_seconds=detail_timeout,
                        max_retries=1,
                    )
                    detail_count += 1
                except ScraperBlockedError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[%s] Availability fetch failed for %s: %s", self.brand, pid, exc)

            item = self.parse_plp_item(
                raw_item,
                query_slug=query_slug,
                availability_payload=avail_payload,
            )
            if item is not None:
                products.append(item)

        return products
