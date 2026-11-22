"""Shared Akinon / Eren Perakende scraper engine for SuperStep, Occasion, and FashFed."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import urlencode, urljoin

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
_KIDS_KEYWORDS = ("çocuk", "cocuk", "bebek", "baby", "kid", "kids", "junior", "genç", "genc", "gs ", "td ", "ps ")
_MEN_KEYWORDS = ("erkek", "man", "men", "unisex")
_WOMEN_KEYWORDS = ("kadın", "kadin", "woman", "women", "unisex")


def _strip_html(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", cleaned).strip()


def _looks_like_small_shoe_code(label: str) -> bool:
    """Return True if a size label looks like a US/UK shoe size (e.g. 4, 8.5, 10,5) instead of EU."""
    normalized = label.strip().replace(",", ".")
    try:
        val = float(normalized)
    except ValueError:
        return False
    return 1.0 <= val <= 16.0


@dataclass(frozen=True)
class AkinonBrandConfig:
    """Storefront configuration for an Akinon / Omnitron Turkey brand."""

    brand: str
    display_name: str
    domain: str
    category_paths: dict[str, tuple[str, ...]]
    default_sorter: str | None = None
    default_category: ProductCategory = ProductCategory.SHOES
    default_run_mode: ScrapeRunMode = ScrapeRunMode.CLOUD
    extra_query_params: dict[str, str] = field(default_factory=dict)


EREN_BRAND_CONFIGS: dict[str, AkinonBrandConfig] = {
    "superstep": AkinonBrandConfig(
        brand="superstep",
        display_name="SuperStep",
        domain="www.superstep.com.tr",
        category_paths={
            "all": ("/erkek-ayakkabi/", "/kadin-ayakkabi/", "/erkek-giyim/", "/kadin-giyim/"),
            "men": ("/erkek-ayakkabi/", "/erkek-giyim/"),
            "women": ("/kadin-ayakkabi/", "/kadin-giyim/"),
        },
        default_sorter="number",  # İndirim Oranına Göre
        default_category=ProductCategory.SHOES,
    ),
    "occasion": AkinonBrandConfig(
        brand="occasion",
        display_name="Occasion",
        domain="www.occasion.com.tr",
        category_paths={
            "all": ("/erkek-ayakkabi/", "/kadin-ayakkabi/", "/erkek-giyim/", "/kadin-giyim/"),
            "men": ("/erkek-ayakkabi/", "/erkek-giyim/"),
            "women": ("/kadin-ayakkabi/", "/kadin-giyim/"),
        },
        default_sorter="default",
        default_category=ProductCategory.SHOES,
    ),
    "fashfed": AkinonBrandConfig(
        brand="fashfed",
        display_name="FashFed",
        domain="www.fashfed.com",
        category_paths={
            "all": ("/flash-indirim/", "/erkek/", "/kadin/"),
            "men": ("/flash-indirim/", "/erkek/"),
            "women": ("/flash-indirim/", "/kadin/"),
        },
        default_sorter="-discount_ratio",
        default_category=ProductCategory.SHOES,
    ),
}


class AkinonScraper(BaseScraper):
    """Reusable Akinon (`?format=json`) listing + variant detail scraper."""

    engine_name = "akinon"

    def __init__(
        self,
        config: AkinonBrandConfig,
        *,
        section: Literal["all", "men", "women"] = "all",
        only_discounted: bool = True,
        max_categories: int = 4,
        max_pages_per_category: int = 2,
        max_detail_fetches: int = 48,
        fetch_variant_details: bool = True,
        max_retries: int = 2,
        impersonate: str | None = None,
        timeout_seconds: int = 20,
        min_jitter_seconds: float = 0.6,
        max_jitter_seconds: float = 1.6,
    ) -> None:
        self.config = config
        self.brand = config.brand
        self.default_run_mode = config.default_run_mode

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

        self.base_url = f"https://{self.config.domain}"
        self.http = HttpClient(
            self.brand,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            impersonate=impersonate,
            default_headers={
                "Referer": f"{self.base_url}/",
                "Origin": self.base_url,
            },
        )

    def close(self) -> None:
        self.http.close()

    def _build_listing_url(self, path: str, page: int) -> str:
        params: dict[str, Any] = {"format": "json", "page": page}
        if self.config.default_sorter:
            params["sorter"] = self.config.default_sorter
        params.update(self.config.extra_query_params)
        sep = "&" if "?" in path else "?"
        return f"{self.base_url}{path}{sep}{urlencode(params)}"

    def _build_detail_url(self, absolute_url: str) -> str:
        full_url = urljoin(f"{self.base_url}/", absolute_url)
        sep = "&" if "?" in full_url else "?"
        return f"{full_url}{sep}format=json"

    def _matches_section(self, raw_product: dict[str, Any], category_path: str) -> bool:
        attrs = raw_product.get("attributes") or {}
        title = str(raw_product.get("name") or "")
        gender_parts = [
            str(attrs.get("integration_cinsiyet") or ""),
            str(attrs.get("integration_gender") or ""),
            str(attrs.get("emarsys_cinsiyet") or ""),
            str(attrs.get("integration_erp_cinsiyet") or ""),
            str(attrs.get("cinsiyet") or ""),
            title,
            category_path,
        ]
        combined = turkish_lower(" ".join(p for p in gender_parts if p))

        # Always skip kids / baby items
        if str(attrs.get("integration_cocuk") or "") == "1":
            return False
        if any(kw in combined for kw in _KIDS_KEYWORDS):
            return False

        if self.section == "all":
            return True

        title_lower = turkish_lower(title)
        title_has_men = bool(re.search(r"(?<![a-zçğıöşü])erkek", title_lower))
        title_has_women = bool(re.search(r"(?<![a-zçğıöşü])kad[ıi]n", title_lower))
        title_is_unisex = "unisex" in title_lower
        if not title_is_unisex:
            if self.section == "men" and title_has_women and not title_has_men:
                return False
            if self.section == "women" and title_has_men and not title_has_women:
                return False

        is_erkek_flag = str(attrs.get("integration_erkek") or "") == "1"
        is_kadin_flag = str(attrs.get("integration_kadin") or "") == "1"
        if is_erkek_flag or is_kadin_flag:
            if self.section == "men":
                return is_erkek_flag
            if self.section == "women":
                return is_kadin_flag

        is_unisex = "unisex" in combined
        is_men = is_unisex or any(re.search(rf"(?<![a-zçğıöşü]){kw}", combined) for kw in ("erkek", "men", "man"))
        is_women = is_unisex or any(re.search(rf"(?<![a-zçğıöşü]){kw}", combined) for kw in ("kadın", "kadin", "women", "woman"))

        if self.section == "men":
            return is_men or not is_women
        if self.section == "women":
            return is_women or not is_men
        return True

    @staticmethod
    def _extract_image_url(raw_product: dict[str, Any]) -> str | None:
        images = raw_product.get("productimage_set") or []
        for img in images:
            if isinstance(img, dict):
                candidate = img.get("image")
                if isinstance(candidate, str) and candidate.startswith("http"):
                    return candidate
        return None

    @staticmethod
    def _extract_fabric_info(attrs: dict[str, Any]) -> tuple[str | None, int | None]:
        for key in (
            "integration_erp_icerik",
            "integration_material",
            "integration_fabric",
            "integration_kumas",
            "kumas_bilgisi",
            "material",
            "fabric",
        ):
            val = attrs.get(key)
            if isinstance(val, str) and val.strip():
                cleaned = _strip_html(val)
                if cleaned and cleaned != "-":
                    return cleaned, extract_polyester_percent(cleaned)
        return None, None

    @staticmethod
    def _extract_fallback_size(attrs: dict[str, Any]) -> list[str]:
        for key in (
            "integration_trBeden",
            "filterable_size",
            "filterable_beden",
            "integration_size",
            "integration_beden",
        ):
            val = attrs.get(key)
            if isinstance(val, str) and val.strip() and val.strip() != "-":
                return [val.strip()]
        return []

    @staticmethod
    def _extract_sizes_from_detail(
        detail_payload: dict[str, Any],
        category: ProductCategory,
    ) -> tuple[list[str], bool, str | None, int | None]:
        """Parse in-stock sizes, critical stock flag, and optional fabric info from Akinon detail JSON."""
        variants = detail_payload.get("variants") or []
        size_variant: dict[str, Any] | None = None

        for var in variants:
            if not isinstance(var, dict):
                continue
            v_key = turkish_lower(str(var.get("attribute_key") or ""))
            v_name = turkish_lower(str(var.get("attribute_name") or ""))
            if any(tok in v_key or tok in v_name for tok in ("beden", "size", "numara")):
                size_variant = var
                break

        available_sizes: list[str] = []
        is_critical_stock = False
        fabric_info: str | None = None
        polyester_pct: int | None = None

        detail_prod = detail_payload.get("product") or {}
        if isinstance(detail_prod, dict):
            fabric_info, polyester_pct = AkinonScraper._extract_fabric_info(
                detail_prod.get("attributes") or {}
            )

        if size_variant is not None:
            for opt in size_variant.get("options") or []:
                if not isinstance(opt, dict):
                    continue
                in_stock = bool(opt.get("in_stock"))
                opt_prod = opt.get("product") or {}
                stock_raw = opt_prod.get("stock") if isinstance(opt_prod, dict) else None
                stock_qty: int | None = None
                if stock_raw is not None:
                    try:
                        stock_qty = int(stock_raw)
                    except (TypeError, ValueError):
                        stock_qty = None

                if not in_stock or (stock_qty is not None and stock_qty <= 0):
                    continue

                if stock_qty is not None and 0 < stock_qty <= _CRITICAL_STOCK_THRESHOLD:
                    is_critical_stock = True

                raw_label = str(opt.get("label") or opt.get("value") or "").strip()
                opt_attrs = opt_prod.get("attributes") if isinstance(opt_prod, dict) else {}
                opt_attrs = opt_attrs or {}

                # Occasion/Converse sometimes uses US/UK numbers in `label` ("4", "8,5")
                # while storing the canonical EU/TR size in `integration_trBeden` / `filterable_size`.
                preferred_label = raw_label
                if category == ProductCategory.SHOES and _looks_like_small_shoe_code(raw_label):
                    for eu_key in ("integration_trBeden", "filterable_size", "filterable_beden"):
                        eu_val = str(opt_attrs.get(eu_key) or "").strip()
                        if eu_val and eu_val != "-" and not _looks_like_small_shoe_code(eu_val):
                            preferred_label = eu_val
                            break

                if preferred_label and preferred_label != "-":
                    available_sizes.append(preferred_label)

        return available_sizes, is_critical_stock, fabric_info, polyester_pct

    def parse_listing_product(
        self,
        raw: dict[str, Any],
        *,
        category_path: str = "",
        detail_payload: dict[str, Any] | None = None,
    ) -> ProductItem | None:
        """Convert an Akinon listing product (+ optional detail payload) into a `ProductItem`."""
        if not raw.get("in_stock", True):
            return None

        title = str(raw.get("name") or "").strip()
        abs_url = str(raw.get("absolute_url") or "").strip()
        if not title or not abs_url:
            return None

        current_price = parse_try_price(raw.get("price"))
        original_price = parse_try_price(raw.get("retail_price"))
        if current_price is None:
            return None
        if original_price is not None and original_price <= current_price:
            original_price = None

        if self.only_discounted and original_price is None:
            return None

        attrs = raw.get("attributes") or {}
        inferred_cat = infer_product_category(
            title,
            str(attrs.get("integration_category") or ""),
            str(attrs.get("cadir_kategori") or ""),
            str(attrs.get("emarsys_ana_grup") or ""),
            str(attrs.get("integration_tanim") or ""),
            category_path,
            default=self.config.default_category,
        )

        fabric_info, polyester_pct = self._extract_fabric_info(attrs)
        available_sizes: list[str] = []
        is_critical_stock = False

        if detail_payload is not None:
            (
                available_sizes,
                is_critical_stock,
                detail_fabric,
                detail_poly,
            ) = self._extract_sizes_from_detail(detail_payload, inferred_cat)
            if not fabric_info and detail_fabric:
                fabric_info = detail_fabric
                polyester_pct = detail_poly

        if not available_sizes:
            available_sizes = self._extract_fallback_size(attrs)
            stock_val = raw.get("stock")
            if isinstance(stock_val, (int, float)) and 0 < int(stock_val) <= _CRITICAL_STOCK_THRESHOLD:
                is_critical_stock = True

        external_id = str(raw.get("base_code") or raw.get("pk") or raw.get("sku") or "").strip()
        color_code = str(
            attrs.get("integration_color_hash")
            or attrs.get("integration_renk_tonu")
            or attrs.get("integration_renk")
            or ""
        ).strip()
        if color_code and color_code not in {"-", "0"} and raw.get("base_code"):
            external_id = f"{raw['base_code']}-{color_code}"
        elif raw.get("pk"):
            external_id = str(raw["pk"])

        description = _strip_html(
            str(
                attrs.get("integration_description")
                or attrs.get("integration_kalem_icerik")
                or ""
            )
        )

        return ProductItem(
            brand=self.brand,
            external_id=external_id,
            title=title,
            category=inferred_cat,
            url=urljoin(f"{self.base_url}/", abs_url),
            image_url=self._extract_image_url(raw),
            current_price=current_price,
            original_price=original_price,
            currency="TRY",
            available_sizes=available_sizes,
            is_critical_stock=is_critical_stock,
            fabric_info=fabric_info,
            polyester_percent=polyester_pct,
            raw_metadata={
                "pk": raw.get("pk"),
                "sku": raw.get("sku"),
                "base_code": raw.get("base_code"),
                "category_path": category_path,
                "description": description[:500] if description else "",
            },
        )

    def fetch_products(self) -> list[ProductItem]:
        """Fetch discounted products and per-size availability from Akinon JSON endpoints."""
        paths = self.config.category_paths.get(self.section) or self.config.category_paths["all"]
        selected_paths = list(paths[: self.max_categories])

        candidates: list[tuple[dict[str, Any], str]] = []
        seen_urls: set[str] = set()
        category_failures = 0

        for cat_idx, path in enumerate(selected_paths):
            if cat_idx > 0:
                self.sleep_jitter()

            cat_failed = False
            for page in range(1, self.max_pages_per_category + 1):
                if page > 1:
                    self.sleep_jitter()

                url = self._build_listing_url(path, page)
                try:
                    payload = self.http.get_json(url)
                except ScraperBlockedError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] Listing error on %s page %d: %s", self.brand, path, page, exc)
                    if page == 1:
                        cat_failed = True
                    break

                raw_products = payload.get("products") or []
                if not raw_products:
                    break

                for raw_prod in raw_products:
                    if not isinstance(raw_prod, dict):
                        continue
                    abs_url = str(raw_prod.get("absolute_url") or "").strip()
                    if not abs_url or abs_url in seen_urls:
                        continue
                    if not raw_prod.get("in_stock", True):
                        continue
                    if not self._matches_section(raw_prod, path):
                        continue

                    cur_p = parse_try_price(raw_prod.get("price"))
                    orig_p = parse_try_price(raw_prod.get("retail_price"))
                    if cur_p is None:
                        continue
                    if self.only_discounted and (orig_p is None or orig_p <= cur_p):
                        continue

                    seen_urls.add(abs_url)
                    candidates.append((raw_prod, path))

                pagination = payload.get("pagination") or {}
                num_pages = int(pagination.get("num_pages") or 1)
                if page >= num_pages:
                    break

            if cat_failed:
                category_failures += 1

        if category_failures == len(selected_paths) and not candidates:
            raise RuntimeError(f"All {len(selected_paths)} Akinon categories failed for {self.brand}")

        logger.info(
            "[%s] Collected %d discounted listing candidates across %d categories",
            self.brand,
            len(candidates),
            len(selected_paths),
        )

        # Prioritize higher discount percentage when detail fetch budget is bounded
        def _discount_sort_key(entry: tuple[dict[str, Any], str]) -> float:
            raw_p = entry[0]
            ratio = raw_p.get("discount_ratio")
            if ratio is not None:
                try:
                    return -float(ratio)
                except (TypeError, ValueError):
                    pass
            cur = parse_try_price(raw_p.get("price")) or Decimal("1")
            orig = parse_try_price(raw_p.get("retail_price")) or cur
            if orig > cur and orig > Decimal("0"):
                return -float((orig - cur) / orig)
            return 0.0

        candidates.sort(key=_discount_sort_key)

        products_by_id: dict[str, ProductItem] = {}
        detail_fetches = 0

        for raw_prod, path in candidates:
            detail_payload: dict[str, Any] | None = None
            if self.fetch_variant_details and detail_fetches < self.max_detail_fetches:
                abs_url = str(raw_prod.get("absolute_url") or "")
                if detail_fetches > 0:
                    # Polite shorter jitter between detail calls
                    self.sleep_jitter()
                try:
                    detail_payload = self.http.get_json(self._build_detail_url(abs_url))
                    detail_fetches += 1
                except ScraperBlockedError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[%s] Detail fetch failed for %s, using listing fallback: %s", self.brand, abs_url, exc)
            elif self.fetch_variant_details and detail_fetches >= self.max_detail_fetches:
                break

            item = self.parse_listing_product(
                raw_prod,
                category_path=path,
                detail_payload=detail_payload,
            )
            if item is not None and item.external_id not in products_by_id:
                products_by_id[item.external_id] = item

        return list(products_by_id.values())


class ErenScraper(AkinonScraper):
    """Unified Eren Perakende scraper for SuperStep, Occasion, and FashFed."""

    engine_name = "eren"

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
        min_jitter_seconds: float = 0.3,
        max_jitter_seconds: float = 0.8,
    ) -> None:
        brand_key = brand.strip().lower()
        if brand_key not in EREN_BRAND_CONFIGS:
            supported = ", ".join(sorted(EREN_BRAND_CONFIGS))
            raise ValueError(f"Unsupported Eren Perakende brand '{brand}'. Supported: {supported}")

        super().__init__(
            EREN_BRAND_CONFIGS[brand_key],
            section=section,
            only_discounted=only_discounted,
            max_categories=max_categories,
            max_pages_per_category=max_pages_per_category,
            max_detail_fetches=max_detail_fetches,
            fetch_variant_details=fetch_variant_details,
            max_retries=max_retries,
            impersonate=impersonate,
            timeout_seconds=timeout_seconds,
            min_jitter_seconds=min_jitter_seconds,
            max_jitter_seconds=max_jitter_seconds,
        )
