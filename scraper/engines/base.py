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
    url: str = Field(..., min_length=1, description="Direct product page URL")
    image_url: str | None = Field(default=None)
    current_price: Decimal = Field(..., ge=Decimal("0"))
    original_price: Decimal | None = Field(default=None, ge=Decimal("0"))
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

    @field_validator("current_price", "original_price")
    @classmethod
    def quantize_price(cls, value: Decimal | None) -> Decimal | None:
        if value is None:
            return None
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

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

    @model_validator(mode="after")
    def compute_discount_percent(self) -> ProductItem:
        if self.discount_percent is None:
            if (
                self.original_price is not None
                and self.original_price > Decimal("0")
                and self.original_price > self.current_price
            ):
                ratio = (self.original_price - self.current_price) / self.original_price
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
            "url": self.url,
            "image_url": self.image_url,
            "current_price": float(self.current_price),
            "original_price": float(self.original_price) if self.original_price is not None else None,
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

    @abstractmethod
    def fetch_products(self) -> list[ProductItem]:
        """Fetch and normalize currently discounted products for this brand."""

    def sleep_jitter(self) -> None:
        """Pause briefly with uniform random jitter to stay polite against rate limits."""
        if self.max_jitter_seconds <= 0:
            return
        delay = random.uniform(self.min_jitter_seconds, self.max_jitter_seconds)
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
        try:
            products = self.fetch_products()
            duration_ms = int((time.perf_counter() - started) * 1000)
            return ScrapeResult(
                brand=self.brand,
                engine=self.engine_name,
                status=ScrapeStatus.SUCCESS,
                products=products,
                fetched_count=len(products),
                duration_ms=duration_ms,
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
