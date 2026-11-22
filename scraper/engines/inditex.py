"""Shared Inditex Group scraper engine for Zara, Pull&Bear, Bershka, Lefties, and Massimo Dutti (TR)."""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal

from curl_cffi import requests as curl_requests

from .base import (
    BaseScraper,
    ProductCategory,
    ProductGender,
    ProductItem,
    ScrapeRunMode,
    ScraperBlockedError,
)
from ..core.filters import extract_polyester_percent, turkish_lower
from ..core.http import (
    IMPERSONATE_PROFILES,
    get_safe_ca_bundle,
    infer_product_gender,
    is_blocked_body,
    is_kids_product,
)

logger = logging.getLogger(__name__)

_PROMO_KEYWORDS = (
    "promocion",
    "promotion",
    "indirim",
    "sale",
    "special price",
    "special_price",
    "özel fiyat",
    "ozel fiyat",
    "fırsat",
    "firsat",
    "outlet",
    "black friday",
    "mid season",
    "offer",
)

_EXCLUDED_SECTION_KEYWORDS = (
    "çocuk",
    "cocuk",
    "kid",
    "nino",
    "niño",
    "nina",
    "niña",
    "bebek",
    "baby",
    "teen",
    "home",
    "ev ",
    "beauty",
    "kozmetik",
    "parfüm",
    "parfum",
    "perfume",
    "gift card",
    "hediye kart",
    "join life",
    "edit",
    "editorial",
)

_MEN_SECTION_KEYWORDS = ("erkek", "man", "men", "hombre", "caballero")
_WOMEN_SECTION_KEYWORDS = ("kadın", "kadin", "woman", "women", "mujer", "señora", "senora")

_SHOES_KEYWORDS = (
    "shoe",
    "footwear",
    "sneaker",
    "boot",
    "loafer",
    "sandal",
    "slipper",
    "trainer",
    "moccasin",
    "espadrille",
    "calzado",
    "zapato",
    "bota",
    "deportivo",
    "ayakkabı",
    "ayakkabi",
    "bot",
    "çizme",
    "cizme",
    "terlik",
    "sandalet",
    "makosen",
)

_OUTERWEAR_KEYWORDS = (
    "coat",
    "jacket",
    "blazer",
    "trench",
    "parka",
    "puffer",
    "bomber",
    "anorak",
    "gilet",
    "vest",
    "overcoat",
    "biker",
    "huntress",
    "abrigo",
    "cazadora",
    "chaqueta",
    "gabardina",
    "mont",
    "kaban",
    "ceket",
    "trençkot",
    "trenckot",
    "yağmurluk",
    "yagmurluk",
    "palto",
    "yelek",
)

_BOTTOMS_KEYWORDS = (
    "trouser",
    "pant",
    "jean",
    "denim",
    "short",
    "jogger",
    "chino",
    "cargo",
    "skirt",
    "bermuda",
    "legging",
    "pantalon",
    "vaquero",
    "falda",
    "pantolon",
    "şort",
    "sort",
    "etek",
    "eşofman altı",
    "esofman alti",
    "tayt",
)

_TOPS_KEYWORDS = (
    "shirt",
    "t-shirt",
    "tshirt",
    "tee",
    "sweater",
    "knit",
    "jumper",
    "pullover",
    "hoodie",
    "sweatshirt",
    "cardigan",
    "polo",
    "top",
    "blouse",
    "jersey",
    "camisa",
    "camiseta",
    "sudadera",
    "punto",
    "tişört",
    "tisort",
    "gömlek",
    "gomlek",
    "kazak",
    "triko",
    "hırka",
    "hirka",
    "bluz",
    "atlet",
    "süveter",
    "suveter",
)

def _cents_to_try(raw_value: Any) -> Decimal | None:
    """Convert Inditex integer/string kuruş price (e.g. '129000') into TRY Decimal."""
    if raw_value is None or raw_value == "":
        return None
    try:
        cents = Decimal(str(raw_value).strip())
    except Exception:  # noqa: BLE001
        return None
    if cents <= Decimal("0"):
        return None
    return (cents / Decimal("100")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _infer_category(*hints: str) -> ProductCategory:
    """Map Inditex family, subfamily, category path, and Turkish title to canonical category."""
    combined = turkish_lower(" ".join(h for h in hints if h))
    if not combined:
        return ProductCategory.OTHER

    # Remove sleeve-length phrases that falsely contain "short" (e.g. "short sleeve t-shirt")
    cleaned = re.sub(r"\bshort\s+sleeve\b", "", combined)

    def _has_any(keywords: tuple[str, ...]) -> bool:
        for kw in keywords:
            if " " in kw or "-" in kw:
                if kw in cleaned:
                    return True
            elif re.search(rf"(?<![a-zçğıöşü]){re.escape(kw)}", cleaned):
                return True
        return False

    # Order matters: shoes -> outerwear -> explicit tops (e.g. t-shirt/polo) -> bottoms -> tops
    if _has_any(_SHOES_KEYWORDS):
        return ProductCategory.SHOES
    if _has_any(_OUTERWEAR_KEYWORDS):
        return ProductCategory.OUTERWEAR
    if _has_any(("tişört", "tisort", "t-shirt", "tshirt", "polo", "gömlek", "gomlek", "kazak", "triko", "hırka", "hirka", "bluz", "sweatshirt", "hoodie")):
        return ProductCategory.TOPS
    if _has_any(_BOTTOMS_KEYWORDS):
        return ProductCategory.BOTTOMS
    if _has_any(_TOPS_KEYWORDS):
        return ProductCategory.TOPS
    return ProductCategory.OTHER


@dataclass(frozen=True)
class InditexBrandConfig:
    """Storefront and API configuration for a single Inditex Turkey brand."""

    brand: str
    display_name: str
    protocol: Literal["itxrest", "zara_ajax"]
    domain: str
    store_path: str
    static_cdn: str
    store_id: int
    catalog_id: int
    language_id: int = -43
    app_id: int = 1
    type_catalog: int = 1
    default_run_mode: ScrapeRunMode = ScrapeRunMode.CLOUD
    extra_promo_category_ids: tuple[int, ...] = field(default_factory=tuple)


INDITEX_BRAND_CONFIGS: dict[str, InditexBrandConfig] = {
    "pullandbear": InditexBrandConfig(
        brand="pullandbear",
        display_name="Pull&Bear",
        protocol="itxrest",
        domain="www.pullandbear.com",
        store_path="tr",
        static_cdn="https://static.pullandbear.net",
        store_id=25009521,
        catalog_id=20309459,
        language_id=-43,
        app_id=1,
    ),
    "bershka": InditexBrandConfig(
        brand="bershka",
        display_name="Bershka",
        protocol="itxrest",
        domain="www.bershka.com",
        store_path="tr",
        static_cdn="https://static.bershka.net",
        store_id=44109521,
        catalog_id=40259534,
        language_id=-43,
        app_id=1,
    ),
    "lefties": InditexBrandConfig(
        brand="lefties",
        display_name="Lefties",
        protocol="itxrest",
        domain="www.lefties.com",
        store_path="tr",
        static_cdn="https://static.lefties.com",
        store_id=94009021,
        catalog_id=90009064,
        language_id=-43,
        app_id=1,
    ),
    "massimodutti": InditexBrandConfig(
        brand="massimodutti",
        display_name="Massimo Dutti",
        protocol="itxrest",
        domain="www.massimodutti.com",
        store_path="tr",
        static_cdn="https://static.massimodutti.net",
        store_id=34009471,
        catalog_id=30359506,
        language_id=-43,
        app_id=1,
    ),
    "zara": InditexBrandConfig(
        brand="zara",
        display_name="Zara",
        protocol="zara_ajax",
        domain="www.zara.com",
        store_path="tr/tr",
        static_cdn="https://static.zara.net",
        store_id=11766,
        catalog_id=0,
        language_id=-43,
        app_id=1,
    ),
}


@dataclass
class _CategoryNode:
    category_id: int
    name: str
    path: str
    key: str
    is_promo: bool
    section_hint: str


class InditexScraper(BaseScraper):
    """Unified Inditex scraper supporting Pull&Bear, Bershka, Lefties, Massimo Dutti, and Zara TR."""

    engine_name = "inditex"

    def __init__(
        self,
        brand: str,
        *,
        section: Literal["all", "men", "women"] = "all",
        only_discounted: bool = True,
        include_regular_categories: bool = False,
        max_categories: int = 12,
        max_detail_fetches: int = 36,
        batch_size: int = 30,
        max_retries: int = 2,
        impersonate: str | None = None,
        timeout_seconds: int = 20,
        min_jitter_seconds: float = 1.0,
        max_jitter_seconds: float = 3.0,
    ) -> None:
        brand_key = brand.strip().lower()
        if brand_key not in INDITEX_BRAND_CONFIGS:
            supported = ", ".join(sorted(INDITEX_BRAND_CONFIGS))
            raise ValueError(f"Unsupported Inditex brand '{brand}'. Supported: {supported}")

        self.config = INDITEX_BRAND_CONFIGS[brand_key]
        self.brand = self.config.brand
        self.default_run_mode = self.config.default_run_mode

        super().__init__(
            timeout_seconds=timeout_seconds,
            min_jitter_seconds=min_jitter_seconds,
            max_jitter_seconds=max_jitter_seconds,
        )

        self.section = section
        self.only_discounted = only_discounted
        self.include_regular_categories = include_regular_categories
        self.max_categories = max(1, max_categories)
        self.max_detail_fetches = max(0, max_detail_fetches)
        self.batch_size = max(5, min(50, batch_size))
        self.max_retries = max(0, max_retries)
        self._impersonate = impersonate or random.choice(("chrome131", "chrome124"))
        self._ca_bundle = get_safe_ca_bundle()
        self._session: curl_requests.Session | None = None

    # ------------------------------------------------------------------
    # HTTP session & anti-bot resilience
    # ------------------------------------------------------------------
    def _get_session(self) -> curl_requests.Session:
        if self._session is None:
            self._session = curl_requests.Session(
                impersonate=self._impersonate,
                verify=self._ca_bundle,
            )
        return self._session

    def _rotate_session(self) -> curl_requests.Session:
        self.close()
        alternatives = [p for p in IMPERSONATE_PROFILES if p != self._impersonate]
        self._impersonate = random.choice(alternatives or list(IMPERSONATE_PROFILES))
        return self._get_session()

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:  # noqa: BLE001
                pass
            self._session = None

    def _build_headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": f"https://{self.config.domain}/{self.config.store_path}/",
            "Origin": f"https://{self.config.domain}",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    @staticmethod
    def _is_blocked_body(text: str) -> bool:
        return is_blocked_body(text)

    def _get_json(self, url: str) -> Any:
        """Execute a GET request expecting JSON, with impersonate rotation and backoff."""
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            session = self._get_session() if attempt == 0 else self._rotate_session()
            try:
                resp = session.get(
                    url,
                    headers=self._build_headers(),
                    timeout=self.timeout_seconds,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "[%s] Request error on attempt %d/%d (%s): %s",
                    self.brand,
                    attempt + 1,
                    self.max_retries + 1,
                    url,
                    exc,
                )
                if attempt < self.max_retries:
                    time.sleep(random.uniform(1.5, 3.5) * (attempt + 1))
                continue

            if resp.status_code in (403, 429) or self._is_blocked_body(resp.text):
                last_error = ScraperBlockedError(
                    f"HTTP {resp.status_code} anti-bot block on {url} (profile={self._impersonate})"
                )
                logger.warning(
                    "[%s] Anti-bot/rate-limit HTTP %d on attempt %d/%d (profile=%s)",
                    self.brand,
                    resp.status_code,
                    attempt + 1,
                    self.max_retries + 1,
                    self._impersonate,
                )
                if attempt < self.max_retries:
                    time.sleep(random.uniform(2.0, 4.5) * (attempt + 1))
                    continue
                raise last_error

            if resp.status_code >= 500:
                last_error = RuntimeError(f"HTTP {resp.status_code} server error on {url}")
                if attempt < self.max_retries:
                    time.sleep(random.uniform(1.5, 3.0) * (attempt + 1))
                    continue
                raise last_error

            if resp.status_code != 200:
                raise RuntimeError(f"Unexpected HTTP {resp.status_code} on {url}")

            try:
                return resp.json()
            except Exception as exc:  # noqa: BLE001
                if self._is_blocked_body(resp.text):
                    raise ScraperBlockedError(
                        f"Received anti-bot HTML instead of JSON on {url}"
                    ) from exc
                raise RuntimeError(f"Invalid JSON response from {url}: {exc}") from exc

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Failed to fetch {url}")

    # ------------------------------------------------------------------
    # Public BaseScraper entrypoint
    # ------------------------------------------------------------------
    def fetch_products(self) -> list[ProductItem]:
        """Fetch normalized products for this Inditex brand."""
        if self.config.protocol == "zara_ajax":
            return self._fetch_zara_products()
        return self._fetch_itxrest_products()

    # ------------------------------------------------------------------
    # Category tree selection
    # ------------------------------------------------------------------
    def _matches_section(self, path_lower: str, section_hint: str) -> bool:
        if any(excl in path_lower for excl in _EXCLUDED_SECTION_KEYWORDS):
            return False
        if is_kids_product(path_lower, section_hint):
            return False

        if self.section == "all":
            return True

        combined = f"{path_lower} {section_hint.lower()}"
        is_men = any(re.search(rf"\b{re.escape(kw)}\b", combined) for kw in _MEN_SECTION_KEYWORDS)
        is_women = any(re.search(rf"\b{re.escape(kw)}\b", combined) for kw in _WOMEN_SECTION_KEYWORDS)

        if self.section == "men":
            return is_men and not is_women
        if self.section == "women":
            return is_women and not is_men
        return True

    def _collect_categories(self, raw_categories: list[dict[str, Any]]) -> list[_CategoryNode]:
        """Flatten nested Inditex category trees and select target categories."""
        all_nodes: list[_CategoryNode] = []

        def walk(nodes: list[dict[str, Any]], parent_path: str = "", root_section: str = "") -> None:
            for node in nodes:
                name = str(node.get("name") or "").strip()
                name_en = str(node.get("nameEn") or node.get("sectionName") or "").strip()
                key = str(node.get("key") or "").strip()
                section_label = root_section or name_en or name
                label = f"{name} ({name_en})" if name_en and name_en.lower() != name.lower() else name
                cur_path = f"{parent_path} > {label}" if parent_path else label

                raw_view_id = node.get("viewCategoryId")
                raw_id = node.get("id")
                chosen_id = raw_view_id if raw_view_id and int(raw_view_id) > 0 else raw_id

                subs = node.get("subcategories") or []
                path_lower = turkish_lower(f"{cur_path} {key}")
                is_promo = any(kw in path_lower for kw in _PROMO_KEYWORDS)

                if chosen_id is not None and self._matches_section(path_lower, section_label):
                    # Avoid container-only roots (depth 0 with many subcategories) unless promo
                    if (not subs or is_promo) and parent_path:
                        all_nodes.append(
                            _CategoryNode(
                                category_id=int(chosen_id),
                                name=name or name_en or str(chosen_id),
                                path=cur_path,
                                key=key,
                                is_promo=is_promo,
                                section_hint=section_label,
                            )
                        )
                if subs:
                    walk(subs, cur_path, section_label)

        walk(raw_categories)

        # Deduplicate by category_id preserving order
        seen_ids: set[int] = set()
        unique_nodes: list[_CategoryNode] = []
        for cid in self.config.extra_promo_category_ids:
            seen_ids.add(cid)
            unique_nodes.append(
                _CategoryNode(
                    category_id=cid,
                    name=f"Configured Promo {cid}",
                    path=f"Promo > {cid}",
                    key="",
                    is_promo=True,
                    section_hint=self.section,
                )
            )

        for node in all_nodes:
            if node.category_id not in seen_ids:
                seen_ids.add(node.category_id)
                unique_nodes.append(node)

        # Exclude collaboration/license "special edition" noise that is not actual price promo
        promo_nodes = [
            n
            for n in unique_nodes
            if n.is_promo
            and "licencia" not in n.key.lower()
            and "special edition" not in n.path.lower()
            and "special_collections" not in n.key.lower()
            and "special_events" not in n.key.lower()
            and "creatupack" not in n.key.lower()
        ]
        # Prefer actual clothing/shoe/all-items promo nodes over bags/accessories-only promos
        promo_nodes.sort(
            key=lambda n: (
                0 if any(k in n.key.lower() for k in ("promociones", "special_prices", "sale", "rebajas")) else 1,
                1 if any(w in turkish_lower(n.path) for w in ("çanta", "canta", "aksesuar", "bag", "accessor")) else 0,
                len(n.path),
            )
        )

        if promo_nodes and not self.include_regular_categories:
            return promo_nodes[: self.max_categories]

        # Fallback or expanded mode: add main clothing & shoe categories
        regular_nodes = [
            n
            for n in unique_nodes
            if not n.is_promo
            and _infer_category(n.path, n.key) != ProductCategory.OTHER
        ]
        combined = promo_nodes + regular_nodes
        return combined[: self.max_categories]

    # ------------------------------------------------------------------
    # ITXREST engine (Pull&Bear, Bershka, Lefties, Massimo Dutti)
    # ------------------------------------------------------------------
    def _fetch_itxrest_products(self) -> list[ProductItem]:
        cfg = self.config
        cat_url = (
            f"https://{cfg.domain}/itxrest/2/catalog/store/{cfg.store_id}/{cfg.catalog_id}/category"
            f"?languageId={cfg.language_id}&typeCatalog={cfg.type_catalog}&appId={cfg.app_id}"
        )
        cat_payload = self._get_json(cat_url)
        raw_categories = cat_payload.get("categories") or []
        categories = self._collect_categories(raw_categories)
        logger.info("[%s] Selected %d categories to scan (section=%s)", self.brand, len(categories), self.section)

        if not categories:
            return []

        products_by_id: dict[str, ProductItem] = {}
        category_failures = 0

        def _scan(cat_list: list[_CategoryNode]) -> int:
            nonlocal category_failures
            scanned_failures = 0
            for idx, cat in enumerate(cat_list):
                if idx > 0:
                    self.sleep_jitter()

                try:
                    cat_items = self._fetch_itxrest_category(cat)
                    for item in cat_items:
                        if item.external_id not in products_by_id:
                            products_by_id[item.external_id] = item
                    logger.info(
                        "[%s] Category %s (%d): parsed %d items (running unique total: %d)",
                        self.brand,
                        cat.name,
                        cat.category_id,
                        len(cat_items),
                        len(products_by_id),
                    )
                except ScraperBlockedError:
                    raise
                except Exception as exc:  # noqa: BLE001 — isolate single category failure
                    scanned_failures += 1
                    category_failures += 1
                    logger.warning(
                        "[%s] Skipping category %s (%d) due to error: %s",
                        self.brand,
                        cat.name,
                        cat.category_id,
                        exc,
                    )
            return scanned_failures

        first_failures = _scan(categories)

        # If promo-only categories yielded 0 products (e.g. off-season promo banner),
        # automatically fall back to regular clothing/shoe categories to catch hidden markdowns.
        if not products_by_id and not self.include_regular_categories:
            prev_flag = self.include_regular_categories
            self.include_regular_categories = True
            try:
                expanded = self._collect_categories(raw_categories)
            finally:
                self.include_regular_categories = prev_flag
            scanned_ids = {c.category_id for c in categories}
            fallback_cats = [c for c in expanded if c.category_id not in scanned_ids][: min(6, self.max_categories)]
            if fallback_cats:
                logger.info(
                    "[%s] Promo categories yielded 0 items; scanning %d fallback regular categories.",
                    self.brand,
                    len(fallback_cats),
                )
                _scan(fallback_cats)

        if first_failures == len(categories) and not products_by_id:
            raise RuntimeError(f"All {len(categories)} categories failed for {self.brand}")

        return list(products_by_id.values())

    def _fetch_itxrest_category(self, category: _CategoryNode) -> list[ProductItem]:
        cfg = self.config
        ids_url = (
            f"https://{cfg.domain}/itxrest/3/catalog/store/{cfg.store_id}/{cfg.catalog_id}"
            f"/category/{category.category_id}/product"
            f"?languageId={cfg.language_id}&appId={cfg.app_id}&showProducts=false"
        )
        data = self._get_json(ids_url)
        product_ids = [int(pid) for pid in (data.get("productIds") or []) if pid]
        if not product_ids:
            return []

        items: list[ProductItem] = []
        for offset in range(0, len(product_ids), self.batch_size):
            chunk = product_ids[offset : offset + self.batch_size]
            ids_csv = ",".join(str(pid) for pid in chunk)

            if offset > 0:
                self.sleep_jitter()

            arr_url = (
                f"https://{cfg.domain}/itxrest/3/catalog/store/{cfg.store_id}/{cfg.catalog_id}"
                f"/productsArray?productIds={ids_csv}&languageId={cfg.language_id}&appId={cfg.app_id}"
            )
            arr_data = self._get_json(arr_url)
            raw_products = arr_data.get("products") or []
            if not raw_products:
                continue

            # Fetch live SKU stock availability for this batch
            stock_map = self._fetch_itxrest_batch_stock(chunk)

            for raw_prod in raw_products:
                parsed = self._parse_itxrest_product(raw_prod, category, stock_map)
                if parsed is not None:
                    items.append(parsed)

        return items

    def _fetch_itxrest_batch_stock(
        self,
        product_ids: list[int],
    ) -> dict[int, tuple[str, str]]:
        """Fetch real-time SKU stock availability and threshold labels for a product batch."""
        cfg = self.config
        ids_csv = ",".join(str(pid) for pid in product_ids)
        stock_url = (
            f"https://{cfg.domain}/itxrest/2/catalog/store/{cfg.store_id}/{cfg.catalog_id}"
            f"/product/stock?productIds={ids_csv}"
        )
        try:
            payload = self._get_json(stock_url)
        except ScraperBlockedError:
            raise
        except Exception as exc:  # noqa: BLE001 — degrade gracefully to isBuyable flags
            logger.debug("[%s] Batch stock fetch failed, falling back to isBuyable: %s", self.brand, exc)
            return {}

        sku_stock: dict[int, tuple[str, str]] = {}
        for entry in payload.get("stocks") or []:
            for sku_entry in entry.get("stocks") or []:
                sku_id = sku_entry.get("id")
                if sku_id is None:
                    continue
                availability = str(sku_entry.get("availability") or "").strip().lower()
                threshold = str(sku_entry.get("typeThreshold") or "").strip().upper()
                sku_stock[int(sku_id)] = (availability, threshold)
        return sku_stock

    def _parse_itxrest_product(
        self,
        raw: dict[str, Any],
        category: _CategoryNode,
        stock_map: dict[int, tuple[str, str]],
    ) -> ProductItem | None:
        raw_id = raw.get("id")
        if not raw_id:
            return None

        summaries = raw.get("bundleProductSummaries") or []
        summary = summaries[0] if summaries and isinstance(summaries[0], dict) else raw
        detail = summary.get("detail") or raw.get("detail") or {}
        colors = detail.get("colors") or []
        if not colors:
            return None

        # Match mainColorid if present, otherwise first color
        main_color_id = str(raw.get("mainColorid") or "")
        selected_color = colors[0]
        if main_color_id:
            for col in colors:
                if str(col.get("id") or "") == main_color_id:
                    selected_color = col
                    break

        sizes_raw = selected_color.get("sizes") or []
        if not sizes_raw:
            return None

        available_sizes: list[str] = []
        is_critical_stock = False
        best_current_price: Decimal | None = None
        best_original_price: Decimal | None = None

        for sz in sizes_raw:
            size_name = str(sz.get("name") or "").strip()
            sku_raw = sz.get("sku")
            sku_id = int(sku_raw) if sku_raw is not None else None

            price = _cents_to_try(sz.get("price"))
            old_price = _cents_to_try(sz.get("oldPrice"))
            if old_price is not None and price is not None and old_price <= price:
                old_price = None

            if price is not None and (best_current_price is None or price < best_current_price):
                best_current_price = price
                best_original_price = old_price
            elif best_original_price is None and old_price is not None:
                best_original_price = old_price

            in_stock = False
            if sku_id is not None and sku_id in stock_map:
                avail, threshold = stock_map[sku_id]
                if avail in {"in_stock", "low_on_stock", "few_units"}:
                    in_stock = True
                    if avail in {"low_on_stock", "few_units"} or "BAJO" in threshold or "LOW" in threshold:
                        is_critical_stock = True
            else:
                if sz.get("isBuyable") is True and str(sz.get("backSoon") or "0") != "1":
                    in_stock = True

            if in_stock and size_name:
                available_sizes.append(size_name)

        if best_current_price is None:
            return None

        if self.only_discounted and (best_original_price is None or best_original_price <= best_current_price):
            return None

        title = str(raw.get("name") or summary.get("name") or "").strip()
        if not title:
            return None

        color_name = str(selected_color.get("name") or "").strip()
        product_url_slug = str(raw.get("productUrl") or summary.get("productUrl") or "").strip().lstrip("/")
        if product_url_slug:
            url = f"https://{self.config.domain}/{self.config.store_path}/{product_url_slug}?pelement={raw_id}"
        else:
            url = f"https://{self.config.domain}/{self.config.store_path}/c0p{raw_id}.html"

        image_url = self._extract_itxrest_image(detail, str(selected_color.get("id") or ""))
        fabric_info, polyester_pct = self._extract_itxrest_composition(detail)

        inferred_category = _infer_category(
            str(raw.get("familyNameEN") or ""),
            str(raw.get("subFamilyNameEN") or ""),
            str(raw.get("familyName") or ""),
            str(raw.get("subFamilyName") or ""),
            title,
            category.path,
        )

        inferred_gender = infer_product_gender(
            str(raw.get("sectionNameEN") or ""),
            str(raw.get("sectionName") or ""),
            category.section_hint,
            title,
            category.path,
            default=ProductGender.UNISEX,
        )
        if is_kids_product(title, category.path):
            return None

        # Include color suffix in external_id when bundle holds distinct color variants
        external_id = str(raw_id)
        description = str(detail.get("longDescription") or detail.get("description") or "").strip()

        return ProductItem(
            brand=self.brand,
            external_id=external_id,
            title=f"{title} ({color_name})" if color_name and color_name.lower() not in title.lower() else title,
            category=inferred_category,
            gender=inferred_gender,
            url=url,
            image_url=image_url,
            current_price=best_current_price,
            original_price=best_original_price,
            currency="TRY",
            available_sizes=available_sizes,
            is_critical_stock=is_critical_stock,
            fabric_info=fabric_info,
            polyester_percent=polyester_pct,
            raw_metadata={
                "category_id": category.category_id,
                "category_path": category.path,
                "section": raw.get("sectionNameEN") or category.section_hint,
                "description": description,
            },
        )

    def _extract_itxrest_image(self, detail: dict[str, Any], color_id: str) -> str | None:
        xmedia_list = detail.get("xmedia") or []
        # Prefer xmedia block matching selected color_id
        ordered_blocks = sorted(
            xmedia_list,
            key=lambda blk: 0 if color_id and str(blk.get("path") or "").endswith(f"/{color_id}") else 1,
        )
        for blk in ordered_blocks:
            for item in blk.get("xmediaItems") or []:
                for media in item.get("medias") or []:
                    if media.get("format") not in (1, None) and media.get("type") not in ("image", None):
                        continue
                    extra = media.get("extraInfo") or {}
                    candidate = (
                        extra.get("deliveryUrl")
                        or media.get("url")
                        or extra.get("url")
                    )
                    if isinstance(candidate, str) and candidate.startswith("http"):
                        return candidate.replace("{width}", "750")
                    path = extra.get("deliveryPath") or extra.get("oDeliveryPath")
                    if isinstance(path, str) and path.startswith("/"):
                        return f"{self.config.static_cdn}{path}"
        return None

    @staticmethod
    def _extract_itxrest_composition(detail: dict[str, Any]) -> tuple[str | None, int | None]:
        raw_comp = detail.get("composition") or []
        if not isinstance(raw_comp, list) or not raw_comp:
            return None, None

        parts_text: list[str] = []
        outer_polyester: int | None = None
        any_polyester: int | None = None

        for part_entry in raw_comp:
            if not isinstance(part_entry, dict):
                continue
            part_code = str(part_entry.get("part") or "1")
            fibers = part_entry.get("composition") or []
            fiber_strs: list[str] = []
            for fiber in fibers:
                if not isinstance(fiber, dict):
                    continue
                fname = str(fiber.get("name") or "").strip()
                pct_raw = str(fiber.get("percentage") or fiber.get("description") or "").strip()
                if not fname:
                    continue
                if pct_raw.isdigit():
                    pct_int = int(pct_raw)
                    fiber_strs.append(f"%{pct_int} {fname}")
                    if "polyester" in turkish_lower(fname) or "poliester" in turkish_lower(fname):
                        if part_code == "1":
                            outer_polyester = max(outer_polyester or 0, pct_int)
                        any_polyester = max(any_polyester or 0, pct_int)
                else:
                    fiber_strs.append(fname)
            if fiber_strs:
                prefix = "Dış: " if part_code == "1" and len(raw_comp) > 1 else ("Astar: " if part_code == "2" else "")
                parts_text.append(prefix + ", ".join(fiber_strs))

        fabric_info = " | ".join(parts_text) if parts_text else None
        polyester_pct = outer_polyester if outer_polyester is not None else any_polyester
        if polyester_pct is None and fabric_info:
            polyester_pct = extract_polyester_percent(fabric_info)
        return fabric_info, polyester_pct

    # ------------------------------------------------------------------
    # Zara AJAX engine
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_zara_pdp_details(
        pdp_payload: dict[str, Any],
        target_color_id: str = "",
    ) -> tuple[list[str], bool, str | None, int | None]:
        """Extract in-stock sizes, critical-stock flag, and fabric composition from Zara PDP AJAX JSON."""
        product = pdp_payload.get("product") or {}
        detail = product.get("detail") or {}
        colors = detail.get("colors") or []

        chosen_color: dict[str, Any] = {}
        if target_color_id:
            for col in colors:
                if isinstance(col, dict) and str(col.get("id") or "") == target_color_id:
                    chosen_color = col
                    break
        if not chosen_color and colors and isinstance(colors[0], dict):
            chosen_color = colors[0]

        available_sizes: list[str] = []
        is_critical_stock = False
        for sz in chosen_color.get("sizes") or []:
            if not isinstance(sz, dict):
                continue
            avail = str(sz.get("availability") or "").strip().lower()
            if avail in {"in_stock", "low_on_stock", "few_units"}:
                sz_name = str(sz.get("name") or "").strip()
                if sz_name and sz_name not in available_sizes:
                    available_sizes.append(sz_name)
                if avail in {"low_on_stock", "few_units"}:
                    is_critical_stock = True

        if 0 < len(available_sizes) <= 2:
            is_critical_stock = True

        # Parse Zara `detailedComposition` -> parts[*].areas[*].components or parts[*].components
        fabric_parts: list[str] = []
        outer_polyester: int | None = None
        any_polyester: int | None = None
        detailed_comp = detail.get("detailedComposition") or {}
        if isinstance(detailed_comp, dict):
            for idx, part in enumerate(detailed_comp.get("parts") or []):
                if not isinstance(part, dict):
                    continue
                part_desc = str(part.get("description") or "").strip()
                comps: list[dict[str, Any]] = []
                for c in part.get("components") or []:
                    if isinstance(c, dict):
                        comps.append(c)
                for area in part.get("areas") or []:
                    if isinstance(area, dict):
                        for c in area.get("components") or []:
                            if isinstance(c, dict):
                                comps.append(c)
                fiber_strs: list[str] = []
                for comp in comps:
                    mat = str(comp.get("material") or "").strip()
                    pct = str(comp.get("percentage") or "").strip()
                    if not mat:
                        continue
                    digits = re.sub(r"[^\d]", "", pct)
                    if digits:
                        pct_int = int(digits)
                        fiber_strs.append(f"%{pct_int} {mat}")
                        if "polyester" in turkish_lower(mat) or "poliester" in turkish_lower(mat):
                            if idx == 0 or "dış" in turkish_lower(part_desc) or "outer" in turkish_lower(part_desc):
                                outer_polyester = max(outer_polyester or 0, pct_int)
                            any_polyester = max(any_polyester or 0, pct_int)
                    else:
                        fiber_strs.append(mat)
                if fiber_strs:
                    prefix = f"{part_desc}: " if part_desc and len(detailed_comp.get("parts") or []) > 1 else ""
                    fabric_parts.append(prefix + ", ".join(dict.fromkeys(fiber_strs)))

        fabric_info = " | ".join(fabric_parts) if fabric_parts else None
        polyester_pct = outer_polyester if outer_polyester is not None else any_polyester
        if polyester_pct is None and fabric_info:
            polyester_pct = extract_polyester_percent(fabric_info)
        return available_sizes, is_critical_stock, fabric_info, polyester_pct

    def _fetch_zara_products(self) -> list[ProductItem]:
        cat_url = f"https://{self.config.domain}/{self.config.store_path}/categories?ajax=true"
        cat_payload = self._get_json(cat_url)
        categories = self._collect_categories(cat_payload.get("categories") or [])
        logger.info("[%s] Selected %d Zara categories (section=%s)", self.brand, len(categories), self.section)

        if not categories:
            return []

        raw_candidates: dict[str, tuple[dict[str, Any], _CategoryNode, float]] = {}
        category_failures = 0

        for idx, cat in enumerate(categories):
            if idx > 0:
                self.sleep_jitter()
            try:
                url = f"https://{self.config.domain}/{self.config.store_path}/category/{cat.category_id}/products?ajax=true"
                payload = self._get_json(url)
                found_in_cat = 0
                for group in payload.get("productGroups") or []:
                    for element in group.get("elements") or []:
                        for comp in element.get("commercialComponents") or []:
                            if comp.get("type") != "Product":
                                continue
                            pid = str(comp.get("id") or "").strip()
                            if not pid or pid in raw_candidates:
                                continue
                            cur_p = _cents_to_try(comp.get("price"))
                            orig_p = _cents_to_try(comp.get("oldPrice"))
                            if cur_p is None:
                                continue
                            if orig_p is not None and orig_p <= cur_p:
                                orig_p = None
                            if self.only_discounted and orig_p is None:
                                continue
                            if str(comp.get("availability") or "").lower() == "out_of_stock":
                                continue
                            disc_ratio = float((orig_p - cur_p) / orig_p) if orig_p and orig_p > cur_p else 0.0
                            raw_candidates[pid] = (comp, cat, disc_ratio)
                            found_in_cat += 1
                logger.info(
                    "[%s] Category %s (%d): collected %d candidates",
                    self.brand,
                    cat.name,
                    cat.category_id,
                    found_in_cat,
                )
            except ScraperBlockedError:
                raise
            except Exception as exc:  # noqa: BLE001
                category_failures += 1
                logger.warning(
                    "[%s] Skipping Zara category %s (%d): %s",
                    self.brand,
                    cat.name,
                    cat.category_id,
                    exc,
                )

        if category_failures == len(categories) and not raw_candidates:
            raise RuntimeError(f"All {len(categories)} Zara categories failed")

        # Sort by highest discount first so PDP detail budget goes to the strongest deals
        ordered_candidates = sorted(raw_candidates.values(), key=lambda entry: -entry[2])
        logger.info("[%s] Fetching PDP size/fabric details for up to %d of %d Zara candidates", self.brand, self.max_detail_fetches, len(ordered_candidates))

        products_by_id: dict[str, ProductItem] = {}
        detail_fetches = 0
        pdp_blocked = False

        for comp, cat, _ in ordered_candidates:
            pdp_payload: dict[str, Any] | None = None
            detail = comp.get("detail") or {}
            colors = detail.get("colors") or []
            has_inline_sizes = bool(colors and isinstance(colors[0], dict) and colors[0].get("sizes"))

            if not has_inline_sizes and not pdp_blocked and self.max_detail_fetches > 0:
                if detail_fetches >= self.max_detail_fetches:
                    break
                seo = comp.get("seo") or {}
                kw = str(seo.get("keyword") or "").strip()
                seo_pid = str(seo.get("seoProductId") or "").strip()
                prod_id = comp.get("id")
                if kw and seo_pid and prod_id:
                    if detail_fetches > 0:
                        time.sleep(random.uniform(0.25, 0.65))
                    pdp_url = f"https://{self.config.domain}/{self.config.store_path}/{kw}-p{seo_pid}.html?v1={prod_id}&ajax=true"
                    try:
                        pdp_payload = self._get_json(pdp_url)
                        detail_fetches += 1
                    except ScraperBlockedError as exc:
                        if not products_by_id:
                            raise
                        logger.warning("[%s] Zara PDP blocked after %d products (%s); stopping detail fetches.", self.brand, len(products_by_id), exc)
                        pdp_blocked = True
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("[%s] Zara PDP fetch failed for %s: %s", self.brand, prod_id, exc)
                        continue

            item = self._parse_zara_component(comp, cat, pdp_payload=pdp_payload)
            if item is not None and item.external_id not in products_by_id:
                products_by_id[item.external_id] = item

        return list(products_by_id.values())

    def _parse_zara_category_payload(
        self,
        payload: dict[str, Any],
        category: _CategoryNode,
    ) -> list[ProductItem]:
        items: list[ProductItem] = []
        for group in payload.get("productGroups") or []:
            for element in group.get("elements") or []:
                for comp in element.get("commercialComponents") or []:
                    if comp.get("type") != "Product":
                        continue
                    item = self._parse_zara_component(comp, category)
                    if item is not None:
                        items.append(item)
        return items

    def _parse_zara_component(
        self,
        comp: dict[str, Any],
        category: _CategoryNode,
        *,
        pdp_payload: dict[str, Any] | None = None,
    ) -> ProductItem | None:
        prod_id = comp.get("id")
        title = str(comp.get("name") or "").strip()
        if not prod_id or not title:
            return None
        if is_kids_product(title, category.path):
            return None

        current_price = _cents_to_try(comp.get("price"))
        original_price = _cents_to_try(comp.get("oldPrice"))
        if current_price is None:
            return None
        if original_price is not None and original_price <= current_price:
            original_price = None

        if self.only_discounted and original_price is None:
            return None

        detail = comp.get("detail") or {}
        colors = detail.get("colors") or []
        color = colors[0] if colors else {}
        color_id = str(color.get("id") or "").strip()

        # Extract sizes and composition from PDP AJAX payload if fetched, else inline component
        available_sizes: list[str] = []
        is_critical_stock = False
        fabric_info: str | None = None
        polyester_pct: int | None = None

        if pdp_payload is not None:
            (
                available_sizes,
                is_critical_stock,
                fabric_info,
                polyester_pct,
            ) = self._extract_zara_pdp_details(pdp_payload, target_color_id=color_id)

        if not available_sizes:
            for sz in color.get("sizes") or []:
                avail = str(sz.get("availability") or "").lower()
                if avail in {"in_stock", "low_on_stock"}:
                    sz_name = str(sz.get("name") or "").strip()
                    if sz_name:
                        available_sizes.append(sz_name)
                    if avail == "low_on_stock":
                        is_critical_stock = True

        # Extract image
        image_url: str | None = None
        for media in (color.get("xmedia") or comp.get("xmedia") or []):
            extra = media.get("extraInfo") or {}
            candidate = extra.get("deliveryUrl") or media.get("url")
            if isinstance(candidate, str) and candidate.startswith("http"):
                image_url = candidate.replace("{width}", "750")
                break

        seo = comp.get("seo") or {}
        kw = str(seo.get("keyword") or "").strip()
        seo_pid = str(seo.get("seoProductId") or "").strip()
        if kw and seo_pid:
            url = f"https://{self.config.domain}/{self.config.store_path}/{kw}-p{seo_pid}.html?v1={prod_id}"
        else:
            url = f"https://{self.config.domain}/{self.config.store_path}/search?searchTerm={prod_id}"

        inferred_cat = _infer_category(
            str(comp.get("familyName") or ""),
            str(comp.get("subfamilyName") or ""),
            title,
            category.path,
        )
        inferred_gender = infer_product_gender(
            str(comp.get("sectionName") or ""),
            category.section_hint,
            title,
            category.path,
            default=ProductGender.UNISEX,
        )

        return ProductItem(
            brand=self.brand,
            external_id=str(prod_id),
            title=title,
            category=inferred_cat,
            gender=inferred_gender,
            url=url,
            image_url=image_url,
            current_price=current_price,
            original_price=original_price,
            currency="TRY",
            available_sizes=available_sizes,
            is_critical_stock=is_critical_stock,
            fabric_info=fabric_info,
            polyester_percent=polyester_pct,
            raw_metadata={
                "category_id": category.category_id,
                "category_path": category.path,
                "section": comp.get("sectionName") or category.section_hint,
                "availability": comp.get("availability"),
            },
        )
