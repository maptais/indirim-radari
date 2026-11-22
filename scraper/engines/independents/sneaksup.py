"""Sneaks Up (`sneaksup.com`) sneaker & streetwear discount scraper."""

from __future__ import annotations

import json
import logging
import re
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
_KIDS_MARKERS = ("çocuk", "cocuk", "bebek", "baby", "junior", "kids", "gs ", "td ", "ps ")
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

SNEAKSUP_CATEGORY_PATHS: dict[str, tuple[str, ...]] = {
    "all": (
        "/sneaks-out-erkek",
        "/sneaks-out-kadin",
        "/erkek-ayakkabi-sneaker",
        "/kadin-ayakkabi-sneaker",
        "/erkek-ayakkabi-kosu-ayakkabisi",
        "/basketbol-ayakkabisi",
    ),
    "men": (
        "/sneaks-out-erkek",
        "/erkek-ayakkabi-sneaker",
        "/erkek-ayakkabi-kosu-ayakkabisi",
        "/basketbol-ayakkabisi",
    ),
    "women": (
        "/sneaks-out-kadin",
        "/kadin-ayakkabi-sneaker",
        "/kadin-ayakkabi",
    ),
}


class SneaksUpScraper(BaseScraper):
    """Standalone scraper for Sneaks Up (`www.sneaksup.com`) via Next.js `__NEXT_DATA__` SSR payload."""

    brand = "sneaksup"
    engine_name = "sneaksup"
    default_run_mode = ScrapeRunMode.CLOUD

    def __init__(
        self,
        *,
        section: Literal["all", "men", "women"] = "all",
        only_discounted: bool = True,
        max_categories: int = 4,
        max_retries: int = 2,
        impersonate: str | None = None,
        timeout_seconds: int = 20,
        min_jitter_seconds: float = 0.6,
        max_jitter_seconds: float = 1.6,
    ) -> None:
        super().__init__(
            timeout_seconds=timeout_seconds,
            min_jitter_seconds=min_jitter_seconds,
            max_jitter_seconds=max_jitter_seconds,
        )
        self.section = section
        self.only_discounted = only_discounted
        self.max_categories = max(1, max_categories)

        self.base_url = "https://www.sneaksup.com"
        self.http = HttpClient(
            self.brand,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            impersonate=impersonate,
            default_headers={
                "Referer": f"{self.base_url}/",
            },
        )

    def close(self) -> None:
        self.http.close()

    @staticmethod
    def extract_next_data_products(html: str) -> list[dict[str, Any]]:
        """Extract raw product dictionaries from Sneaks Up's `__NEXT_DATA__` script block."""
        match = _NEXT_DATA_RE.search(html or "")
        if not match:
            return []
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []

        page_props = (payload.get("props") or {}).get("pageProps") or {}
        product_list = page_props.get("productList") or {}
        products_block = product_list.get("products") or {}
        data = products_block.get("data") or []
        return [item for item in data if isinstance(item, dict)]

    def _matches_section(self, raw: dict[str, Any], path: str) -> bool:
        crumb_names = " ".join(
            str(c.get("name") or "")
            for c in (raw.get("breadcrumb") or raw.get("categories") or [])
            if isinstance(c, dict)
        )
        combined = turkish_lower(f"{raw.get('productName') or ''} {crumb_names} {path}")
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

    def parse_product(self, raw: dict[str, Any], *, category_path: str = "") -> ProductItem | None:
        """Convert a Sneaks Up `__NEXT_DATA__` product dict into a `ProductItem`."""
        raw_name = str(raw.get("productName") or "").strip()
        slug = str(raw.get("slug") or "").strip()
        if not raw_name or not slug:
            return None

        if not self._matches_section(raw, category_path):
            return None

        brand_name = str(raw.get("brandName") or "").strip()
        if brand_name and turkish_lower(brand_name) not in turkish_lower(raw_name):
            title = f"{brand_name.title()} {raw_name}"
        else:
            title = raw_name

        price_obj = raw.get("price") or {}
        if not isinstance(price_obj, dict):
            return None

        new_price = parse_try_price(price_obj.get("newPrice"))
        special_price = parse_try_price(price_obj.get("specialPrice"))
        old_price = parse_try_price(price_obj.get("oldPrice"))

        if special_price is not None and (new_price is None or special_price < new_price):
            current_price = special_price
        else:
            current_price = new_price

        if current_price is None:
            return None

        original_price: Decimal | None = None
        if old_price is not None and old_price > current_price:
            original_price = old_price
        elif new_price is not None and new_price > current_price:
            original_price = new_price

        if self.only_discounted and original_price is None:
            return None

        available_sizes: list[str] = []
        is_critical_stock = False
        for var in raw.get("variants") or []:
            if not isinstance(var, dict):
                continue
            qty_raw = var.get("quantity")
            try:
                qty = int(qty_raw) if qty_raw is not None else 0
            except (TypeError, ValueError):
                qty = 0
            if qty <= 0:
                continue

            if 0 < qty <= _CRITICAL_STOCK_THRESHOLD:
                is_critical_stock = True

            size_val = str(var.get("specValueName") or "").strip()
            if not size_val:
                for sp in var.get("specs") or []:
                    if isinstance(sp, dict) and sp.get("specValueName"):
                        size_val = str(sp["specValueName"]).strip()
                        break
            if size_val:
                available_sizes.append(size_val)

        if not available_sizes:
            return None

        image_url: str | None = None
        for pic in raw.get("pictures") or []:
            if isinstance(pic, dict):
                candidate = pic.get("url")
                if isinstance(candidate, str) and candidate.startswith("http"):
                    image_url = candidate
                    break

        breadcrumbs = [
            str(b.get("name") or "")
            for b in (raw.get("breadcrumb") or raw.get("categories") or [])
            if isinstance(b, dict)
        ]
        inferred_cat = infer_product_category(
            title,
            " ".join(breadcrumbs),
            category_path,
            default=ProductCategory.SHOES,
        )

        external_id = str(raw.get("variantId") or raw.get("productId") or raw.get("erpCode") or slug).strip()

        return ProductItem(
            brand=self.brand,
            external_id=external_id,
            title=title,
            category=inferred_cat,
            url=urljoin(f"{self.base_url}/", slug.lstrip("/")),
            image_url=image_url,
            current_price=current_price,
            original_price=original_price,
            currency="TRY",
            available_sizes=available_sizes,
            is_critical_stock=is_critical_stock,
            raw_metadata={
                "product_id": raw.get("productId"),
                "erp_code": raw.get("erpCode"),
                "breadcrumbs": breadcrumbs,
                "category_path": category_path,
            },
        )

    def fetch_products(self) -> list[ProductItem]:
        """Fetch discounted products with live variant stock from Sneaks Up category pages."""
        paths = SNEAKSUP_CATEGORY_PATHS.get(self.section) or SNEAKSUP_CATEGORY_PATHS["all"]
        selected_paths = list(paths[: self.max_categories])

        products_by_id: dict[str, ProductItem] = {}
        failures = 0

        for idx, path in enumerate(selected_paths):
            if idx > 0:
                self.sleep_jitter()
            try:
                html = self.http.get_text(f"{self.base_url}{path}")
            except ScraperBlockedError:
                raise
            except Exception as exc:  # noqa: BLE001
                failures += 1
                logger.warning("[%s] Category %s failed: %s", self.brand, path, exc)
                continue

            raw_products = self.extract_next_data_products(html)
            for raw_prod in raw_products:
                item = self.parse_product(raw_prod, category_path=path)
                if item is not None and item.external_id not in products_by_id:
                    products_by_id[item.external_id] = item

        if failures == len(selected_paths) and not products_by_id:
            raise RuntimeError(f"All {len(selected_paths)} Sneaks Up categories failed")

        logger.info("[%s] Fetched %d discounted products", self.brand, len(products_by_id))
        return list(products_by_id.values())
