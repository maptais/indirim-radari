"""In Street (`instreet.com.tr`) shoe & sportswear discount scraper."""

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
        max_pages: int = 15,
        max_retries: int = 2,
        impersonate: str | None = None,
        timeout_seconds: int = 15,
        min_jitter_seconds: float = 0.15,
        max_jitter_seconds: float = 0.45,
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

    @staticmethod
    def _infer_gender(raw: dict[str, Any], full_title: str) -> ProductGender:
        breadcrumb_names = " ".join(
            str(b.get("name") or "")
            for b in (raw.get("breadcrumb") or [])
            if isinstance(b, dict)
        )
        return infer_product_gender(
            str(raw.get("gender") or ""),
            full_title,
            breadcrumb_names,
            str(raw.get("relativeUrl") or ""),
            default=ProductGender.UNISEX,
        )

    def _matches_section(self, raw: dict[str, Any], full_title: str) -> bool:
        breadcrumb_names = " ".join(
            str(b.get("name") or "")
            for b in (raw.get("breadcrumb") or [])
            if isinstance(b, dict)
        )
        if is_kids_product(full_title, breadcrumb_names, str(raw.get("relativeUrl") or "")):
            return False
        if self.section == "all":
            return True

        gender = self._infer_gender(raw, full_title)
        if gender == ProductGender.UNISEX:
            return True
        if self.section == "men":
            return gender == ProductGender.MEN
        if self.section == "women":
            return gender == ProductGender.WOMEN
        return True

    @staticmethod
    def _extract_cart_promo(
        raw: dict[str, Any],
        current_price: Decimal | None,
    ) -> tuple[Decimal | None, str | None]:
        explicit_cart: Decimal | None = None
        for key in ("thirdPrice", "basketPrice", "cartPrice", "campaignPrice", "specialPrice", "discountedPrice"):
            cand = parse_try_price(raw.get(key))
            if cand is not None and current_price is not None and cand < current_price:
                explicit_cart = cand
                break

        badge_candidates: list[str] = []
        for key in ("thirdPriceLabel", "campaignText", "campaignName", "promotionText", "promoBadge"):
            txt = clean_promo_badge_text(str(raw.get(key) or ""))
            if txt:
                badge_candidates.append(txt)

        # Inspect animatedBadgeList / badges, filtering out non-promo tags like "Kargo Bedava" / "Yeni Sezon"
        for badge_list_key in ("animatedBadgeList", "badges", "campaignBadges"):
            for entry in raw.get(badge_list_key) or []:
                raw_txt = (
                    str(entry.get("badgeText") or entry.get("text") or entry.get("title") or "")
                    if isinstance(entry, dict)
                    else str(entry or "")
                )
                txt = clean_promo_badge_text(raw_txt)
                if not txt:
                    continue
                low = turkish_lower(txt)
                if any(
                    kw in low
                    for kw in ("sepet", "%", "indirim", "ürün", "urun", "al ", "öde", "ode", "fırsat", "firsat", "kampanya")
                ):
                    badge_candidates.append(txt)

        chosen_badge = badge_candidates[0] if badge_candidates else None
        for cand in badge_candidates:
            low = turkish_lower(cand)
            if "sepet" in low or "%" in low:
                chosen_badge = cand
                break

        return resolve_cart_promo(
            current_price,
            explicit_cart_price=explicit_cart,
            promo_badge=chosen_badge,
        )

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

        cart_price, promo_badge = self._extract_cart_promo(raw, current_price)

        if self.only_discounted and original_price is None and cart_price is None:
            return None

        relative_url = str(raw.get("relativeUrl") or "").strip()
        if not relative_url:
            return None
        product_url = urljoin(f"{self.base_url}/", relative_url)

        external_id = str(raw.get("sku") or raw.get("productId") or "").strip()
        if not external_id:
            return None

        raw_options = raw.get("options") or []
        available_sizes: list[str] = []
        is_critical_stock = False
        for opt in raw_options:
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

        # Only discard as out-of-stock if options were provided and every single size is OOS
        if not available_sizes and raw_options:
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

        inferred_gender = self._infer_gender(raw, full_title)

        return ProductItem(
            brand=self.brand,
            external_id=external_id,
            title=full_title,
            category=inferred_cat,
            gender=inferred_gender,
            url=product_url,
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
                "brand_name": brand_name,
                "breadcrumbs": breadcrumbs,
                "discount_rate_label": raw.get("discountRate"),
            },
        )

    def fetch_products(self) -> list[ProductItem]:
        """Fetch discounted products with live size stock from In Street's JSON list API."""
        products_by_id: dict[str, ProductItem] = {}
        pages_to_scan = self.max_pages

        gender_param: str | None = None
        if self.section == "men":
            gender_param = "erkek"
        elif self.section == "women":
            gender_param = "kadin"

        page_failures = 0
        consecutive_empty_pages = 0
        for page in range(1, pages_to_scan + 1):
            if self.is_budget_exceeded():
                logger.warning(
                    "[%s] Soft time budget reached at page %d (%d products kept).",
                    self.brand,
                    page,
                    len(products_by_id),
                )
                break
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

            new_in_page = 0
            for raw_prod in raw_products:
                if not isinstance(raw_prod, dict):
                    continue
                item = self.parse_product(raw_prod)
                if item is not None and item.external_id not in products_by_id:
                    products_by_id[item.external_id] = item
                    new_in_page += 1

            if new_in_page == 0:
                consecutive_empty_pages += 1
                if consecutive_empty_pages >= 2:
                    break
            else:
                consecutive_empty_pages = 0

            pagination = payload.get("pagination") or {}
            total_pages = int(pagination.get("totalPages") or 1)
            if page >= total_pages:
                break

        if page_failures > 0 and not products_by_id:
            raise RuntimeError(f"In Street listing failed across {page_failures} page(s)")

        logger.info("[%s] Fetched %d discounted products", self.brand, len(products_by_id))
        return list(products_by_id.values())
