"""In Street (`instreet.com.tr`) shoe & sportswear discount scraper."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urljoin

from ..base import (
    BaseScraper,
    ProductCategory,
    ProductItem,
    ScrapeRunMode,
    ScraperBlockedError,
)
from ...core.filters import turkish_lower
from ...core.http import HttpClient, infer_product_category, parse_try_price

logger = logging.getLogger(__name__)

_CRITICAL_STOCK_THRESHOLD = 3
_KIDS_MARKERS = ("çocuk", "cocuk", "bebek", "baby", "junior", "kids", "genç", "genc", "(ps)", "(td)", "(gs)")


class InStreetScraper(BaseScraper):
    """Standalone scraper for In Street (`www.instreet.com.tr`) using its JSON listing webservice."""

    brand = "instreet"
    engine_name = "instreet"
    default_run_mode = ScrapeRunMode.CLOUD

    def __init__(
        self,
        *,
        section: Literal["all", "men", "women"] = "all",
        only_discounted: bool = True,
        max_categories: int = 4,
        max_pages: int = 5,
        max_retries: int = 2,
        impersonate: str | None = None,
        timeout_seconds: int = 20,
        min_jitter_seconds: float = 0.6,
        max_jitter_seconds: float = 1.5,
    ) -> None:
        super().__init__(
            timeout_seconds=timeout_seconds,
            min_jitter_seconds=min_jitter_seconds,
            max_jitter_seconds=max_jitter_seconds,
        )
        self.section = section
        self.only_discounted = only_discounted
        self.max_categories = max(1, max_categories)
        self.max_pages = max(1, max_pages)

        self.base_url = "https://www.instreet.com.tr"
        self.http = HttpClient(
            self.brand,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            impersonate=impersonate,
            default_headers={
                "Referer": f"{self.base_url}/indirim",
                "Origin": self.base_url,
            },
        )

    def close(self) -> None:
        self.http.close()

    def _matches_section(self, raw: dict[str, Any], full_title: str) -> bool:
        breadcrumb_names = " ".join(
            str(b.get("name") or "")
            for b in (raw.get("breadcrumb") or [])
            if isinstance(b, dict)
        )
        combined = turkish_lower(f"{full_title} {breadcrumb_names} {raw.get('relativeUrl') or ''}")
        if any(k in combined for k in _KIDS_MARKERS):
            return False
        if self.section == "all":
            return True
        if "unisex" in combined:
            return True
        is_men = "erkek" in combined
        is_women = "kadın" in combined or "kadin" in combined
        if self.section == "men":
            return is_men or not is_women
        if self.section == "women":
            return is_women or not is_men
        return True

    def parse_product(self, raw: dict[str, Any]) -> ProductItem | None:
        """Normalize a single In Street webservice product object into `ProductItem`."""
        if raw.get("isInStock") is False:
            return None

        brand_name = str(raw.get("brandName") or "").strip()
        raw_name = str(raw.get("name") or "").strip()
        if not raw_name:
            return None

        if brand_name and turkish_lower(brand_name) not in turkish_lower(raw_name):
            full_title = f"{brand_name} {raw_name}"
        else:
            full_title = raw_name

        if not self._matches_section(raw, full_title):
            return None

        current_price = parse_try_price(raw.get("price"))
        original_price = parse_try_price(raw.get("oldPrice"))
        if current_price is None:
            return None
        if original_price is not None and original_price <= current_price:
            original_price = None

        if self.only_discounted and original_price is None:
            return None

        relative_url = str(raw.get("relativeUrl") or "").strip()
        if not relative_url:
            return None
        product_url = urljoin(f"{self.base_url}/", relative_url)

        external_id = str(raw.get("sku") or raw.get("productId") or "").strip()
        if not external_id:
            return None

        available_sizes: list[str] = []
        is_critical_stock = False
        for opt in raw.get("options") or []:
            if not isinstance(opt, dict):
                continue
            in_stock = bool(opt.get("is_in_stock"))
            qty_raw = opt.get("qty")
            qty_int: int | None = None
            if qty_raw is not None:
                try:
                    qty_int = int(qty_raw)
                except (TypeError, ValueError):
                    qty_int = None

            if not in_stock or (qty_int is not None and qty_int <= 0):
                continue

            if qty_int is not None and 0 < qty_int <= _CRITICAL_STOCK_THRESHOLD:
                is_critical_stock = True

            size_val = str(opt.get("option_value") or "").strip()
            if size_val:
                available_sizes.append(size_val)

        if not available_sizes:
            return None

        image_url = raw.get("imageUrl") or raw.get("imageUrlVertical")
        if not isinstance(image_url, str) or not image_url.startswith("http"):
            image_url = None

        breadcrumbs = [
            str(b.get("name") or "")
            for b in (raw.get("breadcrumb") or [])
            if isinstance(b, dict)
        ]
        inferred_cat = infer_product_category(
            full_title,
            " ".join(breadcrumbs),
            default=ProductCategory.SHOES,
        )

        return ProductItem(
            brand=self.brand,
            external_id=external_id,
            title=full_title,
            category=inferred_cat,
            url=product_url,
            image_url=image_url,
            current_price=current_price,
            original_price=original_price,
            currency="TRY",
            available_sizes=available_sizes,
            is_critical_stock=is_critical_stock,
            raw_metadata={
                "product_id": raw.get("productId"),
                "brand_name": brand_name,
                "breadcrumbs": breadcrumbs,
                "discount_rate_label": raw.get("discountRate"),
            },
        )

    def fetch_products(self) -> list[ProductItem]:
        """Fetch discounted products with live size stock from In Street's JSON list API."""
        products_by_id: dict[str, ProductItem] = {}
        pages_to_scan = min(self.max_pages, max(2, self.max_categories))

        gender_param: str | None = None
        if self.section == "men":
            gender_param = "erkek"
        elif self.section == "women":
            gender_param = "kadin"

        page_failures = 0
        for page in range(1, pages_to_scan + 1):
            if page > 1:
                self.sleep_jitter()

            params: dict[str, Any] = {
                "uri": "indirim",
                "newCategoryOrder": 1,
                "responseProfile": "compact",
                "page": page,
            }
            if gender_param:
                params["cinsiyet"] = gender_param

            try:
                payload = self.http.get_json(f"{self.base_url}/webservice/v1/list", params=params)
            except ScraperBlockedError:
                raise
            except Exception as exc:  # noqa: BLE001
                page_failures += 1
                logger.warning("[%s] Listing page %d failed: %s", self.brand, page, exc)
                if page == 1:
                    break
                continue

            raw_products = payload.get("products") or []
            if not raw_products:
                break

            for raw_prod in raw_products:
                if not isinstance(raw_prod, dict):
                    continue
                item = self.parse_product(raw_prod)
                if item is not None and item.external_id not in products_by_id:
                    products_by_id[item.external_id] = item

            pagination = payload.get("pagination") or {}
            total_pages = int(pagination.get("totalPages") or 1)
            if page >= total_pages:
                break

        if page_failures > 0 and not products_by_id:
            raise RuntimeError(f"In Street listing failed across {page_failures} page(s)")

        logger.info("[%s] Fetched %d discounted products", self.brand, len(products_by_id))
        return list(products_by_id.values())
