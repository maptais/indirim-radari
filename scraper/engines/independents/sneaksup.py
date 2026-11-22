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
    ProductGender,
    ProductItem,
    ScrapeRunMode,
    ScraperBlockedError,
)
from ...core.filters import turkish_lower
from ...core.http import (
    HttpClient,
    clean_promo_badge_text,
    infer_product_category,
    infer_product_gender,
    is_kids_product,
    parse_try_price,
    resolve_cart_promo,
)

logger = logging.getLogger(__name__)

_CRITICAL_STOCK_THRESHOLD = 3
_NEXT_DATA_RE = re.compile(
    r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

SNEAKSUP_CATEGORY_PATHS: dict[str, tuple[str, ...]] = {
    "all": (
        "/sneaks-out",
        "/sneaks-out-erkek",
        "/sneaks-out-kadin",
        "/erkek-ayakkabi-sneaker",
        "/kadin-ayakkabi-sneaker",
        "/erkek-ayakkabi-kosu-ayakkabisi",
    ),
    "men": (
        "/sneaks-out-erkek",
        "/sneaks-out",
        "/erkek-ayakkabi-sneaker",
        "/erkek-ayakkabi-kosu-ayakkabisi",
    ),
    "women": (
        "/sneaks-out-kadin",
        "/sneaks-out",
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
        max_pages_per_category: int = 4,
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
        self.max_pages_per_category = max(1, max_pages_per_category)

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
    def extract_next_data_page(html: str) -> tuple[list[dict[str, Any]], bool]:
        """Extract raw product dicts and `hasNextPage` flag from Sneaks Up's `__NEXT_DATA__`."""
        match = _NEXT_DATA_RE.search(html or "")
        if not match:
            return [], False
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            return [], False

        page_props = (payload.get("props") or {}).get("pageProps") or {}
        product_list = page_props.get("productList") or {}
        products_block = product_list.get("products") or {}
        data = products_block.get("data") or []
        has_next = bool(products_block.get("hasNextPage"))
        return [item for item in data if isinstance(item, dict)], has_next

    @classmethod
    def extract_next_data_products(cls, html: str) -> list[dict[str, Any]]:
        """Extract raw product dictionaries from Sneaks Up's `__NEXT_DATA__` script block."""
        items, _ = cls.extract_next_data_page(html)
        return items

    @staticmethod
    def _infer_gender(raw: dict[str, Any], path: str) -> ProductGender:
        crumb_names = " ".join(
            str(c.get("name") or "")
            for c in (raw.get("breadcrumb") or raw.get("categories") or [])
            if isinstance(c, dict)
        )
        return infer_product_gender(
            str(raw.get("gender") or ""),
            str(raw.get("productName") or ""),
            crumb_names,
            str(raw.get("slug") or ""),
            path,
            default=ProductGender.UNISEX,
        )

    def _matches_section(self, raw: dict[str, Any], path: str) -> bool:
        crumb_names = " ".join(
            str(c.get("name") or "")
            for c in (raw.get("breadcrumb") or raw.get("categories") or [])
            if isinstance(c, dict)
        )
        if is_kids_product(str(raw.get("productName") or ""), crumb_names, str(raw.get("slug") or ""), path):
            return False
        if self.section == "all":
            return True

        gender = self._infer_gender(raw, path)
        if gender == ProductGender.UNISEX:
            return True
        if self.section == "men":
            return gender == ProductGender.MEN
        if self.section == "women":
            return gender == ProductGender.WOMEN
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

        current_price = new_price or special_price
        if current_price is None:
            return None

        original_price: Decimal | None = None
        if old_price is not None and old_price > current_price:
            original_price = old_price

        explicit_cart: Decimal | None = None
        if special_price is not None and new_price is not None and special_price < new_price:
            explicit_cart = special_price

        badge_candidates: list[str] = []
        for key in ("ribbonText", "campaignText", "promoBadge", "specialPriceLabel"):
            txt = clean_promo_badge_text(str(raw.get(key) or price_obj.get(key) or ""))
            if txt:
                badge_candidates.append(txt)
        for badge_key in ("badges", "campaigns", "productTags"):
            for entry in raw.get(badge_key) or []:
                raw_txt = (
                    str(entry.get("text") or entry.get("name") or entry.get("title") or entry.get("label") or "")
                    if isinstance(entry, dict)
                    else str(entry or "")
                )
                txt = clean_promo_badge_text(raw_txt)
                if txt and any(
                    kw in turkish_lower(txt)
                    for kw in ("sepet", "%", "indirim", "ürün", "urun", "al ", "öde", "ode", "kampanya", "özel")
                ):
                    badge_candidates.append(txt)

        chosen_badge = badge_candidates[0] if badge_candidates else None
        for cand in badge_candidates:
            if "sepet" in turkish_lower(cand) or "%" in cand:
                chosen_badge = cand
                break

        cart_price, promo_badge = resolve_cart_promo(
            current_price,
            explicit_cart_price=explicit_cart,
            promo_badge=chosen_badge,
        )

        if self.only_discounted and original_price is None and cart_price is None:
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

        inferred_gender = self._infer_gender(raw, category_path)
        external_id = str(raw.get("variantId") or raw.get("productId") or raw.get("erpCode") or slug).strip()

        return ProductItem(
            brand=self.brand,
            external_id=external_id,
            title=title,
            category=inferred_cat,
            gender=inferred_gender,
            url=urljoin(f"{self.base_url}/", slug.lstrip("/")),
            image_url=image_url,
            current_price=current_price,
            original_price=original_price,
            cart_price=cart_price,
            promo_badge=promo_badge,
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

            cat_failed = False
            for page_idx in range(1, self.max_pages_per_category + 1):
                if page_idx > 1:
                    self.sleep_jitter()

                url = (
                    f"{self.base_url}{path}"
                    if page_idx == 1
                    else f"{self.base_url}{path}?pageIndex={page_idx}"
                )
                try:
                    html = self.http.get_text(url)
                except ScraperBlockedError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] Category %s page %d failed: %s", self.brand, path, page_idx, exc)
                    if page_idx == 1:
                        cat_failed = True
                    break

                raw_products, has_next_page = self.extract_next_data_page(html)
                if not raw_products:
                    break

                new_in_page = 0
                for raw_prod in raw_products:
                    item = self.parse_product(raw_prod, category_path=path)
                    if item is not None and item.external_id not in products_by_id:
                        products_by_id[item.external_id] = item
                        new_in_page += 1

                if not has_next_page or new_in_page == 0:
                    break

            if cat_failed:
                failures += 1

        if failures == len(selected_paths) and not products_by_id:
            raise RuntimeError(f"All {len(selected_paths)} Sneaks Up categories failed")

        logger.info("[%s] Fetched %d discounted products", self.brand, len(products_by_id))
        return list(products_by_id.values())
