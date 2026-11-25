"""CLI entrypoint and orchestration pipeline for İndirim Radarı scrapers."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from typing import Literal

# Allow running both as `python scraper/main.py` and `python -m scraper.main`
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scraper.core.ai_scorer import AIStyleScorer, is_new_or_price_dropped
from scraper.core.config import VALID_TARGET_GENDERS, AppSettings, UserSettings, get_settings
from scraper.core.db import DatabaseClient
from scraper.core.filters import FilterBatchSummary, FilterResult, ProductFilter
from scraper.core.notifier import PushNotifier
from scraper.core.price_analyzer import PriceAnalysis, PriceAnalyzer
from scraper.engines.base import (
    DEFAULT_BRAND_TIME_BUDGET_SECONDS,
    BaseScraper,
    ProductItem,
    ScrapeRunMode,
    ScrapeStatus,
)
from scraper.engines.eren import EREN_BRAND_CONFIGS, ErenScraper
from scraper.engines.independents import (
    AdidasOutletScraper,
    BarcinScraper,
    DeFactoScraper,
    HMScraper,
    InStreetScraper,
    JackJonesScraper,
    MaviScraper,
    PumaOutletScraper,
    SneaksUpScraper,
    SportiveScraper,
)
from scraper.engines.inditex import INDITEX_BRAND_CONFIGS, InditexScraper
from scraper.engines.mango import MANGO_BRAND_CONFIGS, MangoScraper
from scraper.engines.trendyol import TRENDYOL_BRAND_CONFIGS, TrendyolScraper

logger = logging.getLogger("indirim_radari")

DEFAULT_MAX_AI_SCORES_PER_BRAND = 15
DEFAULT_DB_BATCH_SIZE = 150

INDEPENDENT_SHOE_SCRAPERS: dict[str, type[BaseScraper]] = {
    "instreet": InStreetScraper,
    "sneaksup": SneaksUpScraper,
    "barcin": BarcinScraper,
    "sportive": SportiveScraper,
    "adidasoutlet": AdidasOutletScraper,
    "pumaoutlet": PumaOutletScraper,
}

INDEPENDENT_CLOTHING_SCRAPERS: dict[str, type[BaseScraper]] = {
    "hm": HMScraper,
    "defacto": DeFactoScraper,
    "mavi": MaviScraper,
    "jackjones": JackJonesScraper,
}

INDEPENDENT_SCRAPERS: dict[str, type[BaseScraper]] = {
    **INDEPENDENT_SHOE_SCRAPERS,
    **INDEPENDENT_CLOTHING_SCRAPERS,
}

BRAND_GROUPS: dict[str, tuple[str, ...]] = {
    "inditex": tuple(INDITEX_BRAND_CONFIGS.keys()),
    "eren": tuple(EREN_BRAND_CONFIGS.keys()),
    "mango": tuple(MANGO_BRAND_CONFIGS.keys()),
    "trendyol": tuple(TRENDYOL_BRAND_CONFIGS.keys()),
    "shoes": tuple(EREN_BRAND_CONFIGS.keys()) + tuple(INDEPENDENT_SHOE_SCRAPERS.keys()),
    "clothing": (
        tuple(MANGO_BRAND_CONFIGS.keys())
        + tuple(TRENDYOL_BRAND_CONFIGS.keys())
        + tuple(INDEPENDENT_CLOTHING_SCRAPERS.keys())
    ),
    "independents": tuple(INDEPENDENT_SCRAPERS.keys()),
    "independent_shoes": tuple(INDEPENDENT_SHOE_SCRAPERS.keys()),
    "independent_clothing": tuple(INDEPENDENT_CLOTHING_SCRAPERS.keys()),
}

SUPPORTED_BRANDS: tuple[str, ...] = (
    tuple(INDITEX_BRAND_CONFIGS.keys())
    + tuple(EREN_BRAND_CONFIGS.keys())
    + tuple(MANGO_BRAND_CONFIGS.keys())
    + tuple(TRENDYOL_BRAND_CONFIGS.keys())
    + tuple(INDEPENDENT_CLOTHING_SCRAPERS.keys())
    + tuple(INDEPENDENT_SHOE_SCRAPERS.keys())
)

# Brands that exclusively serve a single gender storefront; only skipped when an
# explicit single-gender `--section` (`men` or `women`) is passed via CLI.
BRAND_GENDER_SCOPE: dict[str, frozenset[str]] = {
    "trendyolmilla": frozenset({"women"}),
    "trendyolman": frozenset({"men"}),
}


def _parse_gender_cli_arg(raw_gender: str) -> list[str]:
    """Parse `--gender` CLI override (e.g. `men`, `men,unisex`, `all`) into canonical list."""
    normalized = raw_gender.strip().lower()
    if normalized == "all":
        return list(VALID_TARGET_GENDERS)
    tokens: list[str] = []
    seen: set[str] = set()
    invalid: list[str] = []
    for part in normalized.split(","):
        tok = part.strip()
        if not tok:
            continue
        if tok == "all":
            return list(VALID_TARGET_GENDERS)
        if tok in VALID_TARGET_GENDERS:
            if tok not in seen:
                seen.add(tok)
                tokens.append(tok)
        else:
            invalid.append(tok)
    if invalid or not tokens:
        raise SystemExit(
            f"Geçersiz --gender değeri: '{raw_gender}'. "
            f"Desteklenen değerler: {', '.join(VALID_TARGET_GENDERS)}, all (virgülle ayrılabilir)."
        )
    return tokens


def _filter_brands_by_target_genders(
    brands: list[str],
    target_genders: list[str],
) -> tuple[list[str], list[str]]:
    """Skip single-gender brands that have no overlap with the user's `target_genders`."""
    desired = {g.strip().lower() for g in target_genders if g.strip()}
    if not desired:
        return list(brands), []

    selected: list[str] = []
    skipped: list[str] = []
    for slug in brands:
        scope = BRAND_GENDER_SCOPE.get(slug)
        if scope is not None and not (scope & desired):
            skipped.append(slug)
        else:
            selected.append(slug)
    return selected, skipped


def get_brand_default_run_mode(brand: str) -> ScrapeRunMode:
    """Return the configured default execution mode (`cloud` or `local`) for a brand slug."""
    slug = brand.strip().lower()
    if slug in INDITEX_BRAND_CONFIGS:
        return InditexScraper.default_run_mode
    if slug in EREN_BRAND_CONFIGS:
        return EREN_BRAND_CONFIGS[slug].default_run_mode
    if slug in MANGO_BRAND_CONFIGS:
        return MANGO_BRAND_CONFIGS[slug].default_run_mode
    if slug in TRENDYOL_BRAND_CONFIGS:
        return TRENDYOL_BRAND_CONFIGS[slug].default_run_mode
    if slug in INDEPENDENT_SCRAPERS:
        return INDEPENDENT_SCRAPERS[slug].default_run_mode
    raise ValueError(f"Unsupported brand '{brand}'")


def _filter_brands_by_target_mode(
    brands: list[str],
    target_mode: Literal["all", "cloud", "local"],
) -> tuple[list[str], list[str]]:
    """Split resolved brands into `(selected, skipped)` according to `--target-mode`."""
    if target_mode == "all":
        return list(brands), []

    desired = ScrapeRunMode(target_mode)
    selected: list[str] = []
    skipped: list[str] = []
    for slug in brands:
        if get_brand_default_run_mode(slug) == desired:
            selected.append(slug)
        else:
            skipped.append(slug)
    return selected, skipped


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _resolve_brands(brand_arg: str) -> list[str]:
    normalized = brand_arg.strip().lower()
    if not normalized or normalized == "all":
        return list(SUPPORTED_BRANDS)

    resolved: list[str] = []
    seen: set[str] = set()
    unknown: list[str] = []

    for token in [b.strip().lower() for b in normalized.split(",") if b.strip()]:
        if token == "all":
            targets = SUPPORTED_BRANDS
        elif token in BRAND_GROUPS:
            targets = BRAND_GROUPS[token]
        elif token in SUPPORTED_BRANDS:
            targets = (token,)
        else:
            unknown.append(token)
            continue

        for brand_slug in targets:
            if brand_slug not in seen:
                seen.add(brand_slug)
                resolved.append(brand_slug)

    if unknown:
        supported = ", ".join(sorted(SUPPORTED_BRANDS))
        groups = ", ".join(sorted([*BRAND_GROUPS.keys(), "all"]))
        raise SystemExit(
            f"Desteklenmeyen marka(lar): {', '.join(unknown)}. "
            f"Desteklenen markalar: {supported} | Gruplar: {groups}"
        )
    return resolved


def _build_scraper(
    brand: str,
    *,
    section: Literal["all", "men", "women"],
    max_categories: int,
    include_regular: bool,
    only_discounted: bool,
    app_settings: AppSettings | None,
    brand_timeout_seconds: float | None = DEFAULT_BRAND_TIME_BUDGET_SECONDS,
) -> BaseScraper:
    raw_timeout = app_settings.request_timeout_seconds if app_settings else 15
    timeout = min(raw_timeout, 15)
    min_jitter = app_settings.min_jitter_seconds if app_settings else 0.2
    max_jitter = app_settings.max_jitter_seconds if app_settings else 0.5

    if brand in INDITEX_BRAND_CONFIGS:
        return InditexScraper(
            brand=brand,
            section=section,
            only_discounted=only_discounted,
            include_regular_categories=include_regular,
            max_categories=max_categories,
            timeout_seconds=timeout,
            min_jitter_seconds=min(min_jitter, 0.25),
            max_jitter_seconds=min(max_jitter, 0.6),
            max_duration_seconds=brand_timeout_seconds,
        )

    if brand in EREN_BRAND_CONFIGS:
        return ErenScraper(
            brand=brand,
            section=section,
            only_discounted=only_discounted,
            max_categories=max_categories,
            timeout_seconds=timeout,
            min_jitter_seconds=min(min_jitter, 0.15),
            max_jitter_seconds=min(max_jitter, 0.45),
            max_duration_seconds=brand_timeout_seconds,
        )

    if brand in MANGO_BRAND_CONFIGS:
        return MangoScraper(
            brand=brand,
            section=section,
            only_discounted=only_discounted,
            max_categories=max_categories,
            timeout_seconds=timeout,
            min_jitter_seconds=min(min_jitter, 0.25),
            max_jitter_seconds=min(max_jitter, 0.6),
            max_duration_seconds=brand_timeout_seconds,
        )

    if brand in TRENDYOL_BRAND_CONFIGS:
        return TrendyolScraper(
            brand=brand,
            section=section,
            only_discounted=only_discounted,
            max_categories=max_categories,
            timeout_seconds=timeout,
            min_jitter_seconds=min(min_jitter, 0.2),
            max_jitter_seconds=min(max_jitter, 0.5),
            max_duration_seconds=brand_timeout_seconds,
        )

    if brand in INDEPENDENT_SCRAPERS:
        scraper_cls = INDEPENDENT_SCRAPERS[brand]
        return scraper_cls(
            section=section,
            only_discounted=only_discounted,
            max_categories=max_categories,
            timeout_seconds=timeout,
            min_jitter_seconds=min(min_jitter, 0.15),
            max_jitter_seconds=min(max_jitter, 0.45),
            max_duration_seconds=brand_timeout_seconds,
        )

    raise ValueError(f"Unsupported brand '{brand}'")


def _print_brand_report(
    brand: str,
    *,
    status: ScrapeStatus,
    fetched_count: int,
    filter_summary: FilterBatchSummary,
    strict_matched_count: int = 0,
    matched_entries: list[tuple[ProductItem, FilterResult, PriceAnalysis, int | None, str | None]],
    saved_count: int,
    ai_scored_count: int,
    notified_count: int,
    duration_ms: int,
    dry_run: bool,
    error_message: str | None = None,
) -> None:
    mode_tag = "DRY-RUN" if dry_run else "DB-SYNC"
    print(f"\n{'=' * 72}")
    print(
        f"[{mode_tag}] Marka: {brand.upper()} | Durum: {status.value.upper()} | "
        f"Süre: {duration_ms} ms"
    )
    if error_message:
        print(f"  Hata/Not: {error_message}")
    print(
        f"  Çekilen indirimli ürün: {fetched_count} | Kataloğa uygun: {filter_summary.passed_count} | "
        f"Bildirim kriterine uyan (sıkı): {strict_matched_count} | Elenen: {filter_summary.rejected_count} | "
        f"Kaydedilen: {saved_count} | AI puanlanan: {ai_scored_count} | Bildirilen: {notified_count}"
    )
    if filter_summary.rejection_breakdown:
        breakdown_str = ", ".join(
            f"{k}={v}" for k, v in sorted(filter_summary.rejection_breakdown.items(), key=lambda kv: -kv[1])
        )
        print(f"  Eleme dağılımı: {breakdown_str}")

    if matched_entries:
        print("  --- Kataloğa Alınan Ürünler (ilk 15) ---")
        for product, f_res, p_res, ai_score, ai_comment in matched_entries[:15]:
            orig = f"{product.original_price} TL" if product.original_price else "-"
            cart_tag = f" | Sepette: {product.cart_price} TL" if product.cart_price is not None else ""
            promo_tag = f" [{product.promo_badge}]" if product.promo_badge else ""
            stock_tag = " [KRİTİK STOK]" if product.is_critical_stock else ""
            real_tag = "GERÇEK İNDİRİM" if p_res.is_real_discount else f"ŞÜPHELİ ({p_res.reason})"
            days_tag = (
                f" | Son {p_res.lowest_price_days} günün en düşüğü"
                if p_res.lowest_price_days is not None and p_res.lowest_price_days > 0
                else ""
            )
            fabric = product.fabric_info or "kumaş bilgisi yok"
            if product.available_sizes:
                matched_str = ", ".join(f_res.matched_sizes) if f_res.matched_sizes else "-"
                sizes_line = f"Eşleşen bedenler: {matched_str} (Tüm stok: {', '.join(product.available_sizes)}){stock_tag}"
            else:
                sizes_line = f"Beden: Beden sitede kontrol{stock_tag}"
            ai_line = ""
            if ai_score is not None:
                comment_suffix = f" — {ai_comment}" if ai_comment else ""
                ai_line = f"\n    AI Tarz: {ai_score}/10{comment_suffix}"
            print(
                f"  • [{product.category.value} | {product.gender.value}] {product.title}{promo_tag}\n"
                f"    Fiyat: {product.current_price} TL{cart_tag} (Eski: {orig}, %{product.discount_percent}) | "
                f"{real_tag}{days_tag}\n"
                f"    {sizes_line}\n"
                f"    Kumaş: {fabric} | Link: {product.url}{ai_line}"
            )
    print("=" * 72)


def run_pipeline(args: argparse.Namespace) -> int:
    if args.test_push:
        try:
            app_settings = get_settings()
            db = DatabaseClient(app_settings)
            notifier = PushNotifier(db, app_settings)
            if not notifier.is_configured:
                logger.error("VAPID_PRIVATE_KEY yapılandırılmamış; test bildirimi gönderilemiyor.")
                return 1
            report = notifier.send_test_notification()
            print(
                f"[TEST-PUSH] Denenen: {report.attempted} | Gönderilen: {report.sent} | "
                f"Pasifleştirilen: {report.deactivated} | Hatalı: {report.failed}"
            )
            for err in report.errors:
                print(f"  - {err}")
            return 0 if report.failed == 0 else 1
        except Exception as exc:  # noqa: BLE001
            logger.error("Test bildirimi başarısız: %s", exc)
            return 1

    brands = _resolve_brands(args.brand)

    effective_target_mode: Literal["all", "cloud", "local"] = getattr(args, "target_mode", "all")
    if getattr(args, "only_cloud", False):
        effective_target_mode = "cloud"
    elif getattr(args, "only_local", False):
        effective_target_mode = "local"

    brands, skipped_brands = _filter_brands_by_target_mode(brands, effective_target_mode)
    if skipped_brands:
        logger.info(
            "Filtered brands by target_mode=%s: running %d, skipped %d (%s).",
            effective_target_mode,
            len(brands),
            len(skipped_brands),
            ", ".join(skipped_brands),
        )
    if not brands:
        logger.warning(
            "No brands matched --brand='%s' with target_mode='%s'; exiting cleanly.",
            args.brand,
            effective_target_mode,
        )
        return 0

    app_settings: AppSettings | None = None
    db: DatabaseClient | None = None
    user_settings: UserSettings

    if args.no_db:
        user_settings = UserSettings()
        logger.info("Running with --no-db: using built-in default UserSettings.")
    else:
        try:
            app_settings = get_settings()
            db = DatabaseClient(app_settings)
            user_settings = db.get_user_settings()
            logger.info(
                "Loaded UserSettings from Supabase (genders=%s, tops=%s, bottoms=%s, shoes=%s, lookback=%dd).",
                user_settings.target_genders,
                user_settings.top_sizes,
                user_settings.bottom_sizes,
                user_settings.shoe_sizes,
                user_settings.price_history_lookback_days,
            )
        except Exception as exc:  # noqa: BLE001
            if args.dry_run:
                logger.warning(
                    "Could not load Supabase settings in --dry-run (%s); falling back to defaults.",
                    exc,
                )
                user_settings = UserSettings()
                db = None
            else:
                logger.error("Supabase connection/settings failed: %s", exc)
                return 1

    # Apply optional `--gender` CLI override for notification/strict-filter testing
    gender_override = getattr(args, "gender", None)
    if gender_override:
        override_genders = _parse_gender_cli_arg(gender_override)
        user_settings = user_settings.model_copy(update={"target_genders": override_genders})
        logger.info(
            "Overriding notification target_genders via --gender CLI flag: %s",
            user_settings.target_genders,
        )

    # Always scrape all genders (`all`) by default so the catalog has full Men, Women, and
    # Unisex coverage. `user_settings.target_genders` is strictly scoped to Web Push / AI
    # notification filtering and never restricts scraping or DB catalog ingestion.
    raw_section = getattr(args, "section", "all")
    effective_section: Literal["all", "men", "women"] = (
        "all" if not raw_section or raw_section == "auto" else raw_section
    )

    if effective_section in {"men", "women"}:
        brands, skipped_gender_brands = _filter_brands_by_target_genders(
            brands, [effective_section]
        )
        if skipped_gender_brands:
            logger.info(
                "Skipped %d brand(s) incompatible with explicit --section=%s: %s",
                len(skipped_gender_brands),
                effective_section,
                ", ".join(skipped_gender_brands),
            )
        if not brands:
            logger.warning(
                "No brands left after filtering for explicit --section=%s; exiting cleanly.",
                effective_section,
            )
            return 0

    logger.info(
        "Scrape section='%s' (catalog covers all scraped genders) | Notification target_genders=%s",
        effective_section,
        user_settings.target_genders,
    )

    run_mode = ScrapeRunMode(
        args.run_mode or (app_settings.scraper_run_mode if app_settings else "local")
    )
    product_filter = ProductFilter(user_settings)
    price_analyzer = PriceAnalyzer(
        db=db,
        lookback_days=user_settings.price_history_lookback_days,
    )
    notifier: PushNotifier | None = None
    if not args.dry_run and not args.no_notify and db is not None and app_settings is not None:
        notifier = PushNotifier(db, app_settings)
        if not notifier.is_configured:
            logger.info("VAPID keys are not configured; Web Push notifications skipped.")
            notifier = None

    ai_scorer: AIStyleScorer | None = None
    if args.skip_ai:
        logger.info("Running with --skip-ai; AI style scoring disabled.")
    elif app_settings is not None:
        candidate_scorer = AIStyleScorer(app_settings)
        if candidate_scorer.is_configured:
            ai_scorer = candidate_scorer
            logger.info("Gemini AI style scorer initialized (model=%s).", ai_scorer.model)
        else:
            logger.info("GEMINI_API_KEY is not configured; AI style scoring skipped.")
    else:
        logger.info("Running without AppSettings; AI style scoring skipped.")

    had_failure = False
    total_ai_scored = 0
    succeeded_brands: list[str] = []
    brand_timeout_seconds: float | None = getattr(
        args, "brand_timeout", DEFAULT_BRAND_TIME_BUDGET_SECONDS
    )
    max_ai_per_brand: int = max(
        0,
        int(getattr(args, "max_ai_scores_per_brand", DEFAULT_MAX_AI_SCORES_PER_BRAND)),
    )

    for brand in brands:
        started = time.perf_counter()
        log_id: int | None = None

        try:
            scraper = _build_scraper(
                brand,
                section=effective_section,
                max_categories=args.max_categories,
                include_regular=args.include_regular,
                only_discounted=not args.include_full_price,
                app_settings=app_settings,
                brand_timeout_seconds=brand_timeout_seconds,
            )
        except Exception as exc:  # noqa: BLE001
            had_failure = True
            duration_ms = int((time.perf_counter() - started) * 1000)
            err_msg = f"Scraper init failed: {type(exc).__name__}: {exc}"
            logger.exception("[%s] %s", brand, err_msg)
            if not args.dry_run and db is not None:
                try:
                    fail_log_id = db.start_scrape_log(brand=brand, engine=brand, run_mode=run_mode)
                    db.finish_scrape_log(
                        fail_log_id,
                        status=ScrapeStatus.FAILED,
                        duration_ms=duration_ms,
                        error_message=err_msg,
                    )
                except Exception as log_exc:  # noqa: BLE001
                    logger.warning("[%s] Could not record init failure in scrape_log: %s", brand, log_exc)
            continue

        if not args.dry_run and db is not None:
            try:
                log_id = db.start_scrape_log(brand=brand, engine=scraper.engine_name, run_mode=run_mode)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] Could not start scrape_log: %s", brand, exc)

        with scraper:
            scrape_result = scraper.run()

        if scrape_result.status in {ScrapeStatus.FAILED, ScrapeStatus.BLOCKED}:
            had_failure = True
            duration_ms = int((time.perf_counter() - started) * 1000)
            if not args.dry_run and db is not None and log_id is not None:
                try:
                    db.finish_scrape_log(
                        log_id,
                        status=scrape_result.status,
                        fetched_count=scrape_result.fetched_count,
                        duration_ms=duration_ms,
                        error_message=scrape_result.error_message,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] Could not finish scrape_log: %s", brand, exc)

            _print_brand_report(
                brand,
                status=scrape_result.status,
                fetched_count=0,
                filter_summary=FilterBatchSummary(),
                matched_entries=[],
                saved_count=0,
                ai_scored_count=0,
                notified_count=0,
                duration_ms=duration_ms,
                dry_run=args.dry_run,
                error_message=scrape_result.error_message,
            )
            continue

        passed_items, filter_summary = product_filter.filter_for_catalog(scrape_result.products)
        matched_entries: list[
            tuple[ProductItem, FilterResult, PriceAnalysis, int | None, str | None]
        ] = []
        strict_matched_count = 0
        saved_count = 0
        ai_scored_count = 0
        notified_count = 0
        final_status = scrape_result.status
        pipeline_error: str | None = scrape_result.error_message

        # ------------------------------------------------------------------
        # Stage 1: Batch-fetch existing products & price history from Supabase
        # ------------------------------------------------------------------
        existing_by_ext: dict[str, dict[str, object] | None] = {}
        history_by_pid: dict[str, list[dict[str, object]]] | None = None
        if db is not None and passed_items:
            try:
                if hasattr(db, "get_products_by_external_ids"):
                    existing_by_ext = db.get_products_by_external_ids(
                        brand,
                        [p.external_id for p, _ in passed_items],
                        chunk_size=DEFAULT_DB_BATCH_SIZE,
                    )
                else:
                    for product, _ in passed_items:
                        existing_by_ext[product.external_id] = db.get_product_by_external_id(
                            product.brand,
                            product.external_id,
                        )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] Batch existing-product lookup failed: %s", brand, exc)
                existing_by_ext = {}

            existing_ids = [
                str(row["id"])
                for row in existing_by_ext.values()
                if isinstance(row, dict) and row.get("id")
            ]
            if existing_ids and hasattr(db, "get_price_history_bulk"):
                try:
                    history_by_pid = db.get_price_history_bulk(
                        existing_ids,
                        days=user_settings.price_history_lookback_days,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] Bulk price_history lookup failed: %s", brand, exc)
                    history_by_pid = None

        # ------------------------------------------------------------------
        # Stage 2: In-memory strict evaluation, price analysis & AI candidate selection
        # ------------------------------------------------------------------
        prepared_records: list[dict[str, object]] = []
        ai_candidate_indices: list[int] = []

        for product, catalog_res in passed_items:
            try:
                strict_res = product_filter.evaluate(product)
                if strict_res.passed:
                    strict_matched_count += 1

                existing_row = existing_by_ext.get(product.external_id)
                existing_id = (
                    str(existing_row["id"])
                    if isinstance(existing_row, dict) and existing_row.get("id")
                    else None
                )

                if db is None:
                    analysis = price_analyzer.analyze_from_history(product, [])
                elif history_by_pid is not None:
                    history_rows = history_by_pid.get(existing_id, []) if existing_id else []
                    analysis = price_analyzer.analyze_from_history(product, history_rows)
                else:
                    analysis, existing_id = price_analyzer.analyze_product(
                        product,
                        existing_product_id=existing_id,
                    )

                effective_ai_score: int | None = (
                    existing_row.get("ai_score") if isinstance(existing_row, dict) else None
                )
                effective_ai_comment: str | None = (
                    existing_row.get("ai_comment") if isinstance(existing_row, dict) else None
                )

                rec_idx = len(prepared_records)
                prepared_records.append(
                    {
                        "product": product,
                        "catalog_res": catalog_res,
                        "strict_res": strict_res,
                        "existing_row": existing_row,
                        "analysis": analysis,
                        "new_ai_score": None,
                        "new_ai_comment": None,
                        "effective_ai_score": effective_ai_score,
                        "effective_ai_comment": effective_ai_comment,
                    }
                )

                # Eligible for Gemini AI scoring ONLY if:
                # 1) passes strict user filter (sizes, fabric, budget, high discount)
                # 2) has no existing AI score in DB
                # 3) is newly discovered OR its effective price dropped below DB price
                # 4) passes `should_score_product` (real discount)
                if ai_scorer is not None and strict_res.passed:
                    is_fresh_or_dropped = is_new_or_price_dropped(
                        product,
                        existing_row if isinstance(existing_row, dict) else None,
                    )
                    if is_fresh_or_dropped:
                        should_score, score_reason = ai_scorer.should_score_product(
                            analysis,
                            existing_product=existing_row if isinstance(existing_row, dict) else None,
                        )
                        if should_score:
                            ai_candidate_indices.append(rec_idx)
                        else:
                            logger.debug(
                                "[%s] Skipping AI score for %s (%s)",
                                brand,
                                product.external_id,
                                score_reason,
                            )
                    else:
                        logger.debug(
                            "[%s] Skipping AI score for %s (existing_unchanged_price)",
                            brand,
                            product.external_id,
                        )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] Failed preparing product %s: %s", brand, product.external_id, exc)
                final_status = ScrapeStatus.PARTIAL
                pipeline_error = f"Partial processing error: {exc}"

        # ------------------------------------------------------------------
        # Stage 3: Score ONLY top-N (default 15/brand) highest-discount strict candidates
        # ------------------------------------------------------------------
        if ai_scorer is not None and ai_candidate_indices and max_ai_per_brand > 0:
            ai_candidate_indices.sort(
                key=lambda idx: (
                    -(prepared_records[idx]["product"].discount_percent or 0),  # type: ignore[union-attr]
                    prepared_records[idx]["product"].effective_price,  # type: ignore[union-attr]
                )
            )
            capped_indices = ai_candidate_indices[:max_ai_per_brand]
            if len(ai_candidate_indices) > len(capped_indices):
                logger.info(
                    "[%s] Capping Gemini AI scoring to top %d of %d eligible strict-match product(s).",
                    brand,
                    len(capped_indices),
                    len(ai_candidate_indices),
                )

            consecutive_rate_limits = 0
            for idx in capped_indices:
                if args.max_ai_scores is not None and total_ai_scored >= args.max_ai_scores:
                    logger.debug(
                        "[%s] Stopping AI scoring (global max_ai_scores=%d reached)",
                        brand,
                        args.max_ai_scores,
                    )
                    break

                rec = prepared_records[idx]
                product = rec["product"]  # type: ignore[assignment]
                score_res = ai_scorer.score_product(
                    product,  # type: ignore[arg-type]
                    style_profile=user_settings.style_profile,
                )
                if score_res.ai_score is not None:
                    rec["new_ai_score"] = score_res.ai_score
                    rec["new_ai_comment"] = score_res.ai_comment
                    rec["effective_ai_score"] = score_res.ai_score
                    rec["effective_ai_comment"] = score_res.ai_comment
                    ai_scored_count += 1
                    total_ai_scored += 1
                    consecutive_rate_limits = 0
                elif score_res.status == "rate_limited":
                    consecutive_rate_limits += 1
                    logger.warning(
                        "[%s] AI scoring rate-limited for %s (%d/2): %s",
                        brand,
                        product.external_id,  # type: ignore[union-attr]
                        consecutive_rate_limits,
                        score_res.error_message,
                    )
                    if consecutive_rate_limits >= 2:
                        logger.warning(
                            "[%s] Aborting remaining AI scores for brand due to consecutive 429 rate limits.",
                            brand,
                        )
                        break
                elif score_res.status == "error":
                    logger.warning(
                        "[%s] AI scoring fallback for %s (%s): %s",
                        brand,
                        product.external_id,  # type: ignore[union-attr]
                        score_res.status,
                        score_res.error_message,
                    )

        for rec in prepared_records:
            matched_entries.append(
                (
                    rec["product"],  # type: ignore[arg-type]
                    rec["catalog_res"],  # type: ignore[arg-type]
                    rec["analysis"],  # type: ignore[arg-type]
                    rec["effective_ai_score"],  # type: ignore[arg-type]
                    rec["effective_ai_comment"],  # type: ignore[arg-type]
                )
            )

        # ------------------------------------------------------------------
        # Stage 4: Batch upsert `products` and batch insert `price_history`
        # ------------------------------------------------------------------
        saved_by_ext: dict[str, dict[str, object]] = {}
        if not args.dry_run and db is not None and prepared_records:
            if hasattr(db, "upsert_products_batch"):
                try:
                    product_payloads = [
                        rec["product"].to_db_payload(  # type: ignore[union-attr]
                            is_real_discount=rec["analysis"].is_real_discount,  # type: ignore[union-attr]
                            lowest_price_days=rec["analysis"].lowest_price_days,  # type: ignore[union-attr]
                            historical_min_price=rec["analysis"].historical_min_price,  # type: ignore[union-attr]
                            ai_score=rec["new_ai_score"],  # type: ignore[arg-type]
                            ai_comment=rec["new_ai_comment"],  # type: ignore[arg-type]
                        )
                        for rec in prepared_records
                    ]
                    saved_rows = db.upsert_products_batch(
                        product_payloads,
                        chunk_size=DEFAULT_DB_BATCH_SIZE,
                    )
                    for row in saved_rows:
                        ext_id = str(row.get("external_id") or "").strip()
                        if ext_id:
                            saved_by_ext[ext_id] = row
                    saved_count = len(saved_rows)

                    history_records: list[dict[str, object]] = []
                    for rec in prepared_records:
                        analysis = rec["analysis"]  # type: ignore[assignment]
                        if not analysis.should_record_history:  # type: ignore[union-attr]
                            continue
                        product = rec["product"]  # type: ignore[assignment]
                        saved_row = saved_by_ext.get(product.external_id)  # type: ignore[union-attr]
                        if saved_row and saved_row.get("id"):
                            history_records.append(
                                {
                                    "product_id": str(saved_row["id"]),
                                    "price": float(product.effective_price),  # type: ignore[union-attr]
                                    "original_price": (
                                        float(product.original_price)  # type: ignore[union-attr]
                                        if product.original_price is not None  # type: ignore[union-attr]
                                        else None
                                    ),
                                    "discount_percent": product.discount_percent,  # type: ignore[union-attr]
                                }
                            )

                    if history_records:
                        if hasattr(db, "record_price_history_batch"):
                            db.record_price_history_batch(
                                history_records,
                                chunk_size=DEFAULT_DB_BATCH_SIZE,
                            )
                        else:
                            for h_rec in history_records:
                                db.record_price_history(
                                    product_id=str(h_rec["product_id"]),
                                    price=h_rec["price"],  # type: ignore[arg-type]
                                    original_price=h_rec["original_price"],  # type: ignore[arg-type]
                                    discount_percent=h_rec["discount_percent"],  # type: ignore[arg-type]
                                )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[%s] Batch DB write failed: %s", brand, exc)
                    final_status = ScrapeStatus.PARTIAL
                    pipeline_error = f"Batch save error: {exc}"
            else:
                for rec in prepared_records:
                    product = rec["product"]  # type: ignore[assignment]
                    analysis = rec["analysis"]  # type: ignore[assignment]
                    try:
                        saved_row = db.upsert_product(
                            product,  # type: ignore[arg-type]
                            is_real_discount=analysis.is_real_discount,  # type: ignore[union-attr]
                            lowest_price_days=analysis.lowest_price_days,  # type: ignore[union-attr]
                            historical_min_price=analysis.historical_min_price,  # type: ignore[union-attr]
                            ai_score=rec["new_ai_score"],  # type: ignore[arg-type]
                            ai_comment=rec["new_ai_comment"],  # type: ignore[arg-type]
                        )
                        saved_by_ext[product.external_id] = saved_row  # type: ignore[union-attr]
                        if analysis.should_record_history:  # type: ignore[union-attr]
                            db.record_price_history(
                                product_id=str(saved_row["id"]),
                                price=product.effective_price,  # type: ignore[union-attr]
                                original_price=product.original_price,  # type: ignore[union-attr]
                                discount_percent=product.discount_percent,  # type: ignore[union-attr]
                            )
                        saved_count += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "[%s] Failed saving product %s: %s",
                            brand,
                            product.external_id,  # type: ignore[union-attr]
                            exc,
                        )
                        final_status = ScrapeStatus.PARTIAL
                        pipeline_error = f"Partial save error: {exc}"

        # ------------------------------------------------------------------
        # Stage 5: Strictly gated Web Push notifications
        # ------------------------------------------------------------------
        if not args.dry_run and db is not None and notifier is not None:
            for rec in prepared_records:
                strict_res = rec["strict_res"]  # type: ignore[assignment]
                if not strict_res.passed:  # type: ignore[union-attr]
                    continue
                product = rec["product"]  # type: ignore[assignment]
                saved_row = saved_by_ext.get(product.external_id)  # type: ignore[union-attr]
                if not saved_row or not saved_row.get("id"):
                    continue
                try:
                    persisted_ai_score = (
                        saved_row.get("ai_score")
                        if saved_row.get("ai_score") is not None
                        else rec["effective_ai_score"]
                    )
                    should_send, notify_reason = notifier.should_notify_product(
                        product,  # type: ignore[arg-type]
                        analysis=rec["analysis"],  # type: ignore[arg-type]
                        user_settings=user_settings,
                        existing_product=rec["existing_row"],  # type: ignore[arg-type]
                        ai_score=persisted_ai_score,  # type: ignore[arg-type]
                    )
                    if should_send:
                        push_report = notifier.notify_product(
                            product,  # type: ignore[arg-type]
                            product_id=str(saved_row["id"]),
                            matched_sizes=strict_res.matched_sizes,  # type: ignore[union-attr]
                            lowest_price_days=rec["analysis"].lowest_price_days,  # type: ignore[union-attr]
                            ai_score=persisted_ai_score,  # type: ignore[arg-type]
                        )
                        if push_report.sent > 0:
                            notified_count += 1
                    else:
                        logger.debug(
                            "[%s] Skipping push for %s (%s)",
                            brand,
                            product.external_id,  # type: ignore[union-attr]
                            notify_reason,
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "[%s] Notification step failed for %s: %s",
                        brand,
                        product.external_id,  # type: ignore[union-attr]
                        exc,
                    )

        # Only include brand in 72h stale cleanup if scrape completed cleanly without budget cutoff
        if (
            final_status == ScrapeStatus.SUCCESS
            and scrape_result.fetched_count > 0
            and not getattr(scraper, "budget_exceeded", False)
        ):
            succeeded_brands.append(brand)

        duration_ms = int((time.perf_counter() - started) * 1000)

        if not args.dry_run and db is not None and log_id is not None:
            try:
                db.finish_scrape_log(
                    log_id,
                    status=final_status,
                    fetched_count=scrape_result.fetched_count,
                    matched_count=len(passed_items),
                    saved_count=saved_count,
                    notified_count=notified_count,
                    duration_ms=duration_ms,
                    error_message=pipeline_error,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("[%s] Could not finalize scrape_log: %s", brand, exc)

        _print_brand_report(
            brand,
            status=final_status,
            fetched_count=scrape_result.fetched_count,
            filter_summary=filter_summary,
            strict_matched_count=strict_matched_count,
            matched_entries=matched_entries,
            saved_count=saved_count,
            ai_scored_count=ai_scored_count,
            notified_count=notified_count,
            duration_ms=duration_ms,
            dry_run=args.dry_run,
            error_message=pipeline_error,
        )

    if not args.dry_run and db is not None and succeeded_brands and hasattr(db, "cleanup_stale_products"):
        try:
            deleted_stale = db.cleanup_stale_products(succeeded_brands, max_age_hours=72)
            logger.info(
                "Stale product cleanup (>72h) completed for %d succeeded brand(s) [%s]: deleted %d row(s).",
                len(succeeded_brands),
                ", ".join(succeeded_brands),
                deleted_stale,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Stale product cleanup failed for succeeded brand(s) [%s]: %s",
                ", ".join(succeeded_brands),
                exc,
            )

    return 1 if had_failure and len(brands) == 1 else 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="İndirim Radarı — Akıllı İndirim Tarayıcı CLI",
    )
    parser.add_argument(
        "--brand",
        default="pullandbear",
        help=(
            "Taranacak marka(lar) veya gruplar: inditex, eren, mango, trendyol, shoes, clothing, independents, all "
            "veya virgülle ayrılmış liste (toplam 22 marka)"
        ),
    )
    parser.add_argument(
        "--target-mode",
        choices=("all", "cloud", "local"),
        default="all",
        help=(
            "Markaların default_run_mode bayrağına göre filtrele: "
            "cloud (veri merkezi uyumlu), local (WAF korumalı yerel markalar), all (tümü; varsayılan: all)."
        ),
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--only-cloud",
        action="store_true",
        help="Yalnızca default_run_mode='cloud' olan markaları tara (--target-mode cloud kısayolu).",
    )
    mode_group.add_argument(
        "--only-local",
        action="store_true",
        help="Yalnızca default_run_mode='local' olan markaları tara (--target-mode local kısayolu).",
    )
    parser.add_argument(
        "--gender",
        default=None,
        help=(
            "Bildirim / sıkı filtre için geçici hedef cinsiyet(ler): men, women, unisex, all "
            "veya virgülle ayrılmış liste (ör. men,unisex). Veritabanı taramasını kısıtlamaz; "
            "belirtilmezse Supabase user_settings.target_genders okunur."
        ),
    )
    parser.add_argument(
        "--section",
        choices=("all", "auto", "men", "women"),
        default="all",
        help="Taranacak reyon: all (hem Erkek hem Kadın — varsayılan), auto (all ile eşdeğer), men veya women.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Canlı veriyi çeker, filtreler ve fiyat analizini gösterir ancak Supabase'e yazmaz.",
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="Supabase bağlantısı kurmadan varsayılan ayarlarla çalışır (yalnızca --dry-run ile önerilir).",
    )
    parser.add_argument(
        "--max-categories",
        type=int,
        default=12,
        help="Marka başına taranacak maksimum kategori sayısı (varsayılan: 12).",
    )
    parser.add_argument(
        "--include-regular",
        action="store_true",
        help="Promosyon kategorilerine ek olarak ana giyim/ayakkabı kategorilerini de tara.",
    )
    parser.add_argument(
        "--include-full-price",
        action="store_true",
        help="İndirimsiz ürünleri de ham çekime dahil et (hata ayıklama için).",
    )
    parser.add_argument(
        "--run-mode",
        choices=("cloud", "local"),
        default=None,
        help="scrape_logs tablosuna yazılacak çalışma modu.",
    )
    parser.add_argument(
        "--test-push",
        action="store_true",
        help="Supabase'deki aktif Web Push aboneliklerine Türkçe test bildirimi gönderip çıkar.",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Tarama sırasında Web Push bildirimi gönderimini devre dışı bırakır.",
    )
    parser.add_argument(
        "--skip-ai",
        action="store_true",
        help="Tarama sırasında Gemini AI tarz puanlamasını atlar.",
    )
    parser.add_argument(
        "--max-ai-scores",
        type=int,
        default=None,
        help="Çalıştırma başına maksimum Gemini AI puanlama sayısı (opsiyonel kota sınırı).",
    )
    parser.add_argument(
        "--max-ai-scores-per-brand",
        type=int,
        default=DEFAULT_MAX_AI_SCORES_PER_BRAND,
        help=f"Marka başına maksimum yeni Gemini AI puanlama sayısı (varsayılan: {DEFAULT_MAX_AI_SCORES_PER_BRAND}).",
    )
    parser.add_argument(
        "--brand-timeout",
        type=float,
        default=DEFAULT_BRAND_TIME_BUDGET_SECONDS,
        help=f"Marka başına yumuşak tarama süre bütçesi, saniye (varsayılan: {int(DEFAULT_BRAND_TIME_BUDGET_SECONDS)}).",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
        help="Log seviyesi (varsayılan: INFO).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.no_db and not args.dry_run:
        args.dry_run = True
    _configure_logging(args.log_level)
    return run_pipeline(args)


if __name__ == "__main__":
    raise SystemExit(main())
