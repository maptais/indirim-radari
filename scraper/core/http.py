"""Shared HTTP session, WAF detection, price parsing, and category inference helpers."""

from __future__ import annotations

import logging
import random
import re
import shutil
import tempfile
import time
from collections.abc import Mapping
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

import certifi
from curl_cffi import requests as curl_requests

from .filters import turkish_lower
from ..engines.base import ProductCategory, ProductGender, ScraperBlockedError

logger = logging.getLogger(__name__)

IMPERSONATE_PROFILES: tuple[str, ...] = (
    "chrome131",
    "chrome124",
    "chrome120",
    "safari17_0",
)

WAF_BLOCK_MARKERS: tuple[str, ...] = (
    "bm-verify",
    "/_sec/verify",
    "ak_bmsc",
    "interstitial/ic.html",
    "reference #18.",
    "sec-if-cpt-container",
    "sec-bc-tile-container",
    "access denied",
    "just a moment...",
    "cf-browser-verification",
    "cf_chl_opt",
    "attention required! | cloudflare",
    "challenges.cloudflare.com",
    "checking your browser before accessing",
    "verify you are human",
    "px-captcha",
    "_pxhd",
    "datadome",
    "incapsula incident",
    "request unsuccessful. incapsula",
)

_SHOES_KEYWORDS: tuple[str, ...] = (
    "shoe",
    "footwear",
    "sneaker",
    "boot",
    "loafer",
    "sandal",
    "slipper",
    "trainer",
    "moccasin",
    "espadrille",
    "cleat",
    "slide",
    "clog",
    "calzado",
    "zapato",
    "bota",
    "deportivo",
    "ayakkabı",
    "ayakkabi",
    "bot",
    "çizme",
    "cizme",
    "terlik",
    "sandalet",
    "makosen",
    "krampon",
    "halı saha",
    "hali saha",
    "koşu ayakkab",
    "kosu ayakkab",
    "basketbol ayakkab",
    "spor ayakkab",
    "yürüyüş ayakkab",
    "yuruyus ayakkab",
    "outdoor ayakkab",
    "tenis ayakkab",
    "günlük ayakkab",
    "gunluk ayakkab",
)

_OUTERWEAR_KEYWORDS: tuple[str, ...] = (
    "coat",
    "jacket",
    "blazer",
    "trench",
    "parka",
    "puffer",
    "bomber",
    "anorak",
    "gilet",
    "vest",
    "overcoat",
    "biker",
    "windbreaker",
    "huntress",
    "abrigo",
    "cazadora",
    "chaqueta",
    "gabardina",
    "mont",
    "kaban",
    "ceket",
    "trençkot",
    "trenckot",
    "yağmurluk",
    "yagmurluk",
    "rüzgarlık",
    "ruzgarlik",
    "palto",
    "yelek",
)

_BOTTOMS_KEYWORDS: tuple[str, ...] = (
    "trouser",
    "pant",
    "jean",
    "denim",
    "short",
    "jogger",
    "chino",
    "cargo",
    "skirt",
    "bermuda",
    "legging",
    "tight",
    "pantalon",
    "vaquero",
    "falda",
    "pantolon",
    "şort",
    "sort",
    "etek",
    "eşofman altı",
    "esofman alti",
    "eşofman alt",
    "esofman alt",
    "tayt",
)

_TOPS_KEYWORDS: tuple[str, ...] = (
    "shirt",
    "t-shirt",
    "tshirt",
    "tee",
    "sweater",
    "knit",
    "jumper",
    "pullover",
    "hoodie",
    "sweatshirt",
    "fleece",
    "cardigan",
    "polo",
    "top",
    "blouse",
    "jersey",
    "camisa",
    "camiseta",
    "sudadera",
    "punto",
    "tişört",
    "tisort",
    "gömlek",
    "gomlek",
    "kazak",
    "triko",
    "hırka",
    "hirka",
    "bluz",
    "atlet",
    "süveter",
    "suveter",
    "polar",
    "forma",
    "eşofman üst",
    "esofman ust",
)

_ACCESSORY_EXACT_KEYWORDS: tuple[str, ...] = (
    "çanta",
    "canta",
    "sırt çantası",
    "sirt cantasi",
    "bel çantası",
    "bel cantasi",
    "valiz",
    "çorap",
    "corap",
    "şapka",
    "sapka",
    "bere",
    "atkı",
    "atki",
    "eldiven",
    "kemer",
    "cüzdan",
    "cuzdan",
    "matara",
    "suluk",
    "backpack",
    "duffel",
    "socks",
    "beanie",
    "cap",
    "hat",
    "gloves",
    "wallet",
    "belt",
    "bottle",
)


def get_safe_ca_bundle() -> str:
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


def is_blocked_body(text: str) -> bool:
    """Detect common anti-bot / WAF HTML challenge payloads."""
    if not text:
        return False
    head = text[:4000].lower()
    return any(marker in head for marker in WAF_BLOCK_MARKERS)


def is_blocked_response(
    status_code: int,
    text: str = "",
    headers: Mapping[str, str] | None = None,
) -> bool:
    """Return True if an HTTP response indicates a WAF / bot challenge or rate limit."""
    if headers:
        lowered_headers = {k.lower(): str(v).lower() for k, v in headers.items()}
        if lowered_headers.get("cf-mitigated") == "challenge":
            return True
        if "x-datadome" in lowered_headers and status_code in (401, 403, 429):
            return True
    if status_code in (403, 429):
        return True
    if status_code == 503 and is_blocked_body(text):
        return True
    return is_blocked_body(text)


def parse_try_price(raw_value: Any) -> Decimal | None:
    """Parse TRY prices from numbers or Turkish/EN formatted strings into a 2-decimal Decimal."""
    if raw_value is None or isinstance(raw_value, bool):
        return None
    if isinstance(raw_value, Decimal):
        val = raw_value
    elif isinstance(raw_value, (int, float)):
        val = Decimal(str(raw_value))
    else:
        text = str(raw_value).strip()
        if not text:
            return None
        # Strip currency symbols, spaces, and text
        cleaned = re.sub(r"[^\d.,\-]", "", text)
        if not cleaned or cleaned in {".", ",", "-"}:
            return None

        # Normalize thousand/decimal separators:
        # - Both ',' and '.' present: whichever appears last is decimal separator
        # - Only ',' present: decimal if 1-2 trailing digits, else thousand separator
        # - Only '.' present: thousand separator if exactly 3 trailing digits (e.g. "1.299" or "12.499")
        if "," in cleaned and "." in cleaned:
            if cleaned.rfind(",") > cleaned.rfind("."):
                cleaned = cleaned.replace(".", "").replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
        elif "," in cleaned:
            parts = cleaned.split(",")
            if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
                cleaned = f"{parts[0]}.{parts[1]}"
            else:
                cleaned = cleaned.replace(",", "")
        elif "." in cleaned:
            parts = cleaned.split(".")
            if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3 and parts[0].isdigit()):
                cleaned = cleaned.replace(".", "")

        try:
            val = Decimal(cleaned)
        except Exception:  # noqa: BLE001
            return None

    if val <= Decimal("0"):
        return None
    return val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def infer_product_category(
    *hints: str,
    default: ProductCategory = ProductCategory.OTHER,
) -> ProductCategory:
    """Map product title, category breadcrumb, or metadata hints to a canonical ProductCategory."""
    combined = turkish_lower(" ".join(h for h in hints if h))
    if not combined:
        return default

    # Remove sleeve-length phrases that falsely contain "short" (e.g. "short sleeve t-shirt")
    cleaned = re.sub(r"\bshort\s+sleeve\b", "", combined)

    def _has_any(keywords: tuple[str, ...]) -> bool:
        for kw in keywords:
            if " " in kw or "-" in kw:
                if kw in cleaned:
                    return True
            elif re.search(rf"(?<![a-zçğıöşü]){re.escape(kw)}(?![a-zçğıöşü])|(?<![a-zçğıöşü]){re.escape(kw)}", cleaned):
                return True
        return False

    # Avoid misclassifying shoe bags or socks as shoes when explicitly accessories
    title_hint = turkish_lower(hints[0]) if hints and hints[0] else ""
    if title_hint and any(
        re.search(rf"(?<![a-zçğıöşü]){re.escape(kw)}", title_hint)
        for kw in _ACCESSORY_EXACT_KEYWORDS
    ):
        if not any(kw in title_hint for kw in ("ayakkabı", "ayakkabi", "sneaker", "bot", "terlik", "sandalet", "krampon")):
            return ProductCategory.OTHER

    if _has_any(_SHOES_KEYWORDS):
        return ProductCategory.SHOES
    if _has_any(_OUTERWEAR_KEYWORDS):
        return ProductCategory.OUTERWEAR
    if _has_any(
        (
            "tişört",
            "tisort",
            "t-shirt",
            "tshirt",
            "polo",
            "gömlek",
            "gomlek",
            "kazak",
            "triko",
            "hırka",
            "hirka",
            "bluz",
            "sweatshirt",
            "hoodie",
            "polar",
        )
    ):
        return ProductCategory.TOPS
    if _has_any(_BOTTOMS_KEYWORDS):
        return ProductCategory.BOTTOMS
    if _has_any(_TOPS_KEYWORDS):
        return ProductCategory.TOPS
    return default


_KIDS_HINT_RE = re.compile(
    r"(?<![a-zçğıöşü])(?:"
    r"çocuk|cocuk|bebek|baby|kids?|junior|niño|nino|niña|nina|infant|toddler|"
    r"kız\s+çocuk|kiz\s+cocuk|erkek\s+çocuk|erkek\s+cocuk|genç\s+çocuk|genc\s+cocuk|"
    r"boy|boys|girl|girls|preschool|big\s+kids|little\s+kids|youth|teen"
    r")(?![a-zçğıöşü])|\((?:ps|td|gs|jr)\)|(?:\s|/|-)(?:ps|td|gs|jr)(?:\s|/|-|$)",
    re.IGNORECASE,
)

_UNISEX_HINT_RE = re.compile(r"(?<![a-zçğıöşü])unisex(?![a-zçğıöşü])", re.IGNORECASE)

_WOMEN_HINT_RE = re.compile(
    r"(?<![a-zçğıöşü])(?:"
    r"kadın|kadin|woman|women|womens|ladies|lady|mujer|señora|senora|bayan|jjxx|"
    r"elbise|etek|bluz|büstiyer|bustiyer|sütyen|sutyen|bralet|topuklu|babet|hamile|"
    r"crop\s*top|tulum|tesettür|tesettur|korseli|tayt|bikini|mayokini|pareo|saten\s+elbise"
    r")(?![a-zçğıöşü])",
    re.IGNORECASE,
)

_MEN_HINT_RE = re.compile(
    r"(?<![a-zçğıöşü])(?:"
    r"erkek|men|mens|man|hombre|caballero|bay"
    r")(?![a-zçğıöşü])",
    re.IGNORECASE,
)


def is_kids_product(*hints: str) -> bool:
    """Return True if any hint indicates a kids/baby/junior product."""
    combined = turkish_lower(" ".join(h for h in hints if h))
    if not combined:
        return False
    return bool(_KIDS_HINT_RE.search(combined))


def infer_product_gender(
    *hints: str,
    default: ProductGender = ProductGender.UNISEX,
) -> ProductGender:
    """Infer canonical ProductGender (`men`, `women`, `unisex`) from ordered hints.

    Hints should be ordered from most specific (e.g., explicit gender field, product title)
    to broadest (e.g., URL slug, category path).
    """
    saw_both = False
    for raw_hint in hints:
        if not raw_hint:
            continue
        cleaned = turkish_lower(str(raw_hint))
        if not cleaned:
            continue
        # Normalize path/slug separators so tokens like `men_shirts` or `/erkek-ayakkabi/` match boundaries
        tokenized = re.sub(r"[/_\-\.,:|>]+", " ", cleaned)

        if _UNISEX_HINT_RE.search(tokenized):
            return ProductGender.UNISEX

        has_women = bool(_WOMEN_HINT_RE.search(tokenized))
        has_men = bool(_MEN_HINT_RE.search(tokenized))

        if has_women and has_men:
            saw_both = True
            continue
        if has_women:
            return ProductGender.WOMEN
        if has_men:
            return ProductGender.MEN

    if saw_both:
        return ProductGender.UNISEX
    return default


class HttpClient:
    """Resilient `curl_cffi` HTTP client with TLS impersonation rotation and WAF detection."""

    def __init__(
        self,
        brand: str,
        *,
        timeout_seconds: int = 20,
        max_retries: int = 2,
        impersonate: str | None = None,
        default_headers: Mapping[str, str] | None = None,
    ) -> None:
        self.brand = brand
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        self._impersonate = impersonate or random.choice(IMPERSONATE_PROFILES)
        self._ca_bundle = get_safe_ca_bundle()
        self._default_headers = dict(default_headers or {})
        self._session: curl_requests.Session | None = None

    @property
    def impersonate(self) -> str:
        return self._impersonate

    def get_session(self) -> curl_requests.Session:
        if self._session is None:
            self._session = curl_requests.Session(
                impersonate=self._impersonate,
                verify=self._ca_bundle,
            )
        return self._session

    def rotate_session(self) -> curl_requests.Session:
        self.close()
        alternatives = [p for p in IMPERSONATE_PROFILES if p != self._impersonate]
        self._impersonate = random.choice(alternatives or list(IMPERSONATE_PROFILES))
        return self.get_session()

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:  # noqa: BLE001
                pass
            self._session = None

    def _merge_headers(self, headers: Mapping[str, str] | None) -> dict[str, str]:
        merged = {
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        merged.update(self._default_headers)
        if headers:
            merged.update(headers)
        return merged

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
        json_body: Any = None,
        data: Any = None,
        allow_redirects: bool = True,
    ) -> Any:
        """Execute an HTTP request with retry, impersonate rotation, and WAF detection."""
        last_error: Exception | None = None
        merged_headers = self._merge_headers(headers)

        for attempt in range(self.max_retries + 1):
            session = self.get_session() if attempt == 0 else self.rotate_session()
            try:
                resp = session.request(
                    method.upper(),
                    url,
                    headers=merged_headers,
                    params=params,
                    json=json_body,
                    data=data,
                    timeout=self.timeout_seconds,
                    allow_redirects=allow_redirects,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "[%s] Request error on attempt %d/%d (%s): %s",
                    self.brand,
                    attempt + 1,
                    self.max_retries + 1,
                    url,
                    exc,
                )
                if attempt < self.max_retries:
                    time.sleep(random.uniform(1.5, 3.0) * (attempt + 1))
                continue

            resp_text = resp.text or ""
            if is_blocked_response(resp.status_code, resp_text, resp.headers):
                last_error = ScraperBlockedError(
                    f"HTTP {resp.status_code} anti-bot/WAF block on {url} (profile={self._impersonate})"
                )
                logger.warning(
                    "[%s] Anti-bot/rate-limit HTTP %d on attempt %d/%d (profile=%s)",
                    self.brand,
                    resp.status_code,
                    attempt + 1,
                    self.max_retries + 1,
                    self._impersonate,
                )
                if attempt < self.max_retries:
                    time.sleep(random.uniform(2.0, 4.0) * (attempt + 1))
                    continue
                raise last_error

            if resp.status_code >= 500:
                last_error = RuntimeError(f"HTTP {resp.status_code} server error on {url}")
                if attempt < self.max_retries:
                    time.sleep(random.uniform(1.5, 3.0) * (attempt + 1))
                    continue
                raise last_error

            if resp.status_code != 200:
                raise RuntimeError(f"Unexpected HTTP {resp.status_code} on {url}")

            return resp

        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Failed to fetch {url}")

    def get_text(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> str:
        req_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        if headers:
            req_headers.update(headers)
        resp = self.request("GET", url, headers=req_headers, params=params)
        return resp.text or ""

    def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        req_headers = {
            "Accept": "application/json, text/plain, */*",
        }
        if headers:
            req_headers.update(headers)
        resp = self.request("GET", url, headers=req_headers, params=params)
        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            if is_blocked_body(resp.text or ""):
                raise ScraperBlockedError(
                    f"Received anti-bot HTML instead of JSON on {url}"
                ) from exc
            raise RuntimeError(f"Invalid JSON response from {url}: {exc}") from exc

    def post_json(
        self,
        url: str,
        *,
        json_body: Any = None,
        data: Any = None,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        req_headers = {
            "Accept": "application/json, text/plain, */*",
        }
        if headers:
            req_headers.update(headers)
        resp = self.request(
            "POST",
            url,
            headers=req_headers,
            params=params,
            json_body=json_body,
            data=data,
        )
        try:
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            if is_blocked_body(resp.text or ""):
                raise ScraperBlockedError(
                    f"Received anti-bot HTML instead of JSON on {url}"
                ) from exc
            raise RuntimeError(f"Invalid JSON response from {url}: {exc}") from exc
