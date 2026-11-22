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

from scraper.core.ai_scorer import AIStyleScorer
from scraper.core.config import AppSettings, UserSettings, get_settings
from scraper.core.db import DatabaseClient
from scraper.core.filters import FilterBatchSummary, FilterResult, ProductFilter
from scraper.core.notifier import PushNotifier
from scraper.core.price_analyzer import PriceAnalysis, PriceAnalyzer
from scraper.engines.base import BaseScraper, ProductItem, ScrapeRunMode, ScrapeStatus
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
}

SUPPORTED_BRANDS: tuple[str, ...] = (
    tuple(INDITEX_BRAND_CONFIGS.keys())
    + tuple(EREN_BRAND_CONFIGS.keys())
    + tuple(MANGO_BRAND_CONFIGS.keys())
    + tuple(TRENDYOL_BRAND_CONFIGS.keys())
    + tuple(INDEPENDENT_CLOTHING_SCRAPERS.keys())
    + tuple(INDEPENDENT_SHOE_SCRAPERS.keys())
)


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
) -> BaseScraper:
    timeout = app_settings.request_timeout_seconds if app_settings else 20
    min_jitter = app_settings.min_jitter_seconds if app_settings else 1.0
    max_jitter = app_settings.max_jitter_seconds if app_settings else 3.0

    if brand in INDITEX_BRAND_CONFIGS:
        return InditexScraper(
            brand=brand,
            section=section,
            only_discounted=only_discounted,
            include_regular_categories=include_regular,
            max_categories=max_categories,
            timeout_seconds=timeout,
            min_jitter_seconds=min_jitter,
            max_jitter_seconds=max_jitter,
        )

    if brand in EREN_BRAND_CONFIGS:
        return ErenScraper(
            brand=brand,
            section=section,
            only_discounted=only_discounted,
            max_categories=max_categories,
            timeout_seconds=timeout,
            min_jitter_seconds=min(min_jitter, 0.4),
            max_jitter_seconds=min(max_jitter, 0.9),
        )

    if brand in MANGO_BRAND_CONFIGS:
        return MangoScraper(
            brand=brand,
            section=section,
            only_discounted=only_discounted,
            max_categories=max_categories,
            timeout_seconds=timeout,
            min_jitter_seconds=min(min_jitter, 0.5),
            max_jitter_seconds=min(max_jitter, 1.1),
        )

    if brand in TRENDYOL_BRAND_CONFIGS:
        return TrendyolScraper(
            brand=brand,
            section=section,
            only_discounted=only_discounted,
            max_categories=max_categories,
            timeout_seconds=timeout,
            min_jitter_seconds=min(min_jitter, 0.4),
            max_jitter_seconds=min(max_jitter, 0.9),
        )

    if brand in INDEPENDENT_SCRAPERS:
        scraper_cls = INDEPENDENT_SCRAPERS[brand]
        return scraper_cls(
            section=section,
            only_discounted=only_discounted,
            max_categories=max_categories,
            timeout_seconds=timeout,
            min_jitter_seconds=min(min_jitter, 0.4),
            max_jitter_seconds=min(max_jitter, 0.9),
        )

    raise ValueError(f"Unsupported brand '{brand}'")


def _print_brand_report(
    brand: str,
    *,
    status: ScrapeStatus,
    fetched_count: int,
    filter_summary: FilterBatchSummary,
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
        f"  Çekilen indirimli ürün: {fetched_count} | Filtreden geçen: {filter_summary.passed_count} | "
        f"Elenen: {filter_summary.rejected_count} | Kaydedilen: {saved_count} | "
        f"AI puanlanan: {ai_scored_count} | Bildirilen: {notified_count}"
    )
    if filter_summary.rejection_breakdown:
        breakdown_str = ", ".join(
            f"{k}={v}" for k, v in sorted(filter_summary.rejection_breakdown.items(), key=lambda kv: -kv[1])
        )
        print(f"  Eleme dağılımı: {breakdown_str}")

    if matched_entries:
        print("  --- Eşleşen Ürünler (ilk 15) ---")
        for product, f_res, p_res, ai_score, ai_comment in matched_entries[:15]:
            orig = f"{product.original_price} TL" if product.original_price else "-"
            stock_tag = " [KRİTİK STOK]" if product.is_critical_stock else ""
            real_tag = "GERÇEK İNDİRİM" if p_res.is_real_discount else f"ŞÜPHELİ ({p_res.reason})"
            days_tag = (
                f" | Son {p_res.lowest_price_days} günün en düşüğü"
                if p_res.lowest_price_days is not None and p_res.lowest_price_days > 0
                else ""
            )
            fabric = product.fabric_info or "kumaş bilgisi yok"
            ai_line = ""
            if ai_score is not None:
                comment_suffix = f" — {ai_comment}" if ai_comment else ""
                ai_line = f"\n    AI Tarz: {ai_score}/10{comment_suffix}"
            print(
                f"  • [{product.category.value}] {product.title}\n"
                f"    Fiyat: {product.current_price} TL (Eski: {orig}, %{product.discount_percent}) | "
                f"{real_tag}{days_tag}\n"
                f"    Eşleşen bedenler: {', '.join(f_res.matched_sizes)} "
                f"(Tüm stok: {', '.join(product.available_sizes)}){stock_tag}\n"
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
                "Loaded UserSettings from Supabase (tops=%s, bottoms=%s, shoes=%s, lookback=%dd).",
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

    for brand in brands:
        started = time.perf_counter()
        log_id: int | None = None

        scraper = _build_scraper(
            brand,
            section=args.section,
            max_categories=args.max_categories,
            include_regular=args.include_regular,
            only_discounted=not args.include_full_price,
            app_settings=app_settings,
        )

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

        passed_items, filter_summary = product_filter.filter_products(scrape_result.products)
        matched_entries: list[
            tuple[ProductItem, FilterResult, PriceAnalysis, int | None, str | None]
        ] = []
        saved_count = 0
        ai_scored_count = 0
        notified_count = 0
        final_status = scrape_result.status
        pipeline_error: str | None = scrape_result.error_message

        for product, filter_res in passed_items:
            try:
                existing_row = (
                    db.get_product_by_external_id(product.brand, product.external_id)
                    if db is not None
                    else None
                )
                existing_id = str(existing_row["id"]) if existing_row else None
                analysis, existing_id = price_analyzer.analyze_product(
                    product,
                    existing_product_id=existing_id,
                )

                new_ai_score: int | None = None
                new_ai_comment: str | None = None
                effective_ai_score: int | None = (
                    existing_row.get("ai_score") if existing_row else None
                )
                effective_ai_comment: str | None = (
                    existing_row.get("ai_comment") if existing_row else None
                )

                if ai_scorer is not None:
                    should_score, score_reason = AIStyleScorer.should_score_product(
                        analysis,
                        existing_product=existing_row,
                    )
                    if should_score:
                        if (
                            args.max_ai_scores is not None
                            and total_ai_scored >= args.max_ai_scores
                        ):
                            logger.debug(
                                "[%s] Skipping AI score for %s (max_ai_scores=%d reached)",
                                brand,
                                product.external_id,
                                args.max_ai_scores,
                            )
                        else:
                            score_res = ai_scorer.score_product(
                                product,
                                style_profile=user_settings.style_profile,
                            )
                            if score_res.ai_score is not None:
                                new_ai_score = score_res.ai_score
                                new_ai_comment = score_res.ai_comment
                                effective_ai_score = new_ai_score
                                effective_ai_comment = new_ai_comment
                                ai_scored_count += 1
                                total_ai_scored += 1
                            elif score_res.status in {"rate_limited", "error"}:
                                logger.warning(
                                    "[%s] AI scoring fallback for %s (%s): %s",
                                    brand,
                                    product.external_id,
                                    score_res.status,
                                    score_res.error_message,
                                )
                    else:
                        logger.debug(
                            "[%s] Skipping AI score for %s (%s)",
                            brand,
                            product.external_id,
                            score_reason,
                        )

                matched_entries.append(
                    (product, filter_res, analysis, effective_ai_score, effective_ai_comment)
                )

                if not args.dry_run and db is not None:
                    saved_row = db.upsert_product(
                        product,
                        is_real_discount=analysis.is_real_discount,
                        lowest_price_days=analysis.lowest_price_days,
                        historical_min_price=analysis.historical_min_price,
                        ai_score=new_ai_score,
                        ai_comment=new_ai_comment,
                    )
                    if analysis.should_record_history:
                        db.record_price_history(
                            product_id=str(saved_row["id"]),
                            price=product.current_price,
                            original_price=product.original_price,
                            discount_percent=product.discount_percent,
                        )
                    saved_count += 1

                    persisted_ai_score = (
                        saved_row.get("ai_score")
                        if saved_row.get("ai_score") is not None
                        else effective_ai_score
                    )

                    if notifier is not None:
                        should_send, notify_reason = notifier.should_notify_product(
                            product,
                            analysis=analysis,
                            user_settings=user_settings,
                            existing_product=existing_row,
                            ai_score=persisted_ai_score,
                        )
                        if should_send:
                            push_report = notifier.notify_product(
                                product,
                                product_id=str(saved_row["id"]),
                                matched_sizes=filter_res.matched_sizes,
                                lowest_price_days=analysis.lowest_price_days,
                                ai_score=persisted_ai_score,
                            )
                            if push_report.sent > 0:
                                notified_count += 1
                        else:
                            logger.debug(
                                "[%s] Skipping push for %s (%s)",
                                brand,
                                product.external_id,
                                notify_reason,
                            )
            except Exception as exc:  # noqa: BLE001 — isolate single product save failure
                logger.warning("[%s] Failed processing product %s: %s", brand, product.external_id, exc)
                final_status = ScrapeStatus.PARTIAL
                pipeline_error = f"Partial save error: {exc}"

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
            matched_entries=matched_entries,
            saved_count=saved_count,
            ai_scored_count=ai_scored_count,
            notified_count=notified_count,
            duration_ms=duration_ms,
            dry_run=args.dry_run,
            error_message=pipeline_error,
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
        "--section",
        choices=("all", "men", "women"),
        default="all",
        help="Taranacak reyon: all, men veya women (varsayılan: all)",
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
