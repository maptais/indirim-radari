"""Product filtering core based on user sizes, keywords, fabrics, and category thresholds."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from decimal import Decimal

from pydantic import BaseModel, Field

from .config import UserSettings
from ..engines.base import ProductCategory, ProductItem

_ALPHA_SIZES = frozenset({"XXS", "XS", "S", "M", "L", "XL", "XXL", "2XL", "3XL", "4XL", "OS", "ONE SIZE", "STD"})
_PREFIX_PATTERN = re.compile(r"^(?:EU|TR|UK|US|EUR|SIZE|BEDEN|NUMARA|NO)\s*[:\-]?\s*", re.IGNORECASE)
_SUFFIX_PATTERN = re.compile(r"\s*[:\-]?\s*(?:EU|TR|EUR|NUMARA|NO)$", re.IGNORECASE)
_FRACTION_HALF_PATTERN = re.compile(r"(\d+)(?:\s+1/2|\s*½)(?!\d)")
_FRACTION_TWO_THIRDS_PATTERN = re.compile(r"(\d+)(?:\s+2/3|\s*⅔)(?!\d)")
_FRACTION_ONE_THIRD_PATTERN = re.compile(r"(\d+)(?:\s+1/3|\s*⅓)(?!\d)")
_WAIST_LENGTH_PATTERN = re.compile(r"^W?(\d{2,3})\s*[/\-xX]\s*L?\d{2,3}$")
_SHOE_RANGE_PATTERN = re.compile(r"^(\d{2}(?:\.5)?)\s*[/\-]\s*(\d{2}(?:\.5)?)$")
_POLYESTER_BEFORE_PATTERN = re.compile(r"%\s*(\d{1,3})\s*(?:polyester|poliester)", re.IGNORECASE)
_POLYESTER_AFTER_PATTERN = re.compile(r"(\d{1,3})\s*%\s*(?:polyester|poliester)", re.IGNORECASE)
_POLYESTER_REVERSE_PATTERN = re.compile(r"(?:polyester|poliester)\s*[:\-]?\s*%?\s*(\d{1,3})\s*%?", re.IGNORECASE)


def turkish_lower(text: str) -> str:
    """Lowercase text with Turkish dotted/dotless I rules preserved."""
    if not text:
        return ""
    mapped = text.replace("İ", "i").replace("I", "ı").lower()
    # Remove stray combining dot above (U+0307) left by Unicode lowercasing of İ
    mapped = mapped.replace("\u0307", "")
    return unicodedata.normalize("NFC", mapped)


def extract_polyester_percent(fabric_info: str | None) -> int | None:
    """Extract the highest polyester percentage mentioned in a fabric description."""
    if not fabric_info:
        return None
    normalized = turkish_lower(fabric_info)
    matches: list[int] = []
    for pattern in (_POLYESTER_BEFORE_PATTERN, _POLYESTER_AFTER_PATTERN, _POLYESTER_REVERSE_PATTERN):
        for raw in pattern.findall(normalized):
            val = int(raw)
            if 0 <= val <= 100:
                matches.append(val)
    return max(matches) if matches else None


def _normalize_numeric_fragment(value: str) -> set[str]:
    """Normalize decimal/fractional shoe and apparel numeric fragments into comparable tokens."""
    out: set[str] = set()
    cleaned = _PREFIX_PATTERN.sub("", value.strip()).strip()
    cleaned = _SUFFIX_PATTERN.sub("", cleaned).strip()
    if not cleaned:
        return out

    cleaned = re.sub(r"(\d),(\d)", r"\1.\2", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # Keep exact third-fraction tokens while adding standard .5 / integer EU equivalents (Adidas EU sizing)
    if _FRACTION_TWO_THIRDS_PATTERN.search(cleaned):
        exact = _FRACTION_TWO_THIRDS_PATTERN.sub(r"\1 2/3", cleaned)
        out.add(exact)
        half_equiv = _FRACTION_TWO_THIRDS_PATTERN.sub(r"\1.5", cleaned)
        cleaned = half_equiv
    elif _FRACTION_ONE_THIRD_PATTERN.search(cleaned):
        exact = _FRACTION_ONE_THIRD_PATTERN.sub(r"\1 1/3", cleaned)
        out.add(exact)
        int_equiv = _FRACTION_ONE_THIRD_PATTERN.sub(r"\1", cleaned)
        cleaned = int_equiv

    cleaned = _FRACTION_HALF_PATTERN.sub(r"\1.5", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    if re.fullmatch(r"\d+\.0+", cleaned):
        cleaned = cleaned.split(".")[0]

    out.add(cleaned)
    return out


def normalize_size_tokens(raw_size: str, category: ProductCategory | str = ProductCategory.OTHER) -> set[str]:
    """Normalize a store size label into comparable canonical tokens."""
    if not raw_size:
        return set()

    text = raw_size.strip().upper()
    if not text:
        return set()

    category_val = category.value if isinstance(category, ProductCategory) else str(category)
    is_shoes = category_val == ProductCategory.SHOES.value

    # Strip parenthetical notes like "M (TR 38-40)" or "42.5 (27 CM)" -> also inspect inner text
    base = re.sub(r"\([^)]*\)", "", text).strip()
    paren_parts = re.findall(r"\(([^)]*)\)", text)

    candidates = [text, base, *paren_parts]
    tokens: set[str] = set()

    for part in candidates:
        part_stripped = part.strip()
        if not part_stripped:
            continue

        # Handle multi-standard strings like "EU 42,5 / UK 8 / US 9" or "US 9 | EU 42 2/3"
        if any(sep in part_stripped for sep in ("/", "|")):
            segments = [seg.strip() for seg in re.split(r"[/|]", part_stripped) if seg.strip()]
            eu_segments = [
                seg
                for seg in segments
                if re.match(r"^(?:EU|TR|EUR)\b", seg, re.IGNORECASE)
                or re.search(r"\b(?:EU|TR|EUR)$", seg, re.IGNORECASE)
            ]
            if eu_segments:
                for seg in eu_segments:
                    tokens.update(_normalize_numeric_fragment(seg))
                continue

        normalized_set = _normalize_numeric_fragment(part_stripped)
        tokens.update(normalized_set)

        for cleaned in list(normalized_set):
            # Handle shoe size ranges like "42-43" or "42/43" (common for slides/slippers)
            if is_shoes:
                shoe_range = _SHOE_RANGE_PATTERN.match(cleaned)
                if shoe_range:
                    low_val, high_val = float(shoe_range.group(1)), float(shoe_range.group(2))
                    if 30 <= low_val <= 52 and 30 <= high_val <= 52 and abs(high_val - low_val) <= 2.0:
                        tokens.add(shoe_range.group(1))
                        tokens.add(shoe_range.group(2))
                        continue

            # Handle waist/length notations like 32/32 or W32/L32 for bottoms (extract waist only)
            waist_match = _WAIST_LENGTH_PATTERN.match(cleaned)
            if waist_match and not is_shoes:
                tokens.add(waist_match.group(1))
                continue

            # Handle combined alpha sizes like M-L or M/L
            if "-" in cleaned or "/" in cleaned:
                split_parts = [p.strip() for p in re.split(r"[-/]", cleaned) if p.strip()]
                if split_parts and all(p in _ALPHA_SIZES for p in split_parts):
                    tokens.update(split_parts)

    # For shoes, prefer normalized numeric shoe tokens (including 1/3 & 2/3) over unrelated text
    if is_shoes:
        numeric_tokens = {
            t for t in tokens if re.fullmatch(r"\d+(?:\.5| [12]/3)?", t)
        }
        if numeric_tokens:
            return numeric_tokens

    return tokens


def match_sizes(
    available_sizes: list[str],
    target_sizes: list[str],
    category: ProductCategory | str = ProductCategory.OTHER,
) -> list[str]:
    """Return available store sizes that match the user's target sizes."""
    if not available_sizes or not target_sizes:
        return []

    target_tokens: set[str] = set()
    for target in target_sizes:
        target_tokens.update(normalize_size_tokens(target, category=category))

    if not target_tokens:
        return []

    matched: list[str] = []
    for size in available_sizes:
        size_tokens = normalize_size_tokens(size, category=category)
        if size_tokens & target_tokens:
            matched.append(size)
    return matched


class FilterResult(BaseModel):
    """Detailed outcome of evaluating a single product against user settings."""

    passed: bool
    reasons: list[str] = Field(default_factory=list)
    matched_sizes: list[str] = Field(default_factory=list)
    effective_polyester_percent: int | None = Field(default=None)


class FilterBatchSummary(BaseModel):
    """Aggregate statistics for a filtered batch of products."""

    total_count: int = 0
    passed_count: int = 0
    rejected_count: int = 0
    rejection_breakdown: dict[str, int] = Field(default_factory=dict)


DEFAULT_MIN_CATALOG_DISCOUNT_PERCENT = 15


class ProductFilter:
    """Evaluates products against size, keyword, fabric, discount, and budget rules."""

    def __init__(
        self,
        user_settings: UserSettings,
        *,
        min_catalog_discount_percent: int = DEFAULT_MIN_CATALOG_DISCOUNT_PERCENT,
    ) -> None:
        self.user_settings = user_settings
        self.min_catalog_discount_percent = max(0, min(100, min_catalog_discount_percent))
        self._target_genders = {g.strip().lower() for g in user_settings.target_genders if g.strip()}
        self._negative_keywords = [
            turkish_lower(kw.strip()) for kw in user_settings.negative_keywords if kw.strip()
        ]
        self._excluded_fabrics = [
            turkish_lower(fab.strip()) for fab in user_settings.excluded_fabrics if fab.strip()
        ]

    def evaluate_for_catalog(self, product: ProductItem) -> FilterResult:
        """Relaxed catalog gate for saving discounted deals into Supabase `products`.

        Keeps products that match `target_genders`, have a positive price, and carry
        either at least `min_catalog_discount_percent` (>= 15%) off OR a cart/promo
        deal — even if the listing page did not return size or fabric metadata.
        """
        reasons: list[str] = []

        if self._target_genders and product.gender.value not in self._target_genders:
            reasons.append(f"gender_mismatch:{product.gender.value}")

        effective_price = product.effective_price
        if effective_price <= Decimal("0"):
            reasons.append("invalid_price")

        discount = product.discount_percent or 0
        has_cart_or_promo = (
            product.cart_price is not None and product.cart_price < product.current_price
        ) or bool(product.promo_badge)
        if discount < self.min_catalog_discount_percent and not has_cart_or_promo:
            reasons.append(
                f"catalog_discount_below_min:{discount}%<{self.min_catalog_discount_percent}%"
            )

        target_sizes = self.user_settings.get_target_sizes(product.category.value)
        matched_sizes = match_sizes(
            product.available_sizes,
            target_sizes,
            category=product.category,
        )
        effective_polyester = (
            product.polyester_percent
            if product.polyester_percent is not None
            else extract_polyester_percent(product.fabric_info)
        )

        return FilterResult(
            passed=len(reasons) == 0,
            reasons=reasons,
            matched_sizes=matched_sizes,
            effective_polyester_percent=effective_polyester,
        )

    def filter_for_catalog(
        self,
        products: list[ProductItem],
    ) -> tuple[list[tuple[ProductItem, FilterResult]], FilterBatchSummary]:
        """Filter scraped products using the relaxed catalog gate for DB storage."""
        passed_items: list[tuple[ProductItem, FilterResult]] = []
        breakdown: Counter[str] = Counter()

        for product in products:
            result = self.evaluate_for_catalog(product)
            if result.passed:
                if product.polyester_percent is None and result.effective_polyester_percent is not None:
                    product = product.model_copy(
                        update={"polyester_percent": result.effective_polyester_percent}
                    )
                passed_items.append((product, result))
            else:
                for reason in result.reasons:
                    bucket = reason.split(":", 1)[0]
                    breakdown[bucket] += 1

        summary = FilterBatchSummary(
            total_count=len(products),
            passed_count=len(passed_items),
            rejected_count=len(products) - len(passed_items),
            rejection_breakdown=dict(breakdown),
        )
        return passed_items, summary

    def evaluate(self, product: ProductItem) -> FilterResult:
        """Evaluate all filter rules for a single product."""
        reasons: list[str] = []

        # 0. Dynamic gender preference filter
        if self._target_genders and product.gender.value not in self._target_genders:
            reasons.append(f"gender_mismatch:{product.gender.value}")

        # 1. Price validity & category thresholds (use effective cart_price when available)
        threshold = self.user_settings.get_threshold(product.category.value)
        effective_price = product.effective_price
        if effective_price <= Decimal("0"):
            reasons.append("invalid_price")
        elif effective_price > threshold.max_price_try:
            reasons.append(
                f"price_above_max:{effective_price}>{threshold.max_price_try}"
            )

        discount = product.discount_percent or 0
        if discount < threshold.min_discount_percent:
            reasons.append(
                f"discount_below_min:{discount}%<{threshold.min_discount_percent}%"
            )

        # 2. Exact / normalized size matching
        target_sizes = self.user_settings.get_target_sizes(product.category.value)
        matched_sizes = match_sizes(
            product.available_sizes,
            target_sizes,
            category=product.category,
        )
        if not product.available_sizes:
            reasons.append("no_sizes_in_stock")
        elif not matched_sizes:
            reasons.append("size_mismatch")

        # 3. Negative keywords (title + optional raw description)
        searchable_parts = [product.title]
        raw_desc = product.raw_metadata.get("description") if product.raw_metadata else None
        if isinstance(raw_desc, str) and raw_desc.strip():
            searchable_parts.append(raw_desc)
        searchable_text = turkish_lower(" ".join(searchable_parts))

        for keyword in self._negative_keywords:
            if keyword and keyword in searchable_text:
                reasons.append(f"negative_keyword:{keyword}")
                break

        # 4. Fabric & polyester rules (only enforced when composition/fabric text is present)
        effective_polyester = (
            product.polyester_percent
            if product.polyester_percent is not None
            else extract_polyester_percent(product.fabric_info)
        )
        if (
            effective_polyester is not None
            and effective_polyester > self.user_settings.max_polyester_percent
        ):
            reasons.append(
                f"polyester_above_max:{effective_polyester}%>{self.user_settings.max_polyester_percent}%"
            )

        fabric_search_text = turkish_lower(
            " ".join(part for part in (product.fabric_info or "", product.title) if part)
        )
        for excluded_fabric in self._excluded_fabrics:
            if excluded_fabric and excluded_fabric in fabric_search_text:
                reasons.append(f"excluded_fabric:{excluded_fabric}")
                break

        return FilterResult(
            passed=len(reasons) == 0,
            reasons=reasons,
            matched_sizes=matched_sizes,
            effective_polyester_percent=effective_polyester,
        )

    def filter_products(
        self,
        products: list[ProductItem],
    ) -> tuple[list[tuple[ProductItem, FilterResult]], FilterBatchSummary]:
        """Filter a list of products and return passing items along with rejection metrics."""
        passed_items: list[tuple[ProductItem, FilterResult]] = []
        breakdown: Counter[str] = Counter()

        for product in products:
            result = self.evaluate(product)
            if result.passed:
                # Populate parsed polyester_percent if filter extracted it from fabric_info
                if product.polyester_percent is None and result.effective_polyester_percent is not None:
                    product = product.model_copy(
                        update={"polyester_percent": result.effective_polyester_percent}
                    )
                passed_items.append((product, result))
            else:
                for reason in result.reasons:
                    bucket = reason.split(":", 1)[0]
                    breakdown[bucket] += 1

        summary = FilterBatchSummary(
            total_count=len(products),
            passed_count=len(passed_items),
            rejected_count=len(products) - len(passed_items),
            rejection_breakdown=dict(breakdown),
        )
        return passed_items, summary


def evaluate_product(product: ProductItem, user_settings: UserSettings) -> FilterResult:
    """Convenience helper to evaluate a single product."""
    return ProductFilter(user_settings).evaluate(product)


def filter_products(
    products: list[ProductItem],
    user_settings: UserSettings,
) -> tuple[list[tuple[ProductItem, FilterResult]], FilterBatchSummary]:
    """Convenience helper to filter a batch of products."""
    return ProductFilter(user_settings).filter_products(products)
