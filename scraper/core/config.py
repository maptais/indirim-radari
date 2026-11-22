"""Environment configuration and typed user settings models."""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SCRAPER_DIR = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = SCRAPER_DIR / ".env"


class AppSettings(BaseSettings):
    """Runtime settings loaded from `scraper/.env` or OS environment variables."""

    model_config = SettingsConfigDict(
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Supabase credentials (required)
    supabase_url: str = Field(..., min_length=1, description="Supabase project URL")
    supabase_service_role_key: SecretStr = Field(
        ...,
        description="Supabase service role key used for server-side read/write access",
    )

    # Gemini AI (Faz 4: style scoring)
    gemini_api_key: SecretStr | None = Field(
        default=None,
        description="Google Gemini API key for style scoring",
    )
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        description="Gemini model identifier used for product style scoring",
    )

    # Web Push / VAPID (optional until Faz 3)
    vapid_public_key: str | None = Field(default=None)
    vapid_private_key: SecretStr | None = Field(default=None)
    vapid_claims_sub: str = Field(default="mailto:you@example.com")

    # Scraper runtime controls
    scraper_run_mode: Literal["cloud", "local"] = Field(default="cloud")
    request_timeout_seconds: int = Field(default=20, ge=5, le=120)
    min_jitter_seconds: float = Field(default=1.0, ge=0.0, le=30.0)
    max_jitter_seconds: float = Field(default=3.0, ge=0.0, le=60.0)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(default="INFO")

    @field_validator("supabase_url")
    @classmethod
    def normalize_supabase_url(cls, value: str) -> str:
        cleaned = value.strip().rstrip("/")
        for suffix in ("/rest/v1", "/rest/v1/"):
            if cleaned.endswith(suffix):
                cleaned = cleaned[: -len(suffix)]
        return cleaned.rstrip("/")

    @model_validator(mode="after")
    def validate_jitter_bounds(self) -> AppSettings:
        if self.max_jitter_seconds < self.min_jitter_seconds:
            raise ValueError("MAX_JITTER_SECONDS must be >= MIN_JITTER_SECONDS")
        return self


class CategoryThreshold(BaseModel):
    """Discount and price budget threshold for a single product category."""

    min_discount_percent: int = Field(default=30, ge=0, le=100)
    max_price_try: Decimal = Field(default=Decimal("2500"), ge=Decimal("0"))


VALID_TARGET_GENDERS: tuple[str, ...] = ("men", "women", "unisex")


class UserSettings(BaseModel):
    """Typed representation of the singleton `public.user_settings` table row."""

    id: int = Field(default=1)
    target_genders: list[str] = Field(default_factory=lambda: ["men", "unisex"])
    top_sizes: list[str] = Field(default_factory=lambda: ["M", "L"])
    bottom_sizes: list[str] = Field(default_factory=lambda: ["32", "M", "L"])
    shoe_sizes: list[str] = Field(default_factory=lambda: ["42", "42.5", "43"])
    negative_keywords: list[str] = Field(default_factory=list)
    max_polyester_percent: int = Field(default=30, ge=0, le=100)
    excluded_fabrics: list[str] = Field(default_factory=list)
    category_thresholds: dict[str, CategoryThreshold] = Field(default_factory=dict)
    style_profile: str = Field(default="")
    min_ai_notify_score: int = Field(default=7, ge=1, le=10)
    price_history_lookback_days: int = Field(default=45, ge=7, le=365)
    notifications_enabled: bool = Field(default=True)

    @field_validator("target_genders", mode="before")
    @classmethod
    def normalize_target_genders(cls, value: object) -> list[str]:
        if not isinstance(value, (list, tuple, set)):
            return ["men", "unisex"]
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in value:
            token = str(item).strip().lower()
            if token in VALID_TARGET_GENDERS and token not in seen:
                seen.add(token)
                cleaned.append(token)
        return cleaned or ["men", "unisex"]

    def to_scraper_section(self) -> Literal["all", "men", "women"]:
        """Map `target_genders` to a scraper storefront section filter (`men`, `women`, or `all`)."""
        genders = set(self.target_genders)
        wants_men = "men" in genders
        wants_women = "women" in genders
        if wants_men and not wants_women:
            return "men"
        if wants_women and not wants_men:
            return "women"
        return "all"

    def get_threshold(self, category: str) -> CategoryThreshold:
        """Return threshold for a category, falling back to `other` or defaults."""
        if category in self.category_thresholds:
            return self.category_thresholds[category]
        if "other" in self.category_thresholds:
            return self.category_thresholds["other"]
        return CategoryThreshold()

    def get_target_sizes(self, category: str) -> list[str]:
        """Return user's preferred sizes matching a normalized product category."""
        if category == "shoes":
            return self.shoe_sizes
        if category == "bottoms":
            return self.bottom_sizes
        if category in {"tops", "outerwear"}:
            return self.top_sizes
        return self.top_sizes + self.bottom_sizes + self.shoe_sizes


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Return a cached `AppSettings` instance."""
    return AppSettings()
