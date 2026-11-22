"""H&M Turkey (`www2.hm.com/tr_tr`) scraper backed by H&M Search Services JSON API."""

from __future__ import annotations

import logging
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

_CRITICAL_STOCK_THRESHOLD = 2

HM_CATEGORY_SPECS: dict[str, tuple[tuple[str, str, str | None], ...]] = {
    "all": (
        ("/men/sale/view-all", "men_sale", None),
        ("/ladies/sale/view-all", "ladies_sale", None),
        ("/men/deals/deal", "men_deals", None),
        ("/ladies/deals/deal", "ladies_deals", None),
        ("/men/shop-by-product/view-all", "men_all", "sale:true||oldSale:true"),
        ("/ladies/shop-by-product/view-all", "ladies_all", "sale:true||oldSale:true"),
    ),
    "men": (
        ("/men/sale/view-all", "men_sale", None),
        ("/men/deals/deal", "men_deals", None),
        ("/men/shop-by-product/view-all", "men_all", "sale:true||oldSale:true"),
    ),
    "women": (
        ("/ladies/sale/view-all", "ladies_sale", None),
        ("/ladies/deals/deal", "ladies_deals", None),
        ("/ladies/shop-by-product/view-all", "ladies_all", "sale:true||oldSale:true"),
    ),
}


class HMScraper(BaseScraper):
    """Standalone scraper for H&M Turkey (`api.hm.com/search-services/v1/tr_tr`).

    Reads structured listing JSON directly from H&M's search-services API, which
    includes both prices (`whitePrice` / `redPrice`) and per-size stock levels
    without triggering `www2.hm.com` PDP Akamai challenges.
    """

    brand = "hm"
    engine_name = "hm"
    default_run_mode = ScrapeRunMode.CLOUD

    def __init__(
        self,
        *,
        section: Literal["all", "men", "women"] = "all",
        only_discounted: bool = True,
        max_categories: int = 4,
        max_pages_per_category: int = 3,
        page_size: int = 48,
        max_retries: int = 2,
        impersonate: str | None = None,
        timeout_seconds: int = 20,
        min_jitter_seconds: float = 0.4,
        max_jitter_seconds: float = 1.0,
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
        self.page_size = max(12, min(72, page_size))

        self.web_base_url = "https://www2.hm.com"
        self.api_url = "https://api.hm.com/search-services/v1/tr_tr/listing/resultpage"
        self.http = HttpClient(
            self.brand,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            impersonate=impersonate,
            default_headers={
                "Origin": self.web_base_url,
                "Referer": f"{self.web_base_url}/tr_tr/",
            },
        )

    def close(self) -> None:
        self.http.close()

    @staticmethod
    def _extract_prices(raw: dict[str, Any]) -> tuple[Decimal | None, Decimal | None]:
        """Parse `(current_price, original_price)` from H&M's `prices` array."""
        prices = raw.get("prices") or []
        white_price: Decimal | None = None
        sale_price: Decimal | None = None
        all_prices: list[Decimal] = []

        if isinstance(prices, list):
            for entry in prices:
                if not isinstance(entry, dict):
                    continue
                val = parse_try_price(entry.get("price") or entry.get("minPrice") or entry.get("formattedPrice"))
                if val is None:
                    continue
                all_prices.append(val)
                ptype = str(entry.get("priceType") or "").strip().lower()
                if ptype == "whiteprice":
                    white_price = val
                elif ptype in {"redprice", "yellowprice", "memberprice", "saleprice"}:
                    if sale_price is None or val < sale_price:
                        sale_price = val

        if not all_prices:
            return None, None

        if sale_price is not None:
            current_price = sale_price
            original_price = white_price if (white_price is not None and white_price > sale_price) else None
            if original_price is None and max(all_prices) > sale_price:
                original_price = max(all_prices)
            return current_price, original_price

        current_price = min(all_prices)
        max_p = max(all_prices)
        original_price = max_p if max_p > current_price else None
        return current_price, original_price

    @staticmethod
    def _extract_sizes(raw: dict[str, Any]) -> tuple[list[str], bool]:
        """Extract in-stock sizes and critical-stock indicator from H&M product JSON."""
        available_sizes: list[str] = []
        is_critical_stock = False

        availability = raw.get("availability") or {}
        stock_state = str(availability.get("stockState") or "").strip().lower()
        if stock_state in {"fewpiecesleft", "lowstock"}:
            is_critical_stock = True

        for entry in raw.get("sizes") or []:
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("label") or entry.get("name") or "").strip()
            if not label:
                continue

            stock_raw = entry.get("stock")
            if stock_raw is not None:
                try:
                    stock_qty = int(stock_raw)
                except (TypeError, ValueError):
                    stock_qty = 1
                if stock_qty <= 0:
                    continue
                if 0 < stock_qty <= _CRITICAL_STOCK_THRESHOLD:
                    is_critical_stock = True

            available_sizes.append(label)

        if 0 < len(available_sizes) <= 1:
            is_critical_stock = True

        return available_sizes, is_critical_stock

    def parse_product(
        self,
        raw: dict[str, Any],
        *,
        category_id: str = "",
    ) -> ProductItem | None:
        """Convert an H&M Search Services product dict into a `ProductItem`."""
        if raw.get("removed") or raw.get("isOnline") is False:
            return None

        availability = raw.get("availability") or {}
        if availability.get("comingSoon"):
            return None

        prod_id = str(raw.get("id") or "").strip()
        title = str(raw.get("productName") or "").strip()
        rel_url = str(raw.get("url") or "").strip()
        if not prod_id or not title or not rel_url:
            return None

        main_cat = str(raw.get("mainCatCode") or "")
        if is_kids_product(title, main_cat, category_id):
            return None

        inferred_gender = infer_product_gender(
            title,
            main_cat,
            category_id,
            rel_url,
            default=ProductGender.UNISEX,
        )
        if self.section == "men" and inferred_gender == ProductGender.WOMEN:
            return None
        if self.section == "women" and inferred_gender == ProductGender.MEN:
            return None

        current_price, original_price = self._extract_prices(raw)
        # Fallback to top-level redPrice/priorPrice/regularPrice if present
        if original_price is None and current_price is not None:
            for orig_key in ("priorPrice", "regularPrice", "whitePrice"):
                cand = parse_try_price(raw.get(orig_key))
                if cand is not None and cand > current_price:
                    original_price = cand
                    break

        if current_price is None:
            return None

        # Extract H&M campaign/marker badges (e.g. "Sepette %20", "2. Ürüne %50", member deal)
        badge_candidates: list[str] = []
        for key in ("promotionalMarkerText", "marketingMarkerText", "clubPriceLabel"):
            txt = clean_promo_badge_text(str(raw.get(key) or ""))
            if txt:
                badge_candidates.append(txt)
        for marker in raw.get("markers") or []:
            if isinstance(marker, dict):
                txt = clean_promo_badge_text(str(marker.get("text") or marker.get("label") or ""))
                if txt and any(kw in turkish_lower(txt) for kw in ("sepet", "%", "indirim", "ürün", "urun", "al ", "öde", "fırsat", "üye")):
                    badge_candidates.append(txt)
        for attr in raw.get("sellingAttributes") or []:
            txt = clean_promo_badge_text(str(attr or ""))
            if txt and any(kw in turkish_lower(txt) for kw in ("sepet", "%", "indirim", "ürün", "urun", "al ", "öde", "fırsat")):
                badge_candidates.append(txt)

        chosen_badge = badge_candidates[0] if badge_candidates else None
        cart_price, promo_badge = resolve_cart_promo(
            current_price,
            promo_badge=chosen_badge,
        )

        if self.only_discounted and original_price is None and cart_price is None:
            return None

        available_sizes, is_critical_stock = self._extract_sizes(raw)
        if not available_sizes:
            return None

        image_url: str | None = None
        for candidate in (raw.get("productImage"), raw.get("modelImage")):
            if isinstance(candidate, str) and candidate.startswith("http"):
                image_url = candidate
                break
        if image_url is None:
            for img in raw.get("images") or []:
                if isinstance(img, dict) and isinstance(img.get("url"), str) and img["url"].startswith("http"):
                    image_url = img["url"]
                    break

        # Map H&M mainCatCode words into readable hints for category inference
        cat_hint = main_cat.replace("_", " ")
        inferred_cat = infer_product_category(
            title,
            cat_hint,
            default=ProductCategory.TOPS,
        )

        return ProductItem(
            brand=self.brand,
            external_id=prod_id,
            title=title,
            category=inferred_cat,
            gender=inferred_gender,
            url=urljoin(f"{self.web_base_url}/", rel_url.lstrip("/")),
            image_url=image_url,
            current_price=current_price,
            original_price=original_price,
            cart_price=cart_price,
            promo_badge=promo_badge,
            currency="TRY",
            available_sizes=available_sizes,
            is_critical_stock=is_critical_stock,
            raw_metadata={
                "main_cat_code": main_cat,
                "color_name": raw.get("colorName"),
                "category_id": category_id,
            },
        )

    def fetch_products(self) -> list[ProductItem]:
        """Fetch discounted H&M Turkey products from Search Services JSON API."""
        specs = HM_CATEGORY_SPECS.get(self.section) or HM_CATEGORY_SPECS["all"]
        selected_specs = list(specs[: self.max_categories])

        products_by_id: dict[str, ProductItem] = {}
        failures = 0

        for spec_idx, (page_id, category_id, filters_param) in enumerate(selected_specs):
            if spec_idx > 0:
                self.sleep_jitter()

            spec_failed = False
            for page in range(1, self.max_pages_per_category + 1):
                if page > 1:
                    self.sleep_jitter()

                params: dict[str, Any] = {
                    "pageSource": "PLP",
                    "page": page,
                    "sort": "RELEVANCE",
                    "pageId": page_id,
                    "page-size": self.page_size,
                    "categoryId": category_id,
                    "touchPoint": "DESKTOP",
                    "skipStockCheck": "false",
                }
                if filters_param:
                    params["filters"] = filters_param

                try:
                    payload = self.http.get_json(self.api_url, params=params)
                except ScraperBlockedError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] API error on %s page %d: %s", self.brand, category_id, page, exc)
                    if page == 1:
                        spec_failed = True
                    break

                plp_list = payload.get("plpList") or {}
                raw_products = plp_list.get("productList") or []
                if not raw_products:
                    break

                for raw_prod in raw_products:
                    if not isinstance(raw_prod, dict):
                        continue
                    item = self.parse_product(raw_prod, category_id=category_id)
                    if item is not None and item.external_id not in products_by_id:
                        products_by_id[item.external_id] = item

                pagination = payload.get("pagination") or {}
                total_pages = int(pagination.get("totalPages") or 1)
                if page >= total_pages:
                    break

            if spec_failed:
                failures += 1

        if failures == len(selected_specs) and not products_by_id:
            raise RuntimeError(f"All {len(selected_specs)} H&M category queries failed")

        logger.info("[%s] Fetched %d discounted products", self.brand, len(products_by_id))
        return list(products_by_id.values())
