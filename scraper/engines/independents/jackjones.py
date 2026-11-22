"""Jack & Jones Turkey (`jackjones.com.tr`) scraper backed by Vue SSR `window.__INITIAL_STATE__`."""

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
from ...core.filters import extract_polyester_percent, turkish_lower
from ...core.http import HttpClient, infer_product_category, parse_try_price

logger = logging.getLogger(__name__)

_KIDS_MARKERS = ("çocuk", "cocuk", "junior", "kids", "bebek", "baby")
_INITIAL_STATE_MARKER = "window.__INITIAL_STATE__"

JACKJONES_CATEGORY_PATHS: dict[str, tuple[str, ...]] = {
    "all": (
        "/tr-tr/indirim",
        "/tr-tr/erkek-giyim",
        "/tr-tr/kadin-giyim",
        "/tr-tr/erkek-giyim/kazak",
        "/tr-tr/kadin-giyim/kazak-ve-hirka",
    ),
    "men": (
        "/tr-tr/indirim",
        "/tr-tr/erkek-giyim",
        "/tr-tr/erkek-giyim/kazak",
    ),
    "women": (
        "/tr-tr/indirim",
        "/tr-tr/kadin-giyim",
        "/tr-tr/kadin-giyim/kazak-ve-hirka",
    ),
}


def _extract_balanced_object(text: str, start_idx: int) -> dict[str, Any] | None:
    """Extract a balanced JSON object starting at `start_idx`."""
    if start_idx < 0 or start_idx >= len(text) or text[start_idx] != "{":
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
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start_idx : idx + 1])
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


class JackJonesScraper(BaseScraper):
    """Standalone scraper for Jack & Jones Turkey (`www.jackjones.com.tr`).

    Parses product catalogs from `window.__INITIAL_STATE__` (`$getProducts-*`)
    and fetches variant size stock + fabric composition from discounted PDPs.
    """

    brand = "jackjones"
    engine_name = "jackjones"
    default_run_mode = ScrapeRunMode.CLOUD

    def __init__(
        self,
        *,
        section: Literal["all", "men", "women"] = "all",
        only_discounted: bool = True,
        max_categories: int = 4,
        max_detail_fetches: int = 24,
        fetch_variant_details: bool = True,
        max_retries: int = 2,
        impersonate: str | None = None,
        timeout_seconds: int = 25,
        min_jitter_seconds: float = 0.5,
        max_jitter_seconds: float = 1.2,
    ) -> None:
        super().__init__(
            timeout_seconds=timeout_seconds,
            min_jitter_seconds=min_jitter_seconds,
            max_jitter_seconds=max_jitter_seconds,
        )
        self.section = section
        self.only_discounted = only_discounted
        self.max_categories = max(1, max_categories)
        self.max_detail_fetches = max(0, max_detail_fetches)
        self.fetch_variant_details = fetch_variant_details

        self.base_url = "https://www.jackjones.com.tr"
        self.http = HttpClient(
            self.brand,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            impersonate=impersonate,
            default_headers={
                "Referer": f"{self.base_url}/tr-tr",
            },
        )

    def close(self) -> None:
        self.http.close()

    @staticmethod
    def extract_initial_state(page_html: str) -> dict[str, Any]:
        """Extract `window.__INITIAL_STATE__` JSON dict from a Jack & Jones TR HTML page."""
        if not page_html:
            return {}
        marker_idx = page_html.find(_INITIAL_STATE_MARKER)
        if marker_idx == -1:
            return {}
        brace_idx = page_html.find("{", marker_idx)
        return _extract_balanced_object(page_html, brace_idx) or {}

    @classmethod
    def extract_listing_products(cls, page_html: str) -> list[dict[str, Any]]:
        """Extract raw listing product dicts from `$getProducts*` keys in `__INITIAL_STATE__`."""
        state = cls.extract_initial_state(page_html)
        products: list[dict[str, Any]] = []
        for key, value in state.items():
            if not key.startswith("$getProducts") or not isinstance(value, dict):
                continue
            results = value.get("results") or []
            if isinstance(results, list):
                for entry in results:
                    if isinstance(entry, dict):
                        products.append(entry)
        return products

    @classmethod
    def extract_detail_metadata(
        cls,
        detail_html: str,
    ) -> tuple[list[str], bool, str | None, int | None]:
        """Extract in-stock sizes, critical stock flag, and optional fabric info from PDP HTML."""
        state = cls.extract_initial_state(detail_html)
        available_sizes: list[str] = []
        fabric_parts: list[str] = []

        def _walk(node: Any, depth: int = 0) -> None:
            if depth > 7:
                return
            if isinstance(node, dict):
                variants = node.get("variants")
                if isinstance(variants, list) and variants and not available_sizes:
                    for var in variants:
                        if not isinstance(var, dict):
                            continue
                        in_stock = bool(var.get("inStock") or var.get("inStockEcom"))
                        if not in_stock:
                            continue
                        size_obj = var.get("size")
                        size_name = (
                            str(size_obj.get("name") or "").strip()
                            if isinstance(size_obj, dict)
                            else str(var.get("sizeName") or "").strip()
                        )
                        if size_name and size_name not in available_sizes:
                            available_sizes.append(size_name)

                for fab_key in ("materialComposition", "composition", "fabric", "material"):
                    raw_fab = node.get(fab_key)
                    if isinstance(raw_fab, str) and raw_fab.strip():
                        cleaned = re.sub(r"<[^>]+>", " ", raw_fab)
                        cleaned = re.sub(r"\s+", " ", cleaned).strip()
                        if cleaned and cleaned not in fabric_parts:
                            fabric_parts.append(cleaned)
                    elif isinstance(raw_fab, list):
                        for part in raw_fab:
                            if isinstance(part, str) and part.strip():
                                cleaned = re.sub(r"\s+", " ", part).strip()
                                if cleaned and cleaned not in fabric_parts:
                                    fabric_parts.append(cleaned)

                for val in node.values():
                    _walk(val, depth + 1)
            elif isinstance(node, list):
                for item in node:
                    _walk(item, depth + 1)

        _walk(state)

        is_critical_stock = 0 < len(available_sizes) <= 2
        fabric_info = ", ".join(fabric_parts[:3]) if fabric_parts else None
        polyester_pct = extract_polyester_percent(fabric_info)
        return available_sizes, is_critical_stock, fabric_info, polyester_pct

    @staticmethod
    def _extract_prices(price_obj: dict[str, Any]) -> tuple[Decimal | None, Decimal | None]:
        if not isinstance(price_obj, dict):
            return None, None

        sales_obj = price_obj.get("salesPrice")
        list_obj = price_obj.get("listPrice") or price_obj.get("originalPrice") or price_obj.get("beforePrice")

        current_price = parse_try_price(
            sales_obj.get("amount") if isinstance(sales_obj, dict) else sales_obj
        )
        original_price = parse_try_price(
            list_obj.get("amount") if isinstance(list_obj, dict) else list_obj
        )

        if current_price is None and original_price is not None:
            current_price = original_price
            original_price = None

        if original_price is not None and current_price is not None and original_price <= current_price:
            original_price = None

        return current_price, original_price

    def _matches_section(self, raw: dict[str, Any], category_path: str) -> bool:
        title = str(raw.get("name") or "")
        sub_brand = str(raw.get("subBrand") or "")
        url = str(raw.get("url") or "")
        combined = turkish_lower(f"{title} {sub_brand} {url} {category_path}")

        if any(k in combined for k in _KIDS_MARKERS):
            return False
        if self.section == "all":
            return True
        if "unisex" in combined:
            return True

        is_women = "kadın" in combined or "kadin" in combined or "jjxx" in combined
        is_men = "erkek" in combined or (not is_women and "jack" in turkish_lower(str(raw.get("brand") or "")))
        if self.section == "men":
            return is_men and not is_women
        if self.section == "women":
            return is_women
        return True

    def parse_listing_product(
        self,
        raw: dict[str, Any],
        *,
        category_path: str = "",
        detail_html: str | None = None,
    ) -> ProductItem | None:
        """Convert a Jack & Jones listing dict (+ optional PDP HTML) into a `ProductItem`."""
        if raw.get("inStock") is False:
            return None

        prod_id = str(raw.get("id") or raw.get("ean") or "").strip()
        title = str(raw.get("name") or "").strip()
        rel_url = str(raw.get("url") or "").strip()
        if not prod_id or not title or not rel_url:
            return None

        if not self._matches_section(raw, category_path):
            return None

        current_price, original_price = self._extract_prices(raw.get("price") or {})
        if current_price is None:
            return None
        if self.only_discounted and original_price is None:
            return None

        image_url: str | None = None
        for key in ("frontImage", "backImage"):
            img_val = raw.get(key)
            if isinstance(img_val, str) and img_val.startswith("http"):
                image_url = img_val
                break
            if isinstance(img_val, dict):
                cand = img_val.get("url") or img_val.get("src")
                if isinstance(cand, str) and cand.startswith("http"):
                    image_url = cand
                    break
        if image_url is None:
            for img in raw.get("images") or []:
                if isinstance(img, str) and img.startswith("http"):
                    image_url = img
                    break
                if isinstance(img, dict):
                    cand = img.get("url") or img.get("src")
                    if isinstance(cand, str) and cand.startswith("http"):
                        image_url = cand
                        break

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
            raw_size = str(raw.get("sizeName") or "").strip()
            if raw_size:
                available_sizes = [raw_size]

        if self.fetch_variant_details and detail_html is not None and not available_sizes:
            return None

        inferred_cat = infer_product_category(
            title,
            rel_url,
            category_path,
            default=ProductCategory.TOPS,
        )

        return ProductItem(
            brand=self.brand,
            external_id=prod_id,
            title=title,
            category=inferred_cat,
            url=urljoin(f"{self.base_url}/", rel_url.lstrip("/")),
            image_url=image_url,
            current_price=current_price,
            original_price=original_price,
            currency="TRY",
            available_sizes=available_sizes,
            is_critical_stock=is_critical_stock,
            fabric_info=fabric_info,
            polyester_percent=polyester_pct,
            raw_metadata={
                "sub_brand": raw.get("subBrand"),
                "category_path": category_path,
            },
        )

    def fetch_products(self) -> list[ProductItem]:
        """Fetch discounted Jack & Jones Turkey products and PDP size availability."""
        paths = JACKJONES_CATEGORY_PATHS.get(self.section) or JACKJONES_CATEGORY_PATHS["all"]
        selected_paths = list(paths[: self.max_categories])

        candidates: list[tuple[dict[str, Any], str, float]] = []
        seen_ids: set[str] = set()
        cat_failures = 0

        for idx, path in enumerate(selected_paths):
            if idx > 0:
                self.sleep_jitter()

            url = f"{self.base_url}{path}"
            try:
                page_html = self.http.get_text(url)
            except ScraperBlockedError:
                raise
            except Exception as exc:  # noqa: BLE001
                cat_failures += 1
                logger.warning("[%s] Category %s failed: %s", self.brand, path, exc)
                continue

            raw_products = self.extract_listing_products(page_html)
            for raw_prod in raw_products:
                pid = str(raw_prod.get("id") or "").strip()
                if not pid or pid in seen_ids:
                    continue
                if raw_prod.get("inStock") is False:
                    continue
                if not self._matches_section(raw_prod, path):
                    continue
                cur_p, orig_p = self._extract_prices(raw_prod.get("price") or {})
                if cur_p is None:
                    continue
                if self.only_discounted and orig_p is None:
                    continue

                disc_ratio = float((orig_p - cur_p) / orig_p) if orig_p and orig_p > cur_p else 0.0
                seen_ids.add(pid)
                candidates.append((raw_prod, path, disc_ratio))

        if cat_failures == len(selected_paths) and not candidates:
            raise RuntimeError(f"All {len(selected_paths)} Jack & Jones categories failed")

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
