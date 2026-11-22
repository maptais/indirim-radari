"""Shared Trendyol Milla & Trendyol Man scraper engine (`trendyol-milla.com`)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urljoin

from .base import (
    BaseScraper,
    ProductCategory,
    ProductItem,
    ScrapeRunMode,
    ScraperBlockedError,
)
from ..core.filters import extract_polyester_percent, turkish_lower
from ..core.http import HttpClient, infer_product_category, parse_try_price

logger = logging.getLogger(__name__)

_CRITICAL_STOCK_THRESHOLD = 3
_KIDS_MARKERS = ("çocuk", "cocuk", "bebek", "baby", "kids", "junior", "kız çocuk", "erkek çocuk")
_SEARCH_PROPS_MARKER = 'window["__single-search-result__PROPS"]'
_MATERIAL_KEYS = ("materyal bileşeni", "materyal bileseni", "materyal", "kumaş", "kumas", "içerik", "icerik")


def _extract_balanced_json(text: str, start_idx: int, open_ch: str, close_ch: str) -> Any | None:
    """Extract a balanced JSON object or array starting at `start_idx`."""
    if start_idx < 0 or start_idx >= len(text) or text[start_idx] != open_ch:
        return None
    depth = 0
    in_string = False
    escape = False
    for idx in range(start_idx, len(text)):
        ch = text[idx]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start_idx : idx + 1])
                except json.JSONDecodeError:
                    return None
    return None


@dataclass(frozen=True)
class TrendyolBrandConfig:
    """Configuration for a Trendyol private-label storefront (`trendyolmilla` or `trendyolman`)."""

    brand: str
    display_name: str
    gender_scope: Literal["men", "women"]
    category_paths: tuple[str, ...]
    default_run_mode: ScrapeRunMode = ScrapeRunMode.CLOUD


TRENDYOL_BRAND_CONFIGS: dict[str, TrendyolBrandConfig] = {
    "trendyolmilla": TrendyolBrandConfig(
        brand="trendyolmilla",
        display_name="Trendyol Milla",
        gender_scope="women",
        category_paths=(
            "/kadin-giyim-x-g1-c82",
            "/kadin-ust-giyim-x-g1-c101456",
            "/kadin-dis-giyim-x-g1-c104024",
            "/kadin-pantolon-x-g1-c70",
            "/kadin-kazak-x-g1-c1092",
        ),
        default_run_mode=ScrapeRunMode.CLOUD,
    ),
    "trendyolman": TrendyolBrandConfig(
        brand="trendyolman",
        display_name="Trendyol Man",
        gender_scope="men",
        category_paths=(
            "/erkek-giyim-x-g2-c82",
            "/erkek-ust-giyim-x-g2-c101456",
            "/erkek-dis-giyim-x-g2-c104024",
            "/erkek-pantolon-x-g2-c70",
            "/erkek-sweatshirt-x-g2-c1179",
        ),
        default_run_mode=ScrapeRunMode.CLOUD,
    ),
}


class TrendyolScraper(BaseScraper):
    """Unified Trendyol Milla / Trendyol Man scraper via `www.trendyol-milla.com` SSR payloads."""

    engine_name = "trendyol"

    def __init__(
        self,
        brand: str,
        *,
        section: Literal["all", "men", "women"] = "all",
        only_discounted: bool = True,
        max_categories: int = 4,
        max_pages_per_category: int = 2,
        max_detail_fetches: int = 24,
        fetch_variant_details: bool = True,
        max_retries: int = 2,
        impersonate: str | None = None,
        timeout_seconds: int = 20,
        min_jitter_seconds: float = 0.4,
        max_jitter_seconds: float = 1.0,
    ) -> None:
        brand_key = brand.strip().lower()
        if brand_key not in TRENDYOL_BRAND_CONFIGS:
            supported = ", ".join(sorted(TRENDYOL_BRAND_CONFIGS))
            raise ValueError(f"Unsupported Trendyol brand '{brand}'. Supported: {supported}")

        self.config = TRENDYOL_BRAND_CONFIGS[brand_key]
        self.brand = self.config.brand
        self.default_run_mode = self.config.default_run_mode

        super().__init__(
            timeout_seconds=timeout_seconds,
            min_jitter_seconds=min_jitter_seconds,
            max_jitter_seconds=max_jitter_seconds,
        )

        self.section = section
        self.only_discounted = only_discounted
        self.max_categories = max(1, max_categories)
        self.max_pages_per_category = max(1, max_pages_per_category)
        self.max_detail_fetches = max(0, max_detail_fetches)
        self.fetch_variant_details = fetch_variant_details

        self.base_url = "https://www.trendyol-milla.com"
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
    def extract_listing_products(page_html: str) -> list[dict[str, Any]]:
        """Extract raw product dicts from `window[\"__single-search-result__PROPS\"]`."""
        if not page_html:
            return []
        marker_idx = page_html.find(_SEARCH_PROPS_MARKER)
        if marker_idx == -1:
            return []
        brace_idx = page_html.find("{", marker_idx)
        payload = _extract_balanced_json(page_html, brace_idx, "{", "}")
        if not isinstance(payload, dict):
            return []
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        products = data.get("products") if isinstance(data, dict) else []
        if not isinstance(products, list):
            return []
        return [p for p in products if isinstance(p, dict)]

    @staticmethod
    def extract_detail_metadata(
        detail_html: str,
    ) -> tuple[list[str], bool, str | None, int | None]:
        """Extract in-stock sizes, critical-stock flag, and fabric info from Trendyol PDP HTML."""
        available_sizes: list[str] = []
        is_critical_stock = False
        fabric_parts: list[str] = []

        if not detail_html:
            return available_sizes, is_critical_stock, None, None

        # 1. Parse `"allVariants":[...]` or `"variants":[...]`
        for key_token in ('"allVariants":', '"variants":'):
            search_from = 0
            while True:
                pos = detail_html.find(key_token, search_from)
                if pos == -1:
                    break
                arr_start = detail_html.find("[", pos + len(key_token))
                if arr_start == -1 or arr_start - pos > len(key_token) + 8:
                    search_from = pos + len(key_token)
                    continue
                variants = _extract_balanced_json(detail_html, arr_start, "[", "]")
                search_from = arr_start + 1
                if not isinstance(variants, list) or not variants:
                    continue

                parsed_sizes: list[str] = []
                for var in variants:
                    if not isinstance(var, dict):
                        continue
                    in_stock = var.get("inStock")
                    if in_stock is False:
                        continue
                    size_label = str(
                        var.get("beautifiedValue")
                        or var.get("value")
                        or var.get("attributeValue")
                        or ""
                    ).strip()
                    if not size_label:
                        continue
                    parsed_sizes.append(size_label)

                    stock_qty = var.get("stock")
                    if isinstance(stock_qty, (int, float)) and 0 < int(stock_qty) <= _CRITICAL_STOCK_THRESHOLD:
                        is_critical_stock = True
                    warning_msg = turkish_lower(str(var.get("stockWarning") or var.get("warningMessage") or ""))
                    if "son " in warning_msg or "tüken" in warning_msg:
                        is_critical_stock = True

                if parsed_sizes:
                    available_sizes = parsed_sizes
                    break
            if available_sizes:
                break

        if 0 < len(available_sizes) <= 2:
            is_critical_stock = True

        # 2. Parse `"attributes":[...]` for fabric / material composition
        attr_pos = detail_html.find('"attributes":[')
        if attr_pos != -1:
            arr_start = detail_html.find("[", attr_pos)
            attrs = _extract_balanced_json(detail_html, arr_start, "[", "]")
            if isinstance(attrs, list):
                for attr in attrs:
                    if not isinstance(attr, dict):
                        continue
                    key_obj = attr.get("key")
                    val_obj = attr.get("value")
                    key_name = turkish_lower(
                        str(key_obj.get("name") if isinstance(key_obj, dict) else key_obj or "")
                    )
                    val_name = str(
                        val_obj.get("name") if isinstance(val_obj, dict) else val_obj or ""
                    ).strip()
                    if not val_name:
                        continue
                    if any(mk in key_name for mk in _MATERIAL_KEYS):
                        if val_name not in fabric_parts:
                            fabric_parts.append(val_name)

        fabric_info = ", ".join(fabric_parts) if fabric_parts else None
        polyester_pct = extract_polyester_percent(fabric_info)
        return available_sizes, is_critical_stock, fabric_info, polyester_pct

    @staticmethod
    def _extract_prices(price_obj: dict[str, Any]) -> tuple[Decimal | None, Decimal | None]:
        """Extract `(current_price, original_price)` from Trendyol's `price` object."""
        if not isinstance(price_obj, dict):
            return None, None

        discounted = parse_try_price(price_obj.get("discountedPrice"))
        current = parse_try_price(price_obj.get("current"))
        original = parse_try_price(price_obj.get("originalPrice"))
        old = parse_try_price(price_obj.get("old"))
        selling = parse_try_price(price_obj.get("sellingPrice"))

        candidates = [p for p in (discounted, current, selling, original, old) if p is not None]
        if not candidates:
            return None, None

        eff_current = min(candidates)
        eff_max = max(candidates)
        eff_original = eff_max if eff_max > eff_current else None
        return eff_current, eff_original

    def _matches_brand_and_section(self, raw: dict[str, Any]) -> bool:
        title = str(raw.get("name") or "")
        cat_obj = raw.get("category") or {}
        cat_name = str(cat_obj.get("name") if isinstance(cat_obj, dict) else "")
        combined = turkish_lower(f"{title} {cat_name}")
        if any(k in combined for k in _KIDS_MARKERS):
            return False

        if self.section != "all" and self.section != self.config.gender_scope:
            if "unisex" not in combined:
                return False
        return True

    def parse_listing_product(
        self,
        raw: dict[str, Any],
        *,
        category_path: str = "",
        detail_html: str | None = None,
    ) -> ProductItem | None:
        """Convert a Trendyol listing product (+ optional PDP HTML) into a `ProductItem`."""
        title = str(raw.get("name") or "").strip()
        rel_url = str(raw.get("url") or "").strip()
        prod_id = str(raw.get("id") or raw.get("contentId") or "").strip()
        if not title or not rel_url or not prod_id:
            return None

        if not self._matches_brand_and_section(raw):
            return None

        current_price, original_price = self._extract_prices(raw.get("price") or {})
        if current_price is None:
            return None
        if self.only_discounted and original_price is None:
            return None

        image_url: str | None = None
        primary_img = raw.get("image")
        if isinstance(primary_img, str) and primary_img.startswith("http"):
            image_url = primary_img
        else:
            images = raw.get("images") or []
            if isinstance(images, list) and images and isinstance(images[0], str):
                image_url = images[0] if images[0].startswith("http") else f"https://cdn.dsmcdn.com{images[0]}"

        available_sizes: list[str] = []
        is_critical_stock = False
        fabric_info: str | None = None
        polyester_pct: int | None = None

        if detail_html:
            (
                available_sizes,
                is_critical_stock,
                fabric_info,
                polyester_pct,
            ) = self.extract_detail_metadata(detail_html)
        else:
            raw_variants = raw.get("variants") or []
            if isinstance(raw_variants, list):
                for var in raw_variants:
                    if isinstance(var, dict) and var.get("inStock", True):
                        val = str(var.get("value") or var.get("beautifiedValue") or "").strip()
                        if val:
                            available_sizes.append(val)

        if self.fetch_variant_details and detail_html is not None and not available_sizes:
            return None

        cat_obj = raw.get("category") or {}
        cat_name = str(cat_obj.get("name") if isinstance(cat_obj, dict) else "")
        inferred_cat = infer_product_category(
            title,
            cat_name,
            category_path,
            default=ProductCategory.TOPS,
        )

        # Normalize product URL without noisy query params if desired, keeping full link clickable
        clean_url = urljoin(f"{self.base_url}/", rel_url.lstrip("/"))

        return ProductItem(
            brand=self.brand,
            external_id=prod_id,
            title=title,
            category=inferred_cat,
            url=clean_url,
            image_url=image_url,
            current_price=current_price,
            original_price=original_price,
            currency="TRY",
            available_sizes=available_sizes,
            is_critical_stock=is_critical_stock,
            fabric_info=fabric_info,
            polyester_percent=polyester_pct,
            raw_metadata={
                "source_brand": raw.get("brand"),
                "category_name": cat_name,
                "category_path": category_path,
            },
        )

    def fetch_products(self) -> list[ProductItem]:
        """Fetch discounted products and per-size stock for Trendyol Milla or Trendyol Man."""
        if self.section != "all" and self.section != self.config.gender_scope:
            logger.info(
                "[%s] Skipping because section='%s' does not match brand scope '%s'",
                self.brand,
                self.section,
                self.config.gender_scope,
            )
            return []

        selected_paths = list(self.config.category_paths[: self.max_categories])
        candidates: list[tuple[dict[str, Any], str, float]] = []
        seen_ids: set[str] = set()
        cat_failures = 0

        for cat_idx, path in enumerate(selected_paths):
            if cat_idx > 0:
                self.sleep_jitter()

            cat_failed = False
            for page in range(1, self.max_pages_per_category + 1):
                if page > 1:
                    self.sleep_jitter()

                url = f"{self.base_url}{path}?sst=PRICE_BY_ASC&pi={page}"
                try:
                    page_html = self.http.get_text(url)
                except ScraperBlockedError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] Listing error on %s page %d: %s", self.brand, path, page, exc)
                    if page == 1:
                        cat_failed = True
                    break

                raw_products = self.extract_listing_products(page_html)
                if not raw_products:
                    break

                for raw_prod in raw_products:
                    pid = str(raw_prod.get("id") or raw_prod.get("contentId") or "").strip()
                    if not pid or pid in seen_ids:
                        continue
                    if not self._matches_brand_and_section(raw_prod):
                        continue
                    cur_p, orig_p = self._extract_prices(raw_prod.get("price") or {})
                    if cur_p is None:
                        continue
                    if self.only_discounted and orig_p is None:
                        continue

                    disc_ratio = float((orig_p - cur_p) / orig_p) if orig_p and orig_p > cur_p else 0.0
                    seen_ids.add(pid)
                    candidates.append((raw_prod, path, disc_ratio))

            if cat_failed:
                cat_failures += 1

        if cat_failures == len(selected_paths) and not candidates:
            raise RuntimeError(f"All {len(selected_paths)} Trendyol categories failed for {self.brand}")

        candidates.sort(key=lambda entry: -entry[2])
        logger.info(
            "[%s] Collected %d discounted listing candidates across %d categories",
            self.brand,
            len(candidates),
            len(selected_paths),
        )

        products: list[ProductItem] = []
        detail_fetches = 0

        for raw_prod, path, _ in candidates:
            detail_html: str | None = None
            if self.fetch_variant_details:
                if detail_fetches >= self.max_detail_fetches:
                    break
                if detail_fetches > 0:
                    self.sleep_jitter()
                rel_url = str(raw_prod.get("url") or "").strip()
                if not rel_url:
                    continue
                pdp_url = urljoin(f"{self.base_url}/", rel_url.lstrip("/"))
                try:
                    detail_html = self.http.get_text(pdp_url)
                    detail_fetches += 1
                except ScraperBlockedError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[%s] Detail fetch failed for %s: %s", self.brand, pdp_url, exc)
                    continue

            item = self.parse_listing_product(
                raw_prod,
                category_path=path,
                detail_html=detail_html,
            )
            if item is not None:
                products.append(item)

        logger.info("[%s] Fetched %d discounted products", self.brand, len(products))
        return products
