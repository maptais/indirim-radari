"""DeFacto (`defacto.com.tr`) clothing discount scraper backed by SSR catalog `data-documents` JSON."""

from __future__ import annotations

import html as html_lib
import json
import logging
import re
from decimal import Decimal
from typing import Any, Literal

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

_KIDS_MARKERS = ("çocuk", "cocuk", "bebek", "baby", "kız çocuk", "erkek çocuk", "junior", "kids")
_DATA_DOCUMENTS_RE = re.compile(
    r"data-documents=(?:'(\{.*?\})'|\"(\{.*?\})\")",
    re.DOTALL,
)
_CRITICAL_STOCK_THRESHOLD = 3

DEFACTO_CATEGORY_PATHS: dict[str, tuple[str, ...]] = {
    "all": (
        "/indirim/erkek-net-yuzde-50",
        "/indirim/kadin-net-yuzde-50",
        "/erkek-indirimli-urunler",
        "/kadin-indirimli-urunler",
        "/indirim/erkek-firsat-fiyatlar",
        "/indirim/kadin-firsat-fiyatlar",
        "/erkek-outlet",
    ),
    "men": (
        "/indirim/erkek-net-yuzde-50",
        "/erkek-indirimli-urunler",
        "/indirim/erkek-firsat-fiyatlar",
        "/erkek-outlet",
    ),
    "women": (
        "/indirim/kadin-net-yuzde-50",
        "/kadin-indirimli-urunler",
        "/indirim/kadin-firsat-fiyatlar",
    ),
}


class DeFactoScraper(BaseScraper):
    """Standalone scraper for DeFacto Turkey (`www.defacto.com.tr`).

    Parses embedded `data-documents` JSON payloads directly from DeFacto's SSR
    discounted & outlet category pages, which include list/campaign prices and
    in-stock sizes in a single request per page.
    """

    brand = "defacto"
    engine_name = "defacto"
    default_run_mode = ScrapeRunMode.CLOUD

    def __init__(
        self,
        *,
        section: Literal["all", "men", "women"] = "all",
        only_discounted: bool = True,
        max_categories: int = 8,
        max_pages_per_category: int = 12,
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
        self.max_pages_per_category = max(1, max_pages_per_category)

        self.base_url = "https://www.defacto.com.tr"
        self.cdn_base_url = "https://dfcdn.defacto.com.tr/410"
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
    def extract_documents_from_html(page_html: str) -> list[dict[str, Any]]:
        """Extract non-empty `data-documents` JSON dicts from DeFacto listing HTML."""
        docs: list[dict[str, Any]] = []
        for match in _DATA_DOCUMENTS_RE.finditer(page_html or ""):
            raw_json = match.group(1) or match.group(2) or ""
            if not raw_json or '"LongCode":""' in raw_json:
                continue
            try:
                parsed = json.loads(html_lib.unescape(raw_json))
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and str(parsed.get("LongCode") or "").strip():
                docs.append(parsed)
        return docs

    @staticmethod
    def _infer_gender(doc: dict[str, Any], category_path: str) -> ProductGender:
        persona = str(doc.get("PersonaDescriptionName") or doc.get("Persona") or "")
        division = str(doc.get("Division") or doc.get("SubDivision") or "")
        title = str(doc.get("Name") or "")
        return infer_product_gender(
            persona,
            division,
            title,
            category_path,
            default=ProductGender.UNISEX,
        )

    def _matches_section(self, doc: dict[str, Any], category_path: str) -> bool:
        persona = str(doc.get("PersonaDescriptionName") or doc.get("Persona") or "")
        division = str(doc.get("Division") or doc.get("SubDivision") or "")
        title = str(doc.get("Name") or "")

        if is_kids_product(title, persona, division, category_path):
            return False
        if self.section == "all":
            return True

        gender = self._infer_gender(doc, category_path)
        if gender == ProductGender.UNISEX:
            return True
        if self.section == "men":
            return gender == ProductGender.MEN
        if self.section == "women":
            return gender == ProductGender.WOMEN
        return True

    def parse_document(
        self,
        doc: dict[str, Any],
        *,
        category_path: str = "",
    ) -> ProductItem | None:
        """Convert a DeFacto `data-documents` JSON object into a `ProductItem`."""
        title = str(doc.get("Name") or doc.get("ProductVariantName") or "").strip()
        long_code = str(doc.get("LongCode") or "").strip()
        seo_name = str(doc.get("SeoName") or "").strip().strip("/")
        variant_index = doc.get("ProductVariantIndex")
        if not title or not long_code:
            return None

        if not self._matches_section(doc, category_path):
            return None

        list_price = parse_try_price(doc.get("Price"))
        discounted_price = parse_try_price(doc.get("DiscountedPrice"))
        campaign_price = parse_try_price(doc.get("CampaignDiscountedPrice"))

        badge_obj = doc.get("CampaignBadge")
        badge_text: str | None = None
        if isinstance(badge_obj, dict):
            if campaign_price is None:
                campaign_price = parse_try_price(badge_obj.get("DiscountPrice"))
            desc = clean_promo_badge_text(str(badge_obj.get("DiscountBadgeDescription") or badge_obj.get("CampaignDescription") or ""))
            disc_amt = badge_obj.get("DiscountAmount")
            if desc and isinstance(disc_amt, (int, float)) and 0 < int(disc_amt) < 100:
                if "%" in desc:
                    badge_text = desc
                else:
                    badge_text = f"{desc} %{int(disc_amt)} İndirim"
            elif desc:
                badge_text = desc

        if not badge_text:
            for key in ("CampaignBadgeText", "CampaignText", "CampaignName", "BadgeText"):
                cand = clean_promo_badge_text(str(doc.get(key) or ""))
                if cand:
                    badge_text = cand
                    break

        # Storefront selling price (before cart/campaign discount)
        base_candidates = [p for p in (discounted_price, list_price) if p is not None]
        if base_candidates:
            current_price = min(base_candidates)
            max_base = max(base_candidates)
            original_price: Decimal | None = max_base if max_base > current_price else None
        elif campaign_price is not None:
            current_price = campaign_price
            original_price = None
        else:
            return None

        cart_price, promo_badge = resolve_cart_promo(
            current_price,
            explicit_cart_price=campaign_price if (campaign_price is not None and campaign_price < current_price) else None,
            promo_badge=badge_text,
        )

        if self.only_discounted and original_price is None and cart_price is None:
            return None

        available_sizes: list[str] = []
        is_critical_stock = False
        raw_sizes = doc.get("Sizes") or []
        saw_explicit_sizes = False
        if isinstance(raw_sizes, list):
            for size_entry in raw_sizes:
                if not isinstance(size_entry, dict):
                    continue
                size_name = str(size_entry.get("SizeName") or "").strip()
                if size_name:
                    saw_explicit_sizes = True
                stock_qty_raw = size_entry.get("StockQuantity")
                qty: int | None = None
                if stock_qty_raw is not None:
                    try:
                        qty = int(stock_qty_raw)
                    except (TypeError, ValueError):
                        qty = None
                if qty is not None and qty <= 0:
                    continue
                if qty is not None and 0 < qty <= _CRITICAL_STOCK_THRESHOLD:
                    is_critical_stock = True
                if size_name:
                    available_sizes.append(size_name)

        if not available_sizes:
            fallback_size = str(doc.get("SizeName") or "").strip()
            if fallback_size:
                available_sizes.append(fallback_size)

        # Only drop as sold out when listing explicitly provided sizes and all had qty <= 0.
        # Keep products whose listing omitted size metadata (`available_sizes=[]`).
        if not available_sizes and saw_explicit_sizes:
            return None

        if 0 < len(available_sizes) <= 1:
            is_critical_stock = True

        if seo_name and variant_index:
            product_url = f"{self.base_url}/{seo_name}-{variant_index}"
        elif seo_name:
            product_url = f"{self.base_url}/{seo_name}"
        else:
            product_url = f"{self.base_url}/statik/urun-{long_code}"

        picture_name = str(doc.get("PictureName") or "").strip()
        image_url: str | None = None
        if picture_name:
            if picture_name.startswith("http"):
                image_url = picture_name
            else:
                image_url = f"{self.cdn_base_url}/{picture_name.lstrip('/')}"

        cat_lvl3 = doc.get("CategoriesLvl3") or {}
        cat_lvl2 = doc.get("CategoriesLvl2") or {}
        inferred_cat = infer_product_category(
            title,
            str(doc.get("CategoryName") or ""),
            str(cat_lvl3.get("CategoryName") or "") if isinstance(cat_lvl3, dict) else "",
            str(cat_lvl2.get("CategoryName") or "") if isinstance(cat_lvl2, dict) else "",
            str(doc.get("BreadCrumb") or ""),
            default=ProductCategory.TOPS,
        )

        inferred_gender = self._infer_gender(doc, category_path)
        external_id = str(variant_index or long_code).strip()

        return ProductItem(
            brand=self.brand,
            external_id=external_id,
            title=title,
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
                "long_code": long_code,
                "color_name": doc.get("ColorName"),
                "category_name": doc.get("CategoryName"),
                "category_path": category_path,
            },
        )

    def fetch_products(self) -> list[ProductItem]:
        """Fetch discounted products across DeFacto sale listing pages."""
        paths = DEFACTO_CATEGORY_PATHS.get(self.section) or DEFACTO_CATEGORY_PATHS["all"]
        selected_paths = list(paths[: self.max_categories])

        products_by_id: dict[str, ProductItem] = {}
        cat_failures = 0

        for cat_idx, path in enumerate(selected_paths):
            if self.is_budget_exceeded():
                logger.warning(
                    "[%s] Soft time budget reached (%d products kept); stopping.",
                    self.brand,
                    len(products_by_id),
                )
                break
            if cat_idx > 0:
                self.sleep_jitter()

            cat_failed = False
            consecutive_empty_pages = 0
            for page in range(1, self.max_pages_per_category + 1):
                if self.is_budget_exceeded():
                    break
                if page > 1:
                    self.sleep_jitter()

                sep = "&" if "?" in path else "?"
                url = f"{self.base_url}{path}{sep}page={page}"
                try:
                    page_html = self.http.get_text(url)
                except ScraperBlockedError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] Listing error on %s page %d: %s", self.brand, path, page, exc)
                    if page == 1:
                        cat_failed = True
                    break

                docs = self.extract_documents_from_html(page_html)
                if not docs:
                    break

                new_in_page = 0
                for doc in docs:
                    item = self.parse_document(doc, category_path=path)
                    if item is not None and item.external_id not in products_by_id:
                        products_by_id[item.external_id] = item
                        new_in_page += 1

                if new_in_page == 0:
                    consecutive_empty_pages += 1
                    if consecutive_empty_pages >= 2:
                        break
                else:
                    consecutive_empty_pages = 0

            if cat_failed:
                cat_failures += 1

        if cat_failures == len(selected_paths) and not products_by_id:
            raise RuntimeError(f"All {len(selected_paths)} DeFacto categories failed")

        logger.info("[%s] Fetched %d discounted products", self.brand, len(products_by_id))
        return list(products_by_id.values())
