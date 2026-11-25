"""Standard product schema and abstract base scraper for all brand engines."""

from __future__ import annotations

import logging
import random
import time
from abc import ABC, abstractmethod
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class ProductCategory(StrEnum):
    """Canonical product categories used across scrapers, DB, and UI."""

    SHOES = "shoes"
    TOPS = "tops"
    BOTTOMS = "bottoms"
    OUTERWEAR = "outerwear"
    OTHER = "other"


class ProductGender(StrEnum):
    """Canonical product gender classifications used across scrapers, DB, and UI."""

    MEN = "men"
    WOMEN = "women"
    UNISEX = "unisex"


class ScrapeRunMode(StrEnum):
    """Execution environment mode for bot-protection tracking."""

    CLOUD = "cloud"
    LOCAL = "local"


class ScrapeStatus(StrEnum):
    """Outcome status recorded in `public.scrape_logs`."""

    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"


class ScraperBlockedError(RuntimeError):
    """Raised when a brand endpoint blocks the scraper (e.g. WAF/Cloudflare/Akamai)."""


class ProductItem(BaseModel):
    """Normalized product record returned by every brand scraper."""

    brand: str = Field(..., min_length=1, description="Canonical lowercase brand slug")
    external_id: str = Field(..., min_length=1, description="Store-specific unique product ID")
    title: str = Field(..., min_length=1, description="Product display title")
    category: ProductCategory = Field(default=ProductCategory.OTHER)
    gender: ProductGender = Field(default=ProductGender.UNISEX)
    url: str = Field(..., min_length=1, description="Direct product page URL")
    image_url: str | None = Field(default=None)
    current_price: Decimal = Field(..., ge=Decimal("0"))
    original_price: Decimal | None = Field(default=None, ge=Decimal("0"))
    cart_price: Decimal | None = Field(default=None, ge=Decimal("0"))
    promo_badge: str | None = Field(default=None)
    discount_percent: int | None = Field(default=None, ge=0, le=100)
    currency: str = Field(default="TRY", min_length=3, max_length=3)
    available_sizes: list[str] = Field(default_factory=list)
    is_critical_stock: bool = Field(default=False)
    fabric_info: str | None = Field(default=None)
    polyester_percent: int | None = Field(default=None, ge=0, le=100)
    raw_metadata: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @field_validator("brand")
    @classmethod
    def normalize_brand(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("external_id", "title", "url")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Field cannot be empty or whitespace")
        return cleaned

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("current_price", "original_price", "cart_price")
    @classmethod
    def quantize_price(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @field_validator("promo_badge")
    @classmethod
    def clean_promo_badge(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = " ".join(value.split()).strip()
        if not cleaned:
            return None
        return cleaned[:120]

    @field_validator("available_sizes")
    @classmethod
    def clean_sizes(cls, sizes: list[str]) -> list[str]:
        seen: set[str] = set()
        cleaned: list[str] = []
        for size in sizes:
            normalized = size.strip().upper()
            if normalized and normalized not in seen:
                seen.add(normalized)
                cleaned.append(normalized)
        return cleaned

    @property
    def effective_price(self) -> Decimal:
        """Return `cart_price` when a lower single-item cart price exists, else `current_price`."""
        if (
            self.cart_price is not None
            and self.cart_price > Decimal("0")
            and self.cart_price < self.current_price
        ):
            return self.cart_price
        return self.current_price

    @model_validator(mode="after")
    def compute_discount_percent(self) -> ProductItem:
        if self.cart_price is not None and (
            self.cart_price <= Decimal("0") or self.cart_price >= self.current_price
        ):
            self.cart_price = None

        if self.discount_percent is None:
            eff_price = self.effective_price
            ref_original: Decimal | None = None
            if (
                self.original_price is not None
                and self.original_price > Decimal("0")
                and self.original_price > eff_price
            ):
                ref_original = self.original_price
            elif eff_price < self.current_price and self.current_price > Decimal("0"):
                ref_original = self.current_price

            if ref_original is not None and ref_original > eff_price:
                ratio = (ref_original - eff_price) / ref_original
                computed = int((ratio * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                self.discount_percent = max(0, min(100, computed))
            else:
                self.discount_percent = 0
        return self

    def to_db_payload(
        self,
        *,
        is_real_discount: bool = False,
        lowest_price_days: int | None = None,
        historical_min_price: Decimal | None = None,
        ai_score: int | None = None,
        ai_comment: str | None = None,
    ) -> dict[str, Any]:
        """Serialize into a dictionary matching `public.products` columns."""
        payload: dict[str, Any] = {
            "brand": self.brand,
            "external_id": self.external_id,
            "title": self.title,
            "category": self.category.value,
            "gender": self.gender.value,
            "url": self.url,
            "image_url": self.image_url,
            "current_price": float(self.current_price),
            "original_price": float(self.original_price) if self.original_price is not None else None,
            "cart_price": float(self.cart_price) if self.cart_price is not None else None,
            "promo_badge": self.promo_badge,
            "discount_percent": self.discount_percent or 0,
            "currency": self.currency,
            "available_sizes": self.available_sizes,
            "is_critical_stock": self.is_critical_stock,
            "fabric_info": self.fabric_info,
            "polyester_percent": self.polyester_percent,
            "is_real_discount": is_real_discount,
            "lowest_price_days": lowest_price_days,
            "historical_min_price": (
                float(historical_min_price) if historical_min_price is not None else None
            ),
            "is_active": True,
        }
        if ai_score is not None:
            payload["ai_score"] = ai_score
        if ai_comment is not None:
            payload["ai_comment"] = ai_comment
        return payload


class ScrapeResult(BaseModel):
    """Structured execution result of a single brand scraper run."""

    brand: str
    engine: str
    status: ScrapeStatus
    products: list[ProductItem] = Field(default_factory=list)
    fetched_count: int = Field(default=0, ge=0)
    duration_ms: int = Field(default=0, ge=0)
    error_message: str | None = Field(default=None)


DEFAULT_BRAND_TIME_BUDGET_SECONDS = 210.0


class BaseScraper(ABC):
    """Abstract base class implemented by all brand and group scraper engines."""

    brand: str = ""
    engine_name: str = ""
    default_run_mode: ScrapeRunMode = ScrapeRunMode.CLOUD

    def __init__(
        self,
        *,
        timeout_seconds: int = 20,
        min_jitter_seconds: float = 1.0,
        max_jitter_seconds: float = 3.0,
        max_duration_seconds: float | None = DEFAULT_BRAND_TIME_BUDGET_SECONDS,
    ) -> None:
        if not self.brand:
            raise ValueError(f"{self.__class__.__name__} must define a non-empty `brand` attribute.")
        if not self.engine_name:
            raise ValueError(f"{self.__class__.__name__} must define a non-empty `engine_name` attribute.")
        if max_jitter_seconds < min_jitter_seconds:
            raise ValueError("max_jitter_seconds must be >= min_jitter_seconds")

        self.timeout_seconds = timeout_seconds
        self.min_jitter_seconds = min_jitter_seconds
        self.max_jitter_seconds = max_jitter_seconds
        self.max_duration_seconds: float | None = (
            float(max_duration_seconds)
            if max_duration_seconds is not None and max_duration_seconds > 0
            else None
        )
        self._budget_deadline: float | None = None
        self.budget_exceeded: bool = False

    def start_budget(self) -> None:
        """Start (or reset) the soft per-brand wall-clock deadline timer."""
        self.budget_exceeded = False
        if self.max_duration_seconds is not None:
            self._budget_deadline = time.monotonic() + self.max_duration_seconds
        else:
            self._budget_deadline = None

    def remaining_budget_seconds(self) -> float | None:
        """Return remaining seconds in the soft brand time budget, or None if unbounded."""
        if self._budget_deadline is None:
            return None
        return max(0.0, self._budget_deadline - time.monotonic())

    def is_budget_exceeded(self, reserve_seconds: float = 0.0) -> bool:
        """Return True once the soft per-brand time budget has elapsed (with optional reserve)."""
        if self._budget_deadline is None:
            return False
        if time.monotonic() >= (self._budget_deadline - max(0.0, reserve_seconds)):
            self.budget_exceeded = True
            return True
        return False

    @abstractmethod
    def fetch_products(self) -> list[ProductItem]:
        """Fetch and normalize currently discounted products for this brand."""

    def sleep_jitter(self, *, scale: float = 1.0) -> None:
        """Pause briefly with uniform random jitter to stay polite against rate limits."""
        if self.max_jitter_seconds <= 0 or scale <= 0:
            return
        if self.is_budget_exceeded():
            return
        delay = random.uniform(self.min_jitter_seconds, self.max_jitter_seconds) * scale
        remaining = self.remaining_budget_seconds()
        if remaining is not None:
            delay = min(delay, remaining)
        if delay > 0:
            time.sleep(delay)

    def close(self) -> None:
        """Release any underlying HTTP sessions or resources (override in subclasses)."""

    def __enter__(self) -> BaseScraper:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def run(self) -> ScrapeResult:
        """Execute `fetch_products` safely so a single brand failure never aborts the run."""
        started = time.perf_counter()
        self.start_budget()
        try:
            products = self.fetch_products()
            duration_ms = int((time.perf_counter() - started) * 1000)
            note = (
                f"Soft time budget reached ({int(self.max_duration_seconds or 0)}s); "
                f"preserved {len(products)} collected product(s)."
                if self.budget_exceeded
                else None
            )
            return ScrapeResult(
                brand=self.brand,
                engine=self.engine_name,
                status=ScrapeStatus.SUCCESS,
                products=products,
                fetched_count=len(products),
                duration_ms=duration_ms,
                error_message=note,
            )
        except ScraperBlockedError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.warning("Brand %s blocked by anti-bot protection: %s", self.brand, exc)
            return ScrapeResult(
                brand=self.brand,
                engine=self.engine_name,
                status=ScrapeStatus.BLOCKED,
                duration_ms=duration_ms,
                error_message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001 — intentional fault isolation per brand
            duration_ms = int((time.perf_counter() - started) * 1000)
            logger.exception("Brand %s scrape failed: %s", self.brand, exc)
            return ScrapeResult(
                brand=self.brand,
                engine=self.engine_name,
                status=ScrapeStatus.FAILED,
                duration_ms=duration_ms,
                error_message=f"{type(exc).__name__}: {exc}",
            )
