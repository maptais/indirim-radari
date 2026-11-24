"""Barçın (`barcin.com`) shoe & sportswear discount scraper."""

from __future__ import annotations

from typing import Literal

from ..base import ProductCategory
from ..eren import AkinonBrandConfig, AkinonScraper

BARCIN_CONFIG = AkinonBrandConfig(
    brand="barcin",
    display_name="Barçın",
    domain="www.barcin.com",
    category_paths={
        "all": ("/indirim/", "/outlet/", "/spor-ayakkabi/"),
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
        )
