"""Gemini-powered AI style scorer for filtered real-discount products."""

from __future__ import annotations

import json
import logging
import re
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Literal
from urllib.parse import urlparse

import certifi
from curl_cffi import requests as curl_requests
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

from .config import AppSettings, get_settings
from .notifier import format_brand_name, format_try_price
from .price_analyzer import PriceAnalysis
from ..engines.base import ProductCategory, ProductItem

logger = logging.getLogger(__name__)

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_STYLE_PROFILE = (
    "Minimalist, sade, kaliteli kumaşlı, büyük logosuz ve zamansız kesimler; "
    "rahat (regular/relaxed) kalıplar, nötr ve toprak tonları."
)

CATEGORY_LABELS_TR: dict[ProductCategory, str] = {
    ProductCategory.SHOES: "Ayakkabı",
    ProductCategory.TOPS: "Üst Giyim",
    ProductCategory.BOTTOMS: "Alt Giyim",
    ProductCategory.OUTERWEAR: "Dış Giyim",
    ProductCategory.OTHER: "Diğer",
}

ALLOWED_IMAGE_MIME_TYPES: dict[str, str] = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/png": "image/png",
    "image/webp": "image/webp",
    "image/gif": "image/gif",
}

EXTENSION_MIME_MAP: dict[str, str] = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

MAX_IMAGE_BYTES = 5 * 1024 * 1024

SYSTEM_INSTRUCTION = (
    "Sen kişisel bir moda ve stil danışmanısın. Sana verilen giyim veya ayakkabı "
    "ürününü (varsa ürün görselini de inceleyerek) kullanıcının tarz profiline göre "
    "1 ile 10 arasında puanlayacaksın.\n"
    "Puanlama ölçeği:\n"
    "- 1-3: Kullanıcının tarz profiline belirgin şekilde aykırı (ör. istenmeyen kalıp, "
    "büyük baskı/logo, uyumsuz renk veya zayıf kumaş).\n"
    "- 4-6: Kısmen uyumlu ama sıradan veya profille tam örtüşmeyen yönleri var.\n"
    "- 7-8: Tarz profiliyle güçlü uyum, günlük kombinlere kolayca yakışır.\n"
    "- 9-10: Kullanıcının tarz profiline tam isabet eden, zamansız ve çok kaliteli parça.\n"
    "Yanıtını SADECE geçerli JSON formatında ver. `ai_comment` alanı kesinlikle Türkçe, "
    "doğal, öz ve tek cümlelik (en fazla 180 karakter) bir gerekçe olmalıdır."
)


def _is_placeholder_key(value: str | None) -> bool:
    if not value:
        return True
    cleaned = value.strip()
    if not cleaned:
        return True
    lowered = cleaned.lower()
    return (
        lowered.startswith("your-")
        or lowered.startswith("replace-")
        or lowered in {"changeme", "placeholder", "none", "null"}
    )


def _get_safe_ca_bundle() -> str:
    """Return an ASCII-safe CA bundle path so libcurl works on non-ASCII Windows paths."""
    src = Path(certifi.where())
    try:
        str(src).encode("ascii")
        return str(src)
    except UnicodeEncodeError:
        dst = Path(tempfile.gettempdir()) / "indirim_radari_cacert.pem"
        if not dst.exists() or dst.stat().st_size != src.stat().st_size:
            shutil.copyfile(src, dst)
        return str(dst)


def _sanitize_comment(raw_comment: str) -> str:
    """Normalize whitespace and keep the Turkish rationale concise and single-line."""
    cleaned = re.sub(r"\s+", " ", raw_comment).strip().strip('"').strip("'")
    if len(cleaned) <= 240:
        return cleaned
    truncated = cleaned[:237].rsplit(" ", 1)[0].rstrip(".,;:")
    return f"{truncated}..."


def _is_rate_limit_error(exc: Exception) -> bool:
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if code == 429:
        return True
    lowered = str(exc).lower()
    return any(
        token in lowered
        for token in ("429", "resource_exhausted", "quota", "rate limit", "too many requests")
    )


class StyleScoreResponse(BaseModel):
    """Structured JSON response schema requested from Gemini."""

    ai_score: int = Field(..., ge=1, le=10, description="1-10 arası tamsayı tarz puanı")
    ai_comment: str = Field(..., min_length=3, max_length=280, description="Tek cümlelik Türkçe gerekçe")


class StyleScoreResult(BaseModel):
    """Outcome returned by `AIStyleScorer.score_product` with safe fallback metadata."""

    ai_score: int | None = Field(default=None, ge=1, le=10)
    ai_comment: str | None = Field(default=None)
    used_image: bool = Field(default=False)
    model: str = Field(default=DEFAULT_GEMINI_MODEL)
    status: Literal["scored", "skipped_unconfigured", "rate_limited", "error"] = Field(
        default="scored"
    )
    error_message: str | None = Field(default=None)


def fetch_product_image(
    image_url: str,
    *,
    timeout_seconds: int = 10,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> tuple[bytes, str] | None:
    """Download a product image safely for multimodal Gemini scoring.

    Returns `(image_bytes, mime_type)` or `None` if unavailable or unsupported.
    """
    cleaned_url = (image_url or "").strip()
    if not cleaned_url.startswith(("http://", "https://")):
        return None

    try:
        response = curl_requests.get(
            cleaned_url,
            impersonate="chrome124",
            verify=_get_safe_ca_bundle(),
            timeout=max(3, timeout_seconds),
            headers={"Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"},
        )
        if response.status_code != 200:
            logger.debug("Image fetch returned HTTP %s for %s", response.status_code, cleaned_url)
            return None

        raw_bytes = response.content or b""
        if not raw_bytes or len(raw_bytes) > max_bytes:
            logger.debug(
                "Image size out of bounds (%d bytes) for %s", len(raw_bytes), cleaned_url
            )
            return None

        raw_ct = (response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
        mime_type = ALLOWED_IMAGE_MIME_TYPES.get(raw_ct)
        if mime_type is None and raw_ct in {"", "application/octet-stream", "binary/octet-stream"}:
            ext = Path(urlparse(cleaned_url).path).suffix.lower()
            mime_type = EXTENSION_MIME_MAP.get(ext)

        if mime_type is None:
            logger.debug("Unsupported image content-type '%s' for %s", raw_ct, cleaned_url)
            return None

        return raw_bytes, mime_type
    except Exception as exc:  # noqa: BLE001 — image download is best-effort
        logger.debug("Could not fetch product image %s: %s", cleaned_url, exc)
        return None


class AIStyleScorer:
    """Scores filtered products against the user's style profile using Google Gemini."""

    def __init__(
        self,
        settings: AppSettings | None = None,
        *,
        model: str | None = None,
        client: Any | None = None,
        image_fetcher: Callable[[str], tuple[bytes, str] | None] | None = None,
        use_images: bool = True,
        max_retries: int = 1,
        retry_backoff_seconds: float = 1.5,
        min_request_interval_seconds: float = 0.4,
    ) -> None:
        self._settings = settings
        if self._settings is None and client is None:
            try:
                self._settings = get_settings()
            except Exception:  # noqa: BLE001
                self._settings = None

        configured_model = (
            self._settings.gemini_model
            if self._settings is not None and self._settings.gemini_model
            else DEFAULT_GEMINI_MODEL
        )
        self.model = (model or configured_model).strip() or DEFAULT_GEMINI_MODEL
        self._use_images = use_images
        self._max_retries = max(0, max_retries)
        self._retry_backoff_seconds = max(0.0, retry_backoff_seconds)
        self._min_request_interval_seconds = max(0.0, min_request_interval_seconds)
        self._last_request_ts: float = 0.0
        self._image_fetcher = image_fetcher or (
            lambda url: fetch_product_image(
                url,
                timeout_seconds=min(
                    10,
                    self._settings.request_timeout_seconds if self._settings else 10,
                ),
            )
        )

        self._client: Any | None = client
        if self._client is None and self._settings is not None:
            raw_key = (
                self._settings.gemini_api_key.get_secret_value()
                if self._settings.gemini_api_key is not None
                else None
            )
            if not _is_placeholder_key(raw_key):
                assert raw_key is not None
                self._client = genai.Client(api_key=raw_key.strip())

    @property
    def is_configured(self) -> bool:
        """Return True when a Gemini client is ready to score products."""
        return self._client is not None

    @staticmethod
    def should_score_product(
        analysis: PriceAnalysis,
        *,
        existing_product: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Score only real-discount products that do not already have an AI score."""
        if not analysis.is_real_discount:
            return False, "not_real_discount"

        if existing_product is not None and existing_product.get("ai_score") is not None:
            return False, "already_scored"

        return True, "eligible"

    @staticmethod
    def build_prompt(product: ProductItem, style_profile: str) -> str:
        """Build a concise Turkish evaluation prompt from product attributes and user style."""
        profile_text = (style_profile or "").strip() or DEFAULT_STYLE_PROFILE
        brand_label = format_brand_name(product.brand)
        category_label = CATEGORY_LABELS_TR.get(product.category, product.category.value)

        price_line = format_try_price(product.current_price)
        if product.original_price is not None and product.original_price > product.current_price:
            price_line = (
                f"{price_line} (Liste: {format_try_price(product.original_price)}, "
                f"%{product.discount_percent or 0} indirim)"
            )

        fabric_parts: list[str] = []
        if product.fabric_info:
            fabric_parts.append(product.fabric_info.strip())
        if product.polyester_percent is not None:
            fabric_parts.append(f"Polyester oranı: %{product.polyester_percent}")
        fabric_line = " | ".join(fabric_parts) if fabric_parts else "Belirtilmemiş"

        return (
            "Aşağıdaki ürünü kullanıcının tarz profiline göre 1-10 arasında puanla ve "
            "tek cümlelik Türkçe gerekçe yaz.\n\n"
            f"Kullanıcı Tarz Profili: {profile_text}\n\n"
            "Ürün Bilgileri:\n"
            f"- Marka: {brand_label}\n"
            f"- Başlık: {product.title}\n"
            f"- Kategori: {category_label} ({product.category.value})\n"
            f"- Fiyat: {price_line}\n"
            f"- Kumaş / Materyal: {fabric_line}\n"
        )

    @staticmethod
    def _parse_response(response: Any) -> StyleScoreResponse:
        """Extract and validate `StyleScoreResponse` from a Gemini SDK response."""
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, StyleScoreResponse):
            return StyleScoreResponse(
                ai_score=max(1, min(10, int(parsed.ai_score))),
                ai_comment=_sanitize_comment(parsed.ai_comment),
            )
        if isinstance(parsed, dict):
            return StyleScoreResponse(
                ai_score=max(1, min(10, int(parsed["ai_score"]))),
                ai_comment=_sanitize_comment(str(parsed["ai_comment"])),
            )

        raw_text = (getattr(response, "text", None) or "").strip()
        if not raw_text:
            raise ValueError("Gemini yanıtı boş döndü.")

        # Strip optional markdown JSON code fences
        cleaned_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.IGNORECASE).strip()
        data = json.loads(cleaned_text)
        if not isinstance(data, dict):
            raise ValueError("Gemini JSON yanıtı beklenen nesne biçiminde değil.")

        raw_score = data.get("ai_score")
        raw_comment = data.get("ai_comment")
        if raw_score is None or not raw_comment:
            raise ValueError("Gemini JSON yanıtında ai_score veya ai_comment eksik.")

        return StyleScoreResponse(
            ai_score=max(1, min(10, int(round(float(raw_score))))),
            ai_comment=_sanitize_comment(str(raw_comment)),
        )

    def _pace_request(self) -> None:
        if self._min_request_interval_seconds <= 0 or self._last_request_ts <= 0:
            return
        elapsed = time.monotonic() - self._last_request_ts
        remaining = self._min_request_interval_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    def score_product(
        self,
        product: ProductItem,
        *,
        style_profile: str,
    ) -> StyleScoreResult:
        """Score a product with Gemini; returns a safe fallback result on error or quota limit."""
        if not self.is_configured:
            return StyleScoreResult(
                ai_score=None,
                ai_comment=None,
                used_image=False,
                model=self.model,
                status="skipped_unconfigured",
                error_message="GEMINI_API_KEY yapılandırılmamış.",
            )

        prompt_text = self.build_prompt(product, style_profile)
        contents: list[Any] = []
        used_image = False

        if self._use_images and product.image_url:
            try:
                fetched = self._image_fetcher(product.image_url)
            except Exception as exc:  # noqa: BLE001
                logger.debug("Image fetcher failed for %s: %s", product.external_id, exc)
                fetched = None
            if fetched is not None:
                img_bytes, mime_type = fetched
                contents.append(types.Part.from_bytes(data=img_bytes, mime_type=mime_type))
                used_image = True

        contents.append(prompt_text)

        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            temperature=0.2,
            response_mime_type="application/json",
            response_schema=StyleScoreResponse,
        )

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                self._pace_request()
                self._last_request_ts = time.monotonic()
                response = self._client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config,
                )
                parsed = self._parse_response(response)
                return StyleScoreResult(
                    ai_score=parsed.ai_score,
                    ai_comment=parsed.ai_comment,
                    used_image=used_image,
                    model=self.model,
                    status="scored",
                )
            except Exception as exc:  # noqa: BLE001 — never abort scraping due to AI errors
                last_exc = exc
                is_rate_limit = _is_rate_limit_error(exc)
                if attempt < self._max_retries:
                    delay = self._retry_backoff_seconds * (attempt + 1)
                    logger.warning(
                        "Gemini scoring attempt %d failed for %s (%s); retrying in %.1fs.",
                        attempt + 1,
                        product.external_id,
                        "rate_limited" if is_rate_limit else type(exc).__name__,
                        delay,
                    )
                    if delay > 0:
                        time.sleep(delay)
                    continue

                status: Literal["rate_limited", "error"] = (
                    "rate_limited" if is_rate_limit else "error"
                )
                logger.warning(
                    "Gemini scoring failed for %s (%s): %s",
                    product.external_id,
                    status,
                    exc,
                )
                return StyleScoreResult(
                    ai_score=None,
                    ai_comment=None,
                    used_image=used_image,
                    model=self.model,
                    status=status,
                    error_message=f"{type(exc).__name__}: {exc}",
                )

        return StyleScoreResult(
            ai_score=None,
            ai_comment=None,
            used_image=used_image,
            model=self.model,
            status="error",
            error_message=str(last_exc) if last_exc else "Unknown Gemini scoring error",
        )
