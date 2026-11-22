"""Shared Mango scraper engine for Mango Man (`mangoman`) and Mango Outlet (`mangooutlet`)."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal
from urllib.parse import quote, urljoin

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

_NEXT_F_PUSH_RE = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.DOTALL)
_KIDS_MARKERS = ("çocuk", "cocuk", "kids", "niño", "nino", "niña", "nina", "bebek", "baby", "teen")
_SALE_PATH_HINTS = ("indirim", "promosyon", "promo", "ozel-fiyat", "özel-fiyat", "sale", "outlet", "rebajas")


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
class MangoBrandConfig:
    """Storefront configuration for Mango Man or Mango Outlet Turkey."""

    brand: str
    display_name: str
    domain: str
    home_paths: dict[str, tuple[str, ...]]
    fallback_category_paths: dict[str, tuple[str, ...]]
    default_run_mode: ScrapeRunMode = ScrapeRunMode.CLOUD


MANGO_BRAND_CONFIGS: dict[str, MangoBrandConfig] = {
    "mangoman": MangoBrandConfig(
        brand="mangoman",
        display_name="Mango Man",
        domain="shop.mango.com",
        home_paths={
            "all": ("/tr/tr/h/erkek",),
            "men": ("/tr/tr/h/erkek",),
            "women": (),
        },
        fallback_category_paths={
            "all": (
                "/tr/tr/c/erkek/ceket/b5a3a3f6",
                "/tr/tr/c/erkek/palto/3a5ade78",
                "/tr/tr/c/erkek/h%C4%B1rka-ve-kazak/89e09112",
                "/tr/tr/c/erkek/sweatshirt/f0789c5b",
                "/tr/tr/c/erkek/gomlek/85ceefeb",
                "/tr/tr/c/erkek/pantolon/b126cc9c",
                "/tr/tr/c/erkek/jean/2402cc2c",
                "/tr/tr/c/erkek/tisort/05a59413",
            ),
            "men": (
                "/tr/tr/c/erkek/ceket/b5a3a3f6",
                "/tr/tr/c/erkek/palto/3a5ade78",
                "/tr/tr/c/erkek/h%C4%B1rka-ve-kazak/89e09112",
                "/tr/tr/c/erkek/sweatshirt/f0789c5b",
                "/tr/tr/c/erkek/gomlek/85ceefeb",
                "/tr/tr/c/erkek/pantolon/b126cc9c",
                "/tr/tr/c/erkek/jean/2402cc2c",
                "/tr/tr/c/erkek/tisort/05a59413",
            ),
            "women": (),
        },
        default_run_mode=ScrapeRunMode.CLOUD,
    ),
    "mangooutlet": MangoBrandConfig(
        brand="mangooutlet",
        display_name="Mango Outlet",
        domain="www.mangooutlet.com",
        home_paths={
            "all": ("/tr/tr/h/erkek", "/tr/tr/h/kadin"),
            "men": ("/tr/tr/h/erkek",),
            "women": ("/tr/tr/h/kadin",),
        },
        fallback_category_paths={
            "all": (
                "/tr/tr/c/erkek/ceket_c1234",
                "/tr/tr/c/kadin/ceket_c1234",
            ),
            "men": ("/tr/tr/c/erkek/ceket_c1234",),
            "women": ("/tr/tr/c/kadin/ceket_c1234",),
        },
        # Mango Outlet TR category routes currently enforce strict WAF HTTP 403 blocking
        default_run_mode=ScrapeRunMode.LOCAL,
    ),
}


class MangoScraper(BaseScraper):
    """Unified Mango scraper parsing Next.js App Router RSC (`__next_f`) payloads."""

    engine_name = "mango"

    def __init__(
        self,
        brand: str,
        *,
        section: Literal["all", "men", "women"] = "all",
        only_discounted: bool = True,
        max_categories: int = 5,
        max_detail_fetches: int = 24,
        fetch_variant_details: bool = True,
        max_retries: int = 2,
        impersonate: str | None = None,
        timeout_seconds: int = 20,
        min_jitter_seconds: float = 0.5,
        max_jitter_seconds: float = 1.2,
    ) -> None:
        brand_key = brand.strip().lower()
        if brand_key not in MANGO_BRAND_CONFIGS:
            supported = ", ".join(sorted(MANGO_BRAND_CONFIGS))
            raise ValueError(f"Unsupported Mango brand '{brand}'. Supported: {supported}")

        self.config = MANGO_BRAND_CONFIGS[brand_key]
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
        self.max_detail_fetches = max(0, max_detail_fetches)
        self.fetch_variant_details = fetch_variant_details

        self.base_url = f"https://{self.config.domain}"
        self.http = HttpClient(
            self.brand,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            impersonate=impersonate,
            default_headers={
                "Referer": f"{self.base_url}/tr/tr",
            },
        )

    def close(self) -> None:
        self.http.close()

    @staticmethod
    def _decode_next_f_chunks(page_html: str) -> str:
        """Unescape and join Next.js App Router `self.__next_f.push` string chunks."""
        chunks: list[str] = []
        for raw_chunk in _NEXT_F_PUSH_RE.findall(page_html or ""):
            try:
                decoded = json.loads(f'"{raw_chunk}"')
                if isinstance(decoded, str):
                    chunks.append(decoded)
            except json.JSONDecodeError:
                chunks.append(raw_chunk.replace('\\"', '"'))
        return "\n".join(chunks)

    @classmethod
    def extract_plp_products(cls, page_html: str) -> list[dict[str, Any]]:
        """Extract `plp.product` payload entries (each containing `product` and `prices`) from RSC HTML."""
        stream = cls._decode_next_f_chunks(page_html)
        if not stream:
            return []

        items: list[dict[str, Any]] = []
        for match in re.finditer(r'"dataTestId"\s*:\s*"plp\.product"', stream):
            pos = match.start()
            brace_idx = stream.rfind("{", max(0, pos - 40), pos)
            if brace_idx == -1:
                continue
            obj = _extract_balanced_json(stream, brace_idx, "{", "}")
            if isinstance(obj, dict) and isinstance(obj.get("product"), dict):
                items.append(obj)

        return items

    @classmethod
    def extract_detail_metadata(
        cls,
        detail_html: str,
    ) -> tuple[list[str], bool, str | None, int | None]:
        """Extract available sizes, critical stock flag, and composition from Mango PDP RSC HTML."""
        stream = cls._decode_next_f_chunks(detail_html)
        if not stream:
            return [], False, None, None

        available_sizes: list[str] = []
        is_critical_stock = False

        # Find `"sizes": [...]` blocks that contain `"available":`
        for match in re.finditer(r'"sizes"\s*:\s*\[', stream):
            arr_start = stream.find("[", match.start())
            sizes_arr = _extract_balanced_json(stream, arr_start, "[", "]")
            if not isinstance(sizes_arr, list) or not sizes_arr:
                continue
            if not any(isinstance(s, dict) and "available" in s for s in sizes_arr):
                continue

            for entry in sizes_arr:
                if not isinstance(entry, dict):
                    continue
                if entry.get("available") is not True:
                    continue
                label = str(entry.get("label") or entry.get("shortDescription") or "").strip()
                if label and label not in available_sizes:
                    available_sizes.append(label)
                last_units = entry.get("lastUnits")
                if last_units is True or (isinstance(last_units, (int, float)) and last_units > 0):
                    is_critical_stock = True
            if available_sizes:
                break

        if 0 < len(available_sizes) <= 2:
            is_critical_stock = True

        # Extract fabric / composition if present in RSC chunks
        fabric_info: str | None = None
        comp_match = re.search(
            r'"(?:composition|compositions|materials?)"\s*:\s*"([^"]{4,300})"',
            stream,
            re.IGNORECASE,
        )
        if comp_match:
            fabric_info = comp_match.group(1).strip()
        else:
            # Fallback: look for Turkish composition percentages like "%100 pamuk"
            pct_matches = re.findall(
                r"%\s*\d{1,3}\s*[a-zA-ZçğıöşüÇĞİÖŞÜ]+(?:\s*,\s*%\s*\d{1,3}\s*[a-zA-ZçğıöşüÇĞİÖŞÜ]+)*",
                stream,
            )
            for cand in pct_matches:
                lower_c = turkish_lower(cand)
                if any(
                    fab in lower_c
                    for fab in ("pamuk", "polyester", "yün", "yun", "keten", "viskon", "elastan", "poliamid", "akrilik", "naylon", "deri")
                ):
                    fabric_info = cand.strip()
                    break

        polyester_pct = extract_polyester_percent(fabric_info)
        return available_sizes, is_critical_stock, fabric_info, polyester_pct

    @staticmethod
    def _extract_prices(
        prices_map: dict[str, Any],
        default_color_id: str,
    ) -> tuple[Decimal | None, Decimal | None]:
        """Extract `(current_price, original_price)` from Mango's `prices` dictionary."""
        if not isinstance(prices_map, dict) or not prices_map:
            return None, None

        color_prices = prices_map.get(default_color_id)
        if not isinstance(color_prices, dict):
            color_prices = next((v for v in prices_map.values() if isinstance(v, dict)), None)
        if not isinstance(color_prices, dict):
            return None, None

        price_block = color_prices.get("default")
        if not isinstance(price_block, dict):
            price_block = color_prices

        current_price = parse_try_price(
            price_block.get("salePrice")
            or price_block.get("discountedPrice")
            or price_block.get("price")
        )
        if current_price is None:
            return None, None

        original_candidates: list[Decimal] = []
        for orig_key in (
            "previousPrice",
            "originalPrice",
            "oldPrice",
            "crossedOutPrice",
            "strikethroughPrice",
            "firstStrikethroughPrice",
            "pvp",
        ):
            val = parse_try_price(price_block.get(orig_key))
            if val is not None and val > current_price:
                original_candidates.append(val)

        crossed_list = price_block.get("crossedOutPrices") or price_block.get("previousPrices") or []
        if isinstance(crossed_list, list):
            for entry in crossed_list:
                raw_val = entry.get("price") if isinstance(entry, dict) else entry
                val = parse_try_price(raw_val)
                if val is not None and val > current_price:
                    original_candidates.append(val)

        original_price = max(original_candidates) if original_candidates else None
        return current_price, original_price

    @staticmethod
    def _extract_image_url(product: dict[str, Any], default_color_id: str) -> str | None:
        assets_domain = str(product.get("assetsDomain") or "https://media.mango.com").rstrip("/")
        colors = product.get("colors") or []
        chosen_color: dict[str, Any] | None = None
        for col in colors:
            if isinstance(col, dict) and str(col.get("id") or "") == default_color_id:
                chosen_color = col
                break
        if chosen_color is None and colors and isinstance(colors[0], dict):
            chosen_color = colors[0]

        if chosen_color is None:
            return None

        looks = chosen_color.get("looks") or {}
        if isinstance(looks, dict):
            for look in looks.values():
                if not isinstance(look, dict):
                    continue
                images = look.get("images") or {}
                if isinstance(images, dict):
                    for img_obj in images.values():
                        if isinstance(img_obj, dict) and isinstance(img_obj.get("img"), str):
                            img_path = img_obj["img"]
                            if img_path.startswith("http"):
                                return img_path
                            return f"{assets_domain}{img_path}?wid=640&hei=896&fit=crop"

        bullet = chosen_color.get("bulletImg")
        if isinstance(bullet, str) and bullet:
            return bullet if bullet.startswith("http") else f"{assets_domain}{bullet}"
        return None

    def parse_plp_entry(
        self,
        entry: dict[str, Any],
        *,
        category_path: str = "",
        detail_html: str | None = None,
    ) -> ProductItem | None:
        """Convert a Mango `plp.product` RSC object (+ optional PDP HTML) into a `ProductItem`."""
        product = entry.get("product") or {}
        if not isinstance(product, dict):
            return None

        title = str(product.get("name") or "").strip()
        ref = str(product.get("reference") or product.get("id") or "").strip()
        rel_url = str(product.get("url") or "").strip()
        if not title or not ref or not rel_url:
            return None

        combined_check = turkish_lower(f"{title} {rel_url} {category_path}")
        if any(k in combined_check for k in _KIDS_MARKERS):
            return None

        default_color_id = str(entry.get("defaultColorId") or "").strip()
        if not default_color_id:
            colors = product.get("colors") or []
            if colors and isinstance(colors[0], dict):
                default_color_id = str(colors[0].get("id") or "")

        current_price, original_price = self._extract_prices(
            entry.get("prices") or {},
            default_color_id,
        )
        if current_price is None:
            return None
        if self.only_discounted and original_price is None:
            return None

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
            # Fallback to listing color sizes when detail fetch is disabled
            for col in product.get("colors") or []:
                if not isinstance(col, dict):
                    continue
                if default_color_id and str(col.get("id") or "") != default_color_id:
                    continue
                for sz in col.get("sizes") or []:
                    if isinstance(sz, dict):
                        if sz.get("available") is False:
                            continue
                        label = str(sz.get("label") or sz.get("shortDescription") or "").strip()
                        if label:
                            available_sizes.append(label)
                if available_sizes:
                    break

        if self.fetch_variant_details and detail_html is not None and not available_sizes:
            return None

        family_hints = " ".join(
            f"{f.get('label', '')} {f.get('labelEn', '')}"
            for f in (product.get("families") or [])
            if isinstance(f, dict)
        )
        inferred_cat = infer_product_category(
            title,
            family_hints,
            rel_url,
            category_path,
            default=ProductCategory.TOPS,
        )

        external_id = f"{ref}-{default_color_id}" if default_color_id else ref

        return ProductItem(
            brand=self.brand,
            external_id=external_id,
            title=title,
            category=inferred_cat,
            url=urljoin(f"{self.base_url}/", quote(rel_url.lstrip("/"), safe="/:?=&%")),
            image_url=self._extract_image_url(product, default_color_id),
            current_price=current_price,
            original_price=original_price,
            currency="TRY",
            available_sizes=available_sizes,
            is_critical_stock=is_critical_stock,
            fabric_info=fabric_info,
            polyester_percent=polyester_pct,
            raw_metadata={
                "reference": ref,
                "color_id": default_color_id,
                "category_path": category_path,
            },
        )

    def _discover_category_paths(self) -> list[str]:
        """Discover promo/sale and clothing category paths from Mango homepages, prioritizing sale links."""
        home_paths = self.config.home_paths.get(self.section)
        if home_paths is None:
            home_paths = self.config.home_paths["all"]

        if not home_paths:
            return []

        discovered_sale: list[str] = []
        discovered_regular: list[str] = []
        seen: set[str] = set()

        for home_path in home_paths:
            try:
                html = self.http.get_text(f"{self.base_url}{home_path}")
            except ScraperBlockedError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.debug("[%s] Home discovery failed on %s: %s", self.brand, home_path, exc)
                continue

            for match in re.findall(r'(/tr/tr/c/(?:erkek|kadin)/[^"\\\s<>]+/[a-f0-9]{6,12})', html):
                lower_m = turkish_lower(match)
                if match in seen:
                    continue
                if any(skip in lower_m for skip in ("cuzdan", "aksesuar", "gunes-gozlugu", "ic-camas", "kravat", "canta", "pijama", "kemer", "bere", "atki", "mayo")):
                    continue
                seen.add(match)
                if any(hint in lower_m for hint in _SALE_PATH_HINTS):
                    discovered_sale.append(match)
                else:
                    discovered_regular.append(match)

        fallbacks = list(
            self.config.fallback_category_paths.get(self.section)
            or self.config.fallback_category_paths["all"]
        )
        ordered = discovered_sale + [p for p in fallbacks if p not in seen] + discovered_regular
        return ordered[: self.max_categories]

    def fetch_products(self) -> list[ProductItem]:
        """Fetch discounted Mango Man or Mango Outlet products."""
        if self.brand == "mangoman" and self.section == "women":
            logger.info("[mangoman] Skipping because section='women' does not match Mango Man.")
            return []

        selected_paths = self._discover_category_paths()
        if not selected_paths:
            return []

        candidates: list[tuple[dict[str, Any], str, float]] = []
        seen_ids: set[str] = set()
        cat_failures = 0

        for idx, path in enumerate(selected_paths):
            if idx > 0:
                self.sleep_jitter()

            url = f"{self.base_url}{quote(path, safe='/:?=&%')}"
            try:
                page_html = self.http.get_text(url)
            except ScraperBlockedError:
                raise
            except Exception as exc:  # noqa: BLE001
                cat_failures += 1
                logger.warning("[%s] Category %s failed: %s", self.brand, path, exc)
                continue

            entries = self.extract_plp_products(page_html)
            for entry in entries:
                prod = entry.get("product") or {}
                ref = str(prod.get("reference") or prod.get("id") or "").strip()
                color_id = str(entry.get("defaultColorId") or "").strip()
                ext_id = f"{ref}-{color_id}" if color_id else ref
                if not ref or ext_id in seen_ids:
                    continue

                cur_p, orig_p = self._extract_prices(entry.get("prices") or {}, color_id)
                if cur_p is None:
                    continue
                if self.only_discounted and orig_p is None:
                    continue

                disc_ratio = float((orig_p - cur_p) / orig_p) if orig_p and orig_p > cur_p else 0.0
                seen_ids.add(ext_id)
                candidates.append((entry, path, disc_ratio))

        if cat_failures == len(selected_paths) and not candidates:
            raise RuntimeError(f"All {len(selected_paths)} Mango categories failed for {self.brand}")

        candidates.sort(key=lambda item: -item[2])
        logger.info(
            "[%s] Collected %d discounted listing candidates across %d categories",
            self.brand,
            len(candidates),
            len(selected_paths),
        )

        products: list[ProductItem] = []
        detail_fetches = 0
        pdp_blocked = False

        for entry, path, _ in candidates:
            detail_html: str | None = None
            if self.fetch_variant_details and not pdp_blocked:
                if detail_fetches >= self.max_detail_fetches:
                    break
                if detail_fetches > 0:
                    self.sleep_jitter()
                rel_url = str((entry.get("product") or {}).get("url") or "").strip()
                if not rel_url:
                    continue
                pdp_url = urljoin(f"{self.base_url}/", quote(rel_url.lstrip("/"), safe="/:?=&%"))
                try:
                    detail_html = self.http.get_text(pdp_url)
                    detail_fetches += 1
                except ScraperBlockedError as exc:
                    logger.warning(
                        "[%s] PDP blocked by WAF (%s); falling back to listing sizes for remaining items.",
                        self.brand,
                        exc,
                    )
                    pdp_blocked = True
                    detail_html = None
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[%s] PDP fetch failed for %s: %s", self.brand, pdp_url, exc)
                    detail_html = None

            item = self.parse_plp_entry(
                entry,
                category_path=path,
                detail_html=detail_html,
            )
            if item is not None:
                products.append(item)

        logger.info("[%s] Fetched %d discounted products", self.brand, len(products))
        return products
