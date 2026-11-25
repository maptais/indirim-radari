"""Mavi (`mavi.com`) clothing & denim scraper backed by Angular Spartacus `ng-state` TransferState."""

from __future__ import annotations

import json
import logging
import re
from decimal import Decimal, ROUND_HALF_UP
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
from ...core.filters import extract_polyester_percent, turkish_lower
from ...core.http import (
    HttpClient,
    clean_promo_badge_text,
    infer_product_category,
    infer_product_gender,
    interleave_by_gender,
    is_kids_product,
    parse_try_price,
    resolve_cart_promo,
)

logger = logging.getLogger(__name__)

_CRITICAL_STOCK_THRESHOLD = 3
_NG_STATE_RE = re.compile(
    r'<script[^>]+id=["\']ng-state["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE,
)

MAVI_CATEGORY_PATHS: dict[str, tuple[str, ...]] = {
    "all": (
        "/erkek/c/2?q=:sale",
        "/kadin/c/1?q=:sale",
        "/outlet/c/4?q=:sale:gender:Erkek",
        "/outlet/c/4?q=:sale:gender:Kad%C4%B1n",
    ),
    "men": (
        "/erkek/c/2?q=:sale",
        "/outlet/c/4?q=:sale:gender:Erkek",
    ),
    "women": (
        "/kadin/c/1?q=:sale",
        "/outlet/c/4?q=:sale:gender:Kad%C4%B1n",
    ),
}


def _strip_html(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def decode_spa_transfer_state(transfer_state: dict[str, Any]) -> Any:
    """Decode Mavi's Angular/Spartacus v2 compressed `__spaTransferState__` graph."""
    values = transfer_state.get("values") or []
    schemas = transfer_state.get("schemas") or []
    memo: dict[int, Any] = {}

    def _resolve(node: Any) -> Any:
        if isinstance(node, str) and node.startswith("~"):
            try:
                idx = int(node[1:], 36)
            except ValueError:
                return node
            if idx < 0 or idx >= len(values):
                return None
            if idx not in memo:
                memo[idx] = None
                memo[idx] = _resolve(values[idx])
            return memo[idx]

        if isinstance(node, list) and node:
            tag = node[0]
            if tag == 0:
                return [_resolve(item) for item in node[1:]]
            if isinstance(tag, int) and tag < 0:
                schema_idx = -tag - 1
                if 0 <= schema_idx < len(schemas):
                    schema = schemas[schema_idx]
                    return {k: _resolve(v) for k, v in zip(schema, node[1:])}
        return node

    return _resolve(transfer_state.get("root"))


class MaviScraper(BaseScraper):
    """Standalone scraper for Mavi Turkey (`www.mavi.com`).

    Decodes Angular SSR `<script id="ng-state">` (`__spaTransferState__`) on
    outlet & discount-sorted listings and fetches per-size stock + exact fabric
    composition (`fabricDefinition`) from product detail pages.
    """

    brand = "mavi"
    engine_name = "mavi"
    default_run_mode = ScrapeRunMode.CLOUD

    def __init__(
        self,
        *,
        section: Literal["all", "men", "women"] = "all",
        only_discounted: bool = True,
        max_categories: int = 4,
        max_pages_per_category: int = 10,
        max_detail_fetches: int = 24,
        fetch_variant_details: bool = True,
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
        self.max_detail_fetches = max(0, max_detail_fetches)
        self.fetch_variant_details = fetch_variant_details

        self.base_url = "https://www.mavi.com"
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
    def extract_decoded_state(page_html: str) -> Any:
        """Parse `<script id="ng-state">` and decode `__spaTransferState__`."""
        match = _NG_STATE_RE.search(page_html or "")
        if not match:
            return None
        try:
            state = json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
        if not isinstance(state, dict):
            return None
        ts = state.get("__spaTransferState__")
        if isinstance(ts, dict) and "root" in ts and "values" in ts and "schemas" in ts:
            return decode_spa_transfer_state(ts)
        return state

    @classmethod
    def extract_listing_products(cls, page_html: str) -> list[dict[str, Any]]:
        """Extract raw SAP Commerce product dicts from decoded Mavi listing TransferState."""
        decoded = cls.extract_decoded_state(page_html)
        if decoded is None:
            return []

        def _find_products(node: Any, depth: int = 0) -> list[dict[str, Any]] | None:
            if depth > 7:
                return None
            if isinstance(node, dict):
                prods = node.get("products")
                if isinstance(prods, list) and prods and isinstance(prods[0], dict):
                    return [p for p in prods if isinstance(p, dict)]
                for val in node.values():
                    found = _find_products(val, depth + 1)
                    if found:
                        return found
            elif isinstance(node, list):
                for item in node:
                    found = _find_products(item, depth + 1)
                    if found:
                        return found
            return None

        return _find_products(decoded) or []

    @classmethod
    def extract_detail_metadata(
        cls,
        detail_html: str,
    ) -> tuple[list[str], bool, str | None, int | None, Decimal | None, Decimal | None]:
        """Extract sizes, critical-stock flag, fabric, and optional refined prices from Mavi PDP."""
        decoded = cls.extract_decoded_state(detail_html)
        if decoded is None:
            return [], False, None, None, None, None

        def _find_pdp(node: Any, depth: int = 0) -> dict[str, Any] | None:
            if depth > 7:
                return None
            if isinstance(node, dict):
                if any(k in node for k in ("allSizeVariants", "fabricDefinition", "baseOptions")) and "code" in node:
                    return node
                for val in node.values():
                    found = _find_pdp(val, depth + 1)
                    if found:
                        return found
            elif isinstance(node, list):
                for item in node:
                    found = _find_pdp(item, depth + 1)
                    if found:
                        return found
            return None

        pdp = _find_pdp(decoded) or {}
        available_sizes: list[str] = []
        is_critical_stock = False

        # 1. Inspect `allSizeVariants`
        for var in pdp.get("allSizeVariants") or []:
            if not isinstance(var, dict):
                continue
            stock_obj = var.get("stock") if isinstance(var.get("stock"), dict) else {}
            status = str(stock_obj.get("stockLevelStatus") or var.get("stockLevelStatus") or "").lower()
            stock_level = stock_obj.get("stockLevel")
            in_stock = var.get("inStock")
            if status == "outofstock" or in_stock is False:
                continue
            if isinstance(stock_level, (int, float)) and int(stock_level) <= 0 and status != "instock":
                continue
            if isinstance(stock_level, (int, float)) and 0 < int(stock_level) <= _CRITICAL_STOCK_THRESHOLD:
                is_critical_stock = True
            if status == "lowstock":
                is_critical_stock = True

            size_val = str(
                var.get("size")
                or var.get("sizeName")
                or var.get("name")
                or var.get("value")
                or ""
            ).strip()
            length_val = str(var.get("length") or "").strip()
            if size_val and length_val and "/" not in size_val:
                label = f"{size_val}/{length_val}"
            else:
                label = size_val
            if label and label not in available_sizes:
                available_sizes.append(label)

        # 2. Fallback to `baseOptions[*].options`
        if not available_sizes:
            for base_opt in pdp.get("baseOptions") or []:
                if not isinstance(base_opt, dict):
                    continue
                for opt in base_opt.get("options") or []:
                    if not isinstance(opt, dict):
                        continue
                    stock_obj = opt.get("stock") if isinstance(opt.get("stock"), dict) else {}
                    status = str(stock_obj.get("stockLevelStatus") or "").lower()
                    stock_level = stock_obj.get("stockLevel")
                    if status == "outofstock":
                        continue
                    if isinstance(stock_level, (int, float)) and int(stock_level) <= 0 and status != "instock":
                        continue
                    if isinstance(stock_level, (int, float)) and 0 < int(stock_level) <= _CRITICAL_STOCK_THRESHOLD:
                        is_critical_stock = True
                    for qual in opt.get("variantOptionQualifiers") or []:
                        if isinstance(qual, dict) and str(qual.get("qualifier") or "").lower() in {"size", "beden", "waist"}:
                            val = str(qual.get("value") or "").strip()
                            if val and val not in available_sizes:
                                available_sizes.append(val)

        if 0 < len(available_sizes) <= 2:
            is_critical_stock = True

        fabric_raw = pdp.get("fabricDefinition")
        fabric_info = _strip_html(str(fabric_raw)) if isinstance(fabric_raw, str) and fabric_raw.strip() else None
        polyester_pct = extract_polyester_percent(fabric_info)

        pdp_current, pdp_original = cls._extract_prices(pdp)
        return available_sizes, is_critical_stock, fabric_info, polyester_pct, pdp_current, pdp_original

    @staticmethod
    def _extract_prices(raw: dict[str, Any]) -> tuple[Decimal | None, Decimal | None]:
        """Extract `(current_price, original_price)` from Mavi SAP Commerce price fields."""
        def _val(obj: Any) -> Decimal | None:
            if isinstance(obj, dict):
                return parse_try_price(obj.get("value") or obj.get("formattedValue"))
            return parse_try_price(obj)

        p_price = _val(raw.get("price"))
        p_sale = _val(raw.get("salePrice"))
        p_store = _val(raw.get("storeSalePrice"))
        p_orig = _val(raw.get("originalPrice") or raw.get("previousPrice") or raw.get("strikeThroughPrice"))

        candidates = [p for p in (p_sale, p_price, p_store, p_orig) if p is not None]
        if not candidates:
            return None, None

        current_price = min(candidates)
        max_price = max(candidates)
        original_price: Decimal | None = max_price if max_price > current_price else None

        # If Mavi reports a positive `discountRate` while price & storeSalePrice are equal, derive original price
        discount_rate = raw.get("discountRate")
        if original_price is None and isinstance(discount_rate, (int, float)) and 0 < discount_rate < 95:
            ratio = Decimal("1") - (Decimal(str(discount_rate)) / Decimal("100"))
            if ratio > Decimal("0"):
                original_price = (current_price / ratio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        return current_price, original_price

    @staticmethod
    def _extract_image_url(raw: dict[str, Any]) -> str | None:
        for img in raw.get("images") or []:
            if isinstance(img, dict):
                url = str(img.get("url") or "").strip()
                if url:
                    return f"https:{url}" if url.startswith("//") else url
        main_img = raw.get("allMainImage")
        if isinstance(main_img, str) and main_img.strip():
            return f"https:{main_img}" if main_img.startswith("//") else main_img
        return None

    @staticmethod
    def _infer_gender(raw: dict[str, Any], category_path: str) -> ProductGender:
        return infer_product_gender(
            str(raw.get("gender") or ""),
            str(raw.get("productTitle") or ""),
            str(raw.get("name") or ""),
            str(raw.get("url") or ""),
            category_path,
            default=ProductGender.UNISEX,
        )

    def _matches_section(self, raw: dict[str, Any], category_path: str) -> bool:
        if is_kids_product(
            str(raw.get("productTitle") or ""),
            str(raw.get("name") or ""),
            str(raw.get("gender") or ""),
            category_path,
        ):
            return False
        if self.section == "all":
            return True
        gender = self._infer_gender(raw, category_path)
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
        def _val(obj: Any) -> Decimal | None:
            if isinstance(obj, dict):
                return parse_try_price(obj.get("value") or obj.get("formattedValue"))
            return parse_try_price(obj)

        explicit_cart: Decimal | None = None
        for key in ("basketPrice", "cartPrice", "campaignPrice", "campaignDiscountedPrice"):
            cand = _val(raw.get(key))
            if cand is not None and current_price is not None and cand < current_price:
                explicit_cart = cand
                break

        badge_candidates: list[str] = []
        for key in ("promotionText", "campaignText", "discountBadge", "promoBadge", "badgeText"):
            txt = clean_promo_badge_text(str(raw.get(key) or ""))
            if txt:
                badge_candidates.append(txt)
        for list_key in ("potentialPromotions", "campaigns", "badges", "promotions"):
            for entry in raw.get(list_key) or []:
                raw_txt = (
                    str(entry.get("description") or entry.get("title") or entry.get("name") or entry.get("text") or "")
                    if isinstance(entry, dict)
                    else str(entry or "")
                )
                txt = clean_promo_badge_text(raw_txt)
                if txt and any(
                    kw in turkish_lower(txt)
                    for kw in ("sepet", "%", "indirim", "ürün", "urun", "al ", "öde", "ode", "kampanya")
                ):
                    badge_candidates.append(txt)

        chosen_badge = badge_candidates[0] if badge_candidates else None
        for cand in badge_candidates:
            if "sepet" in turkish_lower(cand) or "%" in cand:
                chosen_badge = cand
                break

        return resolve_cart_promo(
            current_price,
            explicit_cart_price=explicit_cart,
            promo_badge=chosen_badge,
        )

    def parse_listing_product(
        self,
        raw: dict[str, Any],
        *,
        category_path: str = "",
        detail_html: str | None = None,
    ) -> ProductItem | None:
        """Convert a Mavi TransferState product dict (+ optional PDP HTML) into a `ProductItem`."""
        code = str(raw.get("code") or raw.get("baseProduct") or "").strip()
        rel_url = str(raw.get("url") or "").strip()
        prod_title = str(raw.get("productTitle") or "").strip()
        prod_name = str(raw.get("name") or "").strip()
        if prod_title and prod_name and turkish_lower(prod_title) not in turkish_lower(prod_name):
            title = f"{prod_title} {prod_name}"
        else:
            title = prod_name or prod_title

        if not code or not title or not rel_url:
            return None

        if not self._matches_section(raw, category_path):
            return None

        current_price, original_price = self._extract_prices(raw)
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
                pdp_cur,
                pdp_orig,
            ) = self.extract_detail_metadata(detail_html)
            if pdp_cur is not None and (current_price is None or pdp_cur < current_price):
                current_price = pdp_cur
            if pdp_orig is not None and (original_price is None or pdp_orig > original_price):
                original_price = pdp_orig

        if current_price is None:
            return None
        if original_price is not None and original_price <= current_price:
            original_price = None

        cart_price, promo_badge = self._extract_cart_promo(raw, current_price)
        if self.only_discounted and original_price is None and cart_price is None:
            return None

        if self.fetch_variant_details and detail_html is not None and not available_sizes:
            return None

        inferred_cat = infer_product_category(
            title,
            str(raw.get("fit") or ""),
            rel_url,
            category_path,
            default=ProductCategory.BOTTOMS if raw.get("isJean") else ProductCategory.TOPS,
        )

        inferred_gender = self._infer_gender(raw, category_path)

        return ProductItem(
            brand=self.brand,
            external_id=code,
            title=title,
            category=inferred_cat,
            gender=inferred_gender,
            url=urljoin(f"{self.base_url}/", rel_url.lstrip("/")),
            image_url=self._extract_image_url(raw),
            current_price=current_price,
            original_price=original_price,
            cart_price=cart_price,
            promo_badge=promo_badge,
            currency="TRY",
            available_sizes=available_sizes,
            is_critical_stock=is_critical_stock,
            fabric_info=fabric_info,
            polyester_percent=polyester_pct,
            raw_metadata={
                "code": code,
                "gender": raw.get("gender"),
                "category_path": category_path,
            },
        )

    def fetch_products(self) -> list[ProductItem]:
        """Fetch discounted Mavi products from outlet & discount-sorted pages."""
        paths = MAVI_CATEGORY_PATHS.get(self.section) or MAVI_CATEGORY_PATHS["all"]
        selected_paths = list(paths[: self.max_categories])
        effective_max_pages = (
            min(self.max_pages_per_category, 8)
            if self.section == "all" and len(selected_paths) >= 4
            else self.max_pages_per_category
        )

        candidates: list[tuple[dict[str, Any], str, float]] = []
        seen_codes: set[str] = set()
        cat_failures = 0

        for cat_idx, path in enumerate(selected_paths):
            if self.is_budget_exceeded(reserve_seconds=25.0):
                logger.warning(
                    "[%s] Soft time budget reached during listing (%d candidates kept).",
                    self.brand,
                    len(candidates),
                )
                break
            if cat_idx > 0:
                self.sleep_jitter()

            cat_failed = False
            consecutive_empty_pages = 0
            for page in range(effective_max_pages):
                if self.is_budget_exceeded(reserve_seconds=25.0):
                    break
                if page > 0:
                    self.sleep_jitter()

                sep = "&" if "?" in path else "?"
                url = f"{self.base_url}{path}" if page == 0 else f"{self.base_url}{path}{sep}page={page}"
                try:
                    page_html = self.http.get_text(url)
                except ScraperBlockedError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] Listing error on %s page %d: %s", self.brand, path, page, exc)
                    if page == 0:
                        cat_failed = True
                    break

                raw_products = self.extract_listing_products(page_html)
                if not raw_products:
                    break

                new_in_page = 0
                for raw_prod in raw_products:
                    code = str(raw_prod.get("code") or raw_prod.get("baseProduct") or "").strip()
                    if not code or code in seen_codes:
                        continue
                    if not self._matches_section(raw_prod, path):
                        continue

                    cur_p, orig_p = self._extract_prices(raw_prod)
                    if cur_p is None:
                        continue
                    cart_p, _ = self._extract_cart_promo(raw_prod, cur_p)
                    if self.only_discounted and orig_p is None and cart_p is None:
                        continue

                    eff_p = cart_p if (cart_p is not None and cart_p < cur_p) else cur_p
                    ref_orig = orig_p if (orig_p is not None and orig_p > eff_p) else cur_p
                    disc_ratio = float((ref_orig - eff_p) / ref_orig) if ref_orig > eff_p else 0.0
                    seen_codes.add(code)
                    candidates.append((raw_prod, path, disc_ratio))
                    new_in_page += 1

                if new_in_page == 0:
                    consecutive_empty_pages += 1
                    if consecutive_empty_pages >= 2:
                        break
                else:
                    consecutive_empty_pages = 0

            if cat_failed:
                cat_failures += 1

        if cat_failures == len(selected_paths) and not candidates:
            raise RuntimeError(f"All {len(selected_paths)} Mavi categories failed")

        candidates.sort(key=lambda item: -item[2])
        if self.section == "all":
            candidates = interleave_by_gender(
                candidates,
                lambda item: self._infer_gender(item[0], item[1]),
            )
        logger.info(
            "[%s] Collected %d discounted listing candidates across %d categories",
            self.brand,
            len(candidates),
            len(selected_paths),
        )

        products: list[ProductItem] = []
        detail_fetches = 0
        pdp_blocked = False

        detail_timeout = min(self.timeout_seconds, 10)
        for raw_prod, path, _ in candidates:
            detail_html: str | None = None
            if (
                self.fetch_variant_details
                and not pdp_blocked
                and detail_fetches < self.max_detail_fetches
                and not self.is_budget_exceeded()
            ):
                if detail_fetches > 0:
                    self.sleep_jitter(scale=0.6)
                rel_url = str(raw_prod.get("url") or "").strip()
                if rel_url:
                    pdp_url = urljoin(f"{self.base_url}/", rel_url.lstrip("/"))
                    try:
                        detail_html = self.http.get_text(
                            pdp_url,
                            timeout_seconds=detail_timeout,
                            max_retries=1,
                        )
                        detail_fetches += 1
                    except ScraperBlockedError as exc:
                        if not products:
                            raise
                        logger.warning(
                            "[%s] PDP blocked after %d products (%s); continuing with listing fallback.",
                            self.brand,
                            len(products),
                            exc,
                        )
                        pdp_blocked = True
                        detail_html = None
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("[%s] Detail fetch failed for %s: %s", self.brand, pdp_url, exc)
                        detail_html = None

            item = self.parse_listing_product(
                raw_prod,
                category_path=path,
                detail_html=detail_html,
            )
            if item is not None:
                products.append(item)

        logger.info("[%s] Fetched %d discounted products", self.brand, len(products))
        return products
