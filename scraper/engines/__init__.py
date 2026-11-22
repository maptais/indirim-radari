"""Scraper engines and base abstractions."""

from .base import (
    BaseScraper,
    ProductCategory,
    ProductGender,
    ProductItem,
    ScrapeResult,
    ScrapeRunMode,
    ScrapeStatus,
    ScraperBlockedError,
)
from .eren import AkinonBrandConfig, AkinonScraper, EREN_BRAND_CONFIGS, ErenScraper
from .independents import (
    AdidasOutletScraper,
    BarcinScraper,
    InStreetScraper,
    PumaOutletScraper,
    SneaksUpScraper,
    SportiveScraper,
)
from .inditex import INDITEX_BRAND_CONFIGS, InditexBrandConfig, InditexScraper

__all__ = [
    "AdidasOutletScraper",
    "AkinonBrandConfig",
    "AkinonScraper",
    "BarcinScraper",
    "BaseScraper",
    "EREN_BRAND_CONFIGS",
    "ErenScraper",
    "INDITEX_BRAND_CONFIGS",
    "InStreetScraper",
    "InditexBrandConfig",
    "InditexScraper",
    "ProductCategory",
    "ProductGender",
    "ProductItem",
    "PumaOutletScraper",
    "ScrapeResult",
    "ScrapeRunMode",
    "ScrapeStatus",
    "ScraperBlockedError",
    "SneaksUpScraper",
    "SportiveScraper",
]
