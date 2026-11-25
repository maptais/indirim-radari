"""Barçın (`barcin.com`) shoe & sportswear discount scraper."""

from __future__ import annotations

from typing import Literal

from ..base import DEFAULT_BRAND_TIME_BUDGET_SECONDS, ProductCategory
from ..eren import AkinonBrandConfig, AkinonScraper

BARCIN_CONFIG = AkinonBrandConfig(
    brand="barcin",
    display_name="Barçın",
    domain="www.barcin.com",
    category_paths={
        "all": ("/indirim/", "/outlet/", "/erkek/", "/kadin/", "/spor-ayakkabi/"),
        "men": ("/indirim/", "/outlet/", "/erkek/"),
        "women": ("/indirim/", "/outlet/", "/kadin/"),
    },
    default_sorter="indirim-oranina-gore",
    default_category=ProductCategory.SHOES,
)


class BarcinScraper(AkinonScraper):
    """Standalone scraper for Barçın Spor backed by its Akinon JSON storefront."""

    engine_name = "barcin"

    def __init__(
        self,
        *,
        section: Literal["all", "men", "women"] = "all",
        only_discounted: bool = True,
        max_categories: int = 5,
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
            BARCIN_CONFIG,
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
            max_duration_seconds=max_duration_seconds,
        )
