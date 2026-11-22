"""Puma Turkey Outlet (`tr.puma.com`) scraper backed by Magento listing HTML + `spConfig` variant JSON."""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from dataclasses import dataclass
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

_KIDS_MARKERS = ("çocuk", "cocuk", "bebek", "baby", "junior", "jr", "kids", "inf", "ps ", "td ")
_LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)
_ITEM_LINK_RE = re.compile(
    r'<a[^>]+class=["\'][^"\']*product-item__name[^"\']*["\'][^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
    re.DOTALL | re.IGNORECASE,
)
_FINAL_PRICE_RE = re.compile(
    r'data-price-amount=["\']([^"\']+)["\'][^>]*data-price-type=["\']finalPrice["\']'
    r'|data-price-type=["\']finalPrice["\'][^>]*data-price-amount=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_OLD_PRICE_RE = re.compile(
    r'data-price-amount=["\']([^"\']+)["\'][^>]*data-price-type=["\']oldPrice["\']'
    r'|data-price-type=["\']oldPrice["\'][^>]*data-price-amount=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_SKU_RE = re.compile(r'data-product-sku=["\']([^"\']+)["\']', re.IGNORECASE)
_PID_RE = re.compile(r'data-product-id=["\'](\d+)["\']', re.IGNORECASE)

PUMA_OUTLET_PATHS: dict[str, tuple[str, ...]] = {
    "all": ("/outlet/erkek.html", "/outlet/kadin.html", "/outlet.html"),
    "men": ("/outlet/erkek.html",),
    "women": ("/outlet/kadin.html",),
}


@dataclass
class _PumaListingCard:
    external_id: str
    title: str
    url: str
    image_url: str | None
    current_price: Decimal
    original_price: Decimal | None
    category_path: str


def _extract_balanced_json_object(text: str, start_brace_idx: int) -> dict[str, Any] | None:
    """Extract a balanced JSON object starting at `start_brace_idx`."""
    if start_brace_idx < 0 or start_brace_idx >= len(text) or text[start_brace_idx] != "{":
        return None
    depth = 0
    in_string = False
    escape = False
    for idx in range(start_brace_idx, len(text)):
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
                    parsed = json.loads(text[start_brace_idx : idx + 1])
                    return parsed if isinstance(parsed, dict) else None
                except json.JSONDecodeError:
                    return None
    return None


class PumaOutletScraper(BaseScraper):
    """Standalone scraper for Puma TR Outlet (`tr.puma.com`)."""

    brand = "pumaoutlet"
    engine_name = "pumaoutlet"
    default_run_mode = ScrapeRunMode.CLOUD

    def __init__(
        self,
        *,
        section: Literal["all", "men", "women"] = "all",
        only_discounted: bool = True,
        max_categories: int = 3,
        max_pages_per_category: int = 2,
        max_detail_fetches: int = 24,
        fetch_variant_details: bool = True,
        max_retries: int = 2,
        impersonate: str | None = None,
        timeout_seconds: int = 20,
        min_jitter_seconds: float = 0.3,
        max_jitter_seconds: float = 0.8,
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
        self.max_detail_fetches = max(0, max_detail_fetches)
        self.fetch_variant_details = fetch_variant_details

        self.base_url = "https://tr.puma.com"
        self.http = HttpClient(
            self.brand,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            impersonate=impersonate,
            default_headers={
                "Referer": f"{self.base_url}/outlet.html",
            },
        )

    def close(self) -> None:
        self.http.close()

    def _matches_section(self, title: str, url: str, category_path: str) -> bool:
        combined = turkish_lower(f"{title} {url} {category_path}")
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

    @classmethod
    def parse_listing_html(
        cls,
        page_html: str,
        *,
        base_url: str = "https://tr.puma.com",
        category_path: str = "/outlet.html",
    ) -> list[_PumaListingCard]:
        """Parse Puma TR Magento outlet listing HTML combining `ld+json` and card price markup."""
        ld_by_url: dict[str, dict[str, Any]] = {}
        ld_by_sku: dict[str, dict[str, Any]] = {}

        for raw_block in _LD_JSON_RE.findall(page_html or ""):
            try:
                obj = json.loads(raw_block.strip())
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict) or obj.get("@type") != "Product":
                continue
            url = str(obj.get("url") or "").strip()
            sku = str(obj.get("sku") or "").strip()
            if url:
                ld_by_url[url] = obj
            if sku:
                ld_by_sku[sku] = obj

        cards: list[_PumaListingCard] = []
        seen_urls: set[str] = set()

        # Split listing HTML into product-item chunks
        chunks = re.split(r'(?=<div[^>]+class=["\'][^"\']*grid__item[^"\']*["\'])', page_html or "")
        for chunk in chunks:
            if "product-item__name" not in chunk:
                continue

            link_m = _ITEM_LINK_RE.search(chunk)
            if not link_m:
                continue
            prod_url = urljoin(f"{base_url}/", html_lib.unescape(link_m.group(1).strip()))
            if prod_url in seen_urls:
                continue

            title = html_lib.unescape(re.sub(r"<[^>]+>", " ", link_m.group(2))).strip()
            sku_m = _SKU_RE.search(chunk)
            pid_m = _PID_RE.search(chunk)
            sku = sku_m.group(1).strip() if sku_m else ""
            pid = pid_m.group(1).strip() if pid_m else ""

            ld_obj = ld_by_url.get(prod_url) or (ld_by_sku.get(sku) if sku else None) or {}
            if not title:
                title = str(ld_obj.get("name") or "").strip()
            if not title:
                continue

            final_m = _FINAL_PRICE_RE.search(chunk)
            old_m = _OLD_PRICE_RE.search(chunk)
            final_raw = (final_m.group(1) or final_m.group(2)) if final_m else None
            old_raw = (old_m.group(1) or old_m.group(2)) if old_m else None

            current_price = parse_try_price(final_raw)
            if current_price is None:
                offers = ld_obj.get("offers") or {}
                if isinstance(offers, dict):
                    current_price = parse_try_price(offers.get("price"))
            if current_price is None:
                continue

            original_price = parse_try_price(old_raw)
            if original_price is not None and original_price <= current_price:
                original_price = None

            image_url: str | None = None
            ld_img = ld_obj.get("image")
            if isinstance(ld_img, str) and ld_img.startswith("http"):
                image_url = ld_img
            elif isinstance(ld_img, list) and ld_img and isinstance(ld_img[0], str):
                image_url = ld_img[0]

            external_id = sku or pid or prod_url.rstrip("/").rsplit("/", 1)[-1].replace(".html", "")
            seen_urls.add(prod_url)
            cards.append(
                _PumaListingCard(
                    external_id=external_id,
                    title=title,
                    url=prod_url,
                    image_url=image_url,
                    current_price=current_price,
                    original_price=original_price,
                    category_path=category_path,
                )
            )

        return cards

    @staticmethod
    def extract_sizes_from_detail_html(detail_html: str) -> tuple[list[str], bool]:
        """Parse in-stock sizes from Puma Magento product page `spConfig` JSON."""
        available_sizes: list[str] = []
        is_critical_stock = False

        # Find `"spConfig": {` or `spConfig = {`
        for match in re.finditer(r'["\']?spConfig["\']?\s*[:=]\s*\{', detail_html or ""):
            brace_idx = detail_html.find("{", match.start())
            cfg = _extract_balanced_json_object(detail_html, brace_idx)
            if not cfg or not isinstance(cfg.get("attributes"), dict):
                continue

            for attr in cfg["attributes"].values():
                if not isinstance(attr, dict):
                    continue
                code = turkish_lower(str(attr.get("code") or ""))
                label = turkish_lower(str(attr.get("label") or ""))
                if code != "size" and "beden" not in label and "size" not in label:
                    continue

                for opt in attr.get("options") or []:
                    if not isinstance(opt, dict):
                        continue
                    in_stock_products = opt.get("products") or []
                    if not isinstance(in_stock_products, list) or len(in_stock_products) == 0:
                        continue
                    size_label = str(opt.get("label") or "").strip()
                    if size_label:
                        available_sizes.append(size_label)

            if available_sizes:
                break

        # Detect low-stock hint if only 1-2 sizes remain in stock on an outlet shoe
        if 0 < len(available_sizes) <= 2:
            is_critical_stock = True

        return available_sizes, is_critical_stock

    def fetch_products(self) -> list[ProductItem]:
        """Fetch discounted products and per-size availability from Puma TR Outlet."""
        paths = PUMA_OUTLET_PATHS.get(self.section) or PUMA_OUTLET_PATHS["all"]
        selected_paths = list(paths[: self.max_categories])

        candidates: list[_PumaListingCard] = []
        seen_urls: set[str] = set()
        cat_failures = 0

        for cat_idx, path in enumerate(selected_paths):
            if cat_idx > 0:
                self.sleep_jitter()

            cat_failed = False
            for page in range(1, self.max_pages_per_category + 1):
                if page > 1:
                    self.sleep_jitter()
                url = f"{self.base_url}{path}" if page == 1 else f"{self.base_url}{path}?p={page}"
                try:
                    page_html = self.http.get_text(url)
                except ScraperBlockedError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] Listing error on %s page %d: %s", self.brand, path, page, exc)
                    if page == 1:
                        cat_failed = True
                    break

                cards = self.parse_listing_html(page_html, base_url=self.base_url, category_path=path)
                if not cards:
                    break

                new_in_page = 0
                for card in cards:
                    if card.url in seen_urls:
                        continue
                    if not self._matches_section(card.title, card.url, path):
                        continue
                    if self.only_discounted and card.original_price is None:
                        continue
                    seen_urls.add(card.url)
                    candidates.append(card)
                    new_in_page += 1

                if new_in_page == 0:
                    break

            if cat_failed:
                cat_failures += 1

        if cat_failures == len(selected_paths) and not candidates:
            raise RuntimeError(f"All {len(selected_paths)} Puma Outlet categories failed")

        # Prioritize highest discount percentage when bounding detail page fetches
        def _discount_sort_key(card: _PumaListingCard) -> float:
            if card.original_price and card.original_price > card.current_price:
                return -float((card.original_price - card.current_price) / card.original_price)
            return 0.0

        candidates.sort(key=_discount_sort_key)

        products: list[ProductItem] = []
        detail_fetches = 0

        for card in candidates:
            available_sizes: list[str] = []
            is_critical_stock = False

            if self.fetch_variant_details:
                if detail_fetches >= self.max_detail_fetches:
                    break
                if detail_fetches > 0:
                    self.sleep_jitter()
                try:
                    detail_html = self.http.get_text(card.url)
                    detail_fetches += 1
                    available_sizes, is_critical_stock = self.extract_sizes_from_detail_html(detail_html)
                except ScraperBlockedError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[%s] Detail fetch failed for %s: %s", self.brand, card.url, exc)
                    continue

            if self.fetch_variant_details and not available_sizes:
                continue

            inferred_cat = infer_product_category(
                card.title,
                card.url,
                card.category_path,
                default=ProductCategory.SHOES,
            )

            products.append(
                ProductItem(
                    brand=self.brand,
                    external_id=card.external_id,
                    title=card.title,
                    category=inferred_cat,
                    url=card.url,
                    image_url=card.image_url,
                    current_price=card.current_price,
                    original_price=card.original_price,
                    currency="TRY",
                    available_sizes=available_sizes,
                    is_critical_stock=is_critical_stock,
                    raw_metadata={
                        "category_path": card.category_path,
                    },
                )
            )

        logger.info("[%s] Fetched %d discounted products", self.brand, len(products))
        return products
