"""Sportive (`sportive.com.tr`) shoe & sportswear discount scraper."""

from __future__ import annotations

from typing import Literal

from ..base import ProductCategory
from ..eren import AkinonBrandConfig, AkinonScraper

SPORTIVE_CONFIG = AkinonBrandConfig(
    brand="sportive",
    display_name="Sportive",
    domain="www.sportive.com.tr",
    category_paths={
        "all": ("/outlet/", "/erkek-spor-ayakkabi/", "/kadin-spor-ayakkabi/"),
        "men": ("/outlet/", "/erkek-spor-ayakkabi/", "/erkek-giyim/"),
        "women": ("/outlet/", "/kadin-spor-ayakkabi/", "/kadin-giyim/"),
    },
    default_sorter="smart-indirim",
    default_category=ProductCategory.SHOES,
)


class SportiveScraper(AkinonScraper):
    """Standalone scraper for Sportive backed by its Akinon JSON storefront."""

    engine_name = "sportive"

    def __init__(
        self,
        *,
        section: Literal["all", "men", "women"] = "all",
        only_discounted: bool = True,
        max_categories: int = 3,
        max_pages_per_category: int = 10,
        max_detail_fetches: int = 40,
        fetch_variant_details: bool = True,
        max_retries: int = 2,
        impersonate: str | None = None,
        timeout_seconds: int = 20,
        min_jitter_seconds: float = 0.3,
        max_jitter_seconds: float = 0.8,
    ) -> None:
        super().__init__(
            SPORTIVE_CONFIG,
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
