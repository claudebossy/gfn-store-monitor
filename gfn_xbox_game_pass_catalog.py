import base64
import json
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urljoin

import requests

from gfn_monitor import (
    fetch_all_games,
    get_game_title,
    get_store_names,
    normalize_name,
)

OUTPUT_FILE = (
    Path("site")
    / "data"
    / "gfn-xbox-pc-game-pass-catalog.json"
)
HLTB_CACHE_FILE = (
    Path("site")
    / "data"
    / "howlongtobeat-cache.json"
)
OPENCRITIC_CACHE_FILE = (
    Path("site")
    / "data"
    / "opencritic-cache.json"
)

XBOX_MARKET = os.getenv(
    "XBOX_MARKET",
    os.getenv("GFN_COUNTRY", "CH"),
).strip().upper()
XBOX_LANGUAGE = os.getenv(
    "XBOX_LANGUAGE",
    os.getenv("GFN_LANGUAGE", "en_US"),
).strip().replace("_", "-").lower()
XBOX_PLATFORM_CONTEXT = "pc"
XBOX_SUBSCRIPTION_CONTEXT = "cfq7ttc0kgq8"
XBOX_PC_SIGL_ID = "609d944c-d395-4c0a-9ea4-e9f39b52c1ad"
XBOX_SIGL_URL = (
    "https://catalog.gamepass.com/sigls/v3"
    "?id={sigl_id}"
    "&language={language}"
    "&market={market}"
    "&platformContext={platform_context}"
    "&subscriptionContext={subscription_context}"
)
XBOX_PRODUCTS_URL = (
    "https://displaycatalog.mp.microsoft.com/v7.0/products"
    "?bigIds={big_ids}"
    "&market={market}"
    "&languages={language}"
    "&MS-CV=DGU1mcuYo0WMMp+F.1"
)

REQUEST_TIMEOUT = 60
PRODUCT_BATCH_SIZE = 25
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0 Safari/537.36"
)
HLTB_USER_AGENT = "GitHubCopilotRuntime-WebFetch"
IMAGE_PREFERENCE = (
    "BoxArt",
    "Poster",
    "FeaturePromotionalSquareArt",
    "TitledHeroArt",
    "SuperHeroArt",
)
TITLE_SUFFIX_PATTERNS = (
    r"[:\-]\s*(?:anniversary|championship|collectors?|complete|"
    r"definitive|deluxe|digital deluxe|enhanced|game of the year|"
    r"goty|launch|premium|remastered|standard|ultimate|windows)\s+edition\b.*$",
    r"[:\-]\s*(?:pc|windows)\b.*$",
    r"[:\-]\s*director'?s cut\b.*$",
    r"\s+(?:anniversary|definitive|deluxe|enhanced|premium|"
    r"remastered|ultimate)\s+edition\b.*$",
    r"\s+(?:pc|windows)\b.*$",
)
HLTB_BASE_URL = "https://howlongtobeat.com/"
HLTB_GAME_URL = HLTB_BASE_URL + "game/"
HLTB_IMAGE_URL = HLTB_BASE_URL + "games/"
HLTB_SEARCH_PATH_FALLBACK = "/api/search/site"
HLTB_SEARCH_PAGE_SIZE = 8
HLTB_MATCH_MIN_SCORE = 85
HLTB_MATCH_EXACT_SCORE = 120
HLTB_MATCH_PARTIAL_SCORE = 92
HLTB_CACHE_MAX_AGE = timedelta(days=30)
HLTB_NOT_FOUND_CACHE_MAX_AGE = timedelta(days=7)
OPENCRITIC_BASE_URL = "https://opencritic.com/"
OPENCRITIC_SEARCH_PATH = "/api/game/search?criteria={criteria}"
OPENCRITIC_GAME_PATH = "/api/game/{game_id}"
OPENCRITIC_MATCH_MIN_SCORE = 85
OPENCRITIC_MATCH_EXACT_SCORE = 120
OPENCRITIC_MATCH_PARTIAL_SCORE = 92
SHORT_GAME_TARGET_HOURS = 15.0
SHORT_GAME_MAX_HOURS = 40.0
SHORT_GAME_CRITIC_WEIGHT = 0.7
SHORT_GAME_DURATION_WEIGHT = 0.3
SCRIPT_SRC_PATTERN = re.compile(
    r"<script[^>]+src=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
HLTB_ENDPOINT_PATTERN = re.compile(
    r"fetch\s*\(\s*[\"']/api/([a-zA-Z0-9_/]+)[^\"']*[\"']"
    r"\s*,\s*{[^}]*method:\s*[\"']POST[\"']",
    re.IGNORECASE | re.DOTALL,
)
OPENCRITIC_CLIENT_CONFIG_PATTERN = re.compile(
    r"client\s*:\s*\{[^}]*baseUrl\s*:\s*\"([^\"]+)\""
    r"[^}]*apiKey\s*:\s*\"([^\"]+)\"",
    re.IGNORECASE,
)

session = requests.Session()
session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
)


@dataclass(frozen=True)
class MatchResult:
    gfn_key: str
    match_type: str
    score: int


@dataclass(frozen=True)
class HltbMatchResult:
    data: dict
    score: int
    match_type: str


@dataclass(frozen=True)
class OpenCriticMatchResult:
    data: dict
    score: int
    match_type: str


def request_json(url: str) -> object:
    response = session.get(url, timeout=REQUEST_TIMEOUT)

    if response.status_code >= 400:
        raise RuntimeError(
            f"Request failed with {response.status_code} for {url}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Response was not valid JSON for {url}"
        ) from exc


def save_output(data: dict) -> None:
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = OUTPUT_FILE.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary_file.replace(OUTPUT_FILE)


def save_hltb_cache(entries: dict[str, dict]) -> None:
    HLTB_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = HLTB_CACHE_FILE.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).replace(
                    microsecond=0
                ).isoformat(),
                "entries": entries,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary_file.replace(HLTB_CACHE_FILE)


def save_opencritic_cache(entries: dict[str, dict]) -> None:
    OPENCRITIC_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary_file = OPENCRITIC_CACHE_FILE.with_suffix(".tmp")
    temporary_file.write_text(
        json.dumps(
            {
                "updated_at": datetime.now(timezone.utc).replace(
                    microsecond=0
                ).isoformat(),
                "entries": entries,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary_file.replace(OPENCRITIC_CACHE_FILE)


def batched(values: list[str], size: int) -> list[list[str]]:
    return [
        values[index:index + size]
        for index in range(0, len(values), size)
    ]


def as_https_url(value: str | None) -> str | None:
    if not value:
        return None

    if value.startswith("//"):
        return "https:" + value

    return value


def to_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None

    if isinstance(value, int):
        return value

    if isinstance(value, float) and value.is_integer():
        return int(value)

    return None


def to_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    return None


def choose_product_image(product: dict) -> str | None:
    localized = first_dict(product.get("LocalizedProperties"))
    images = localized.get("Images", [])

    if not isinstance(images, list):
        return None

    image_by_purpose = {}

    for image in images:
        if not isinstance(image, dict):
            continue

        purpose = image.get("ImagePurpose")
        uri = as_https_url(image.get("Uri"))

        if isinstance(purpose, str) and uri and purpose not in image_by_purpose:
            image_by_purpose[purpose] = uri

    for purpose in IMAGE_PREFERENCE:
        if purpose in image_by_purpose:
            return image_by_purpose[purpose]

    return None


def first_dict(value: object) -> dict:
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                return item

    return {}


def product_text(product: dict, key: str) -> str | None:
    localized = first_dict(product.get("LocalizedProperties"))
    value = localized.get(key)

    if isinstance(value, str):
        value = value.strip()
        return value or None

    return None


def load_hltb_cache() -> dict[str, dict]:
    if not HLTB_CACHE_FILE.exists():
        return {}

    try:
        raw_cache = json.loads(
            HLTB_CACHE_FILE.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{HLTB_CACHE_FILE} contains invalid JSON: {exc}"
        ) from exc

    if isinstance(raw_cache, dict):
        entries = raw_cache.get("entries", raw_cache)

        if isinstance(entries, dict):
            return {
                key: value
                for key, value in entries.items()
                if isinstance(key, str) and isinstance(value, dict)
            }

    raise RuntimeError(
        f"{HLTB_CACHE_FILE} must contain a JSON object"
    )


def load_opencritic_cache() -> dict[str, dict]:
    if not OPENCRITIC_CACHE_FILE.exists():
        return {}

    try:
        raw_cache = json.loads(
            OPENCRITIC_CACHE_FILE.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{OPENCRITIC_CACHE_FILE} contains invalid JSON: {exc}"
        ) from exc

    if isinstance(raw_cache, dict):
        entries = raw_cache.get("entries", raw_cache)

        if isinstance(entries, dict):
            return {
                key: value
                for key, value in entries.items()
                if isinstance(key, str) and isinstance(value, dict)
            }

    raise RuntimeError(
        f"{OPENCRITIC_CACHE_FILE} must contain a JSON object"
    )


def get_search_titles(product: dict) -> list[str]:
    localized = first_dict(product.get("LocalizedProperties"))
    raw_titles = localized.get("SearchTitles", [])

    if not isinstance(raw_titles, list):
        return []

    titles = []

    for item in raw_titles:
        if not isinstance(item, dict):
            continue

        title = item.get("SearchTitleString")

        if isinstance(title, str):
            title = title.strip()

            if title:
                titles.append(title)

    return list(dict.fromkeys(titles))


def normalize_spacing(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def title_variants(title: str) -> list[str]:
    variants = [normalize_spacing(title)]

    without_brackets = normalize_spacing(
        re.sub(r"\s*[\(\[].*?[\)\]]\s*", " ", title)
    )
    if without_brackets:
        variants.append(without_brackets)

    for pattern in TITLE_SUFFIX_PATTERNS:
        stripped = normalize_spacing(
            re.sub(
                pattern,
                "",
                title,
                flags=re.IGNORECASE,
            )
        )
        if stripped:
            variants.append(stripped)

    return list(dict.fromkeys(variants))


def parse_iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        return None


def sanitize_hltb_data(data: object) -> dict | None:
    if not isinstance(data, dict):
        return None

    sanitized = {
        "title": data.get("title"),
        "game_id": data.get("game_id"),
        "url": data.get("url"),
        "image_url": data.get("image_url"),
        "main_story_seconds": data.get("main_story_seconds"),
        "review_score": data.get("review_score"),
        "completion_count": data.get("completion_count"),
        "release_year": data.get("release_year"),
        "profile_platform": data.get("profile_platform"),
        "match_type": data.get("match_type"),
        "match_score": data.get("match_score"),
    }

    return sanitized


def sanitize_opencritic_data(data: object) -> dict | None:
    if not isinstance(data, dict):
        return None

    top_critic_score = to_float(data.get("top_critic_score"))
    percent_recommended = to_float(data.get("percent_recommended"))
    median_score = to_int(data.get("median_score"))
    review_count = to_int(data.get("review_count"))
    release_year = to_int(data.get("release_year"))

    sanitized = {
        "title": data.get("title"),
        "game_id": to_int(data.get("game_id")),
        "url": data.get("url"),
        "top_critic_score": (
            round(top_critic_score, 1)
            if top_critic_score is not None and top_critic_score > 0
            else None
        ),
        "percent_recommended": (
            round(percent_recommended, 1)
            if percent_recommended is not None
            and percent_recommended >= 0
            else None
        ),
        "tier": data.get("tier"),
        "median_score": (
            median_score
            if median_score is not None and median_score > 0
            else None
        ),
        "review_count": (
            review_count
            if review_count is not None and review_count > 0
            else None
        ),
        "release_year": (
            release_year
            if release_year is not None and release_year > 0
            else None
        ),
        "match_type": data.get("match_type"),
        "match_score": to_int(data.get("match_score")),
    }

    return sanitized


def compute_duration_fit_score(main_story_seconds: object) -> float | None:
    duration_seconds = to_int(main_story_seconds)

    if duration_seconds is None or duration_seconds <= 0:
        return None

    duration_hours = duration_seconds / 3600

    if duration_hours <= SHORT_GAME_TARGET_HOURS:
        return 100.0

    if duration_hours >= SHORT_GAME_MAX_HOURS:
        return 0.0

    normalized = (
        (duration_hours - SHORT_GAME_TARGET_HOURS)
        / (SHORT_GAME_MAX_HOURS - SHORT_GAME_TARGET_HOURS)
    )
    return round((1 - normalized ** 1.15) * 100, 1)


def build_short_game_score(game: dict) -> dict | None:
    opencritic = game.get("opencritic")
    howlongtobeat = game.get("howlongtobeat")

    if not isinstance(opencritic, dict) or not isinstance(
        howlongtobeat, dict
    ):
        return None

    critic_score = to_float(opencritic.get("top_critic_score"))
    duration_seconds = to_int(
        howlongtobeat.get("main_story_seconds")
    )
    duration_fit_score = compute_duration_fit_score(duration_seconds)

    if (
        critic_score is None
        or critic_score <= 0
        or duration_seconds is None
        or duration_fit_score is None
    ):
        return None

    score = (
        critic_score * SHORT_GAME_CRITIC_WEIGHT
        + duration_fit_score * SHORT_GAME_DURATION_WEIGHT
    )

    return {
        "score": round(score, 1),
        "critic_score": round(critic_score, 1),
        "duration_fit_score": duration_fit_score,
        "main_story_hours": round(duration_seconds / 3600, 1),
        "target_hours": SHORT_GAME_TARGET_HOURS,
    }


def build_gfn_alias_index(
    gfn_catalog: dict[str, dict]
) -> dict[str, set[str]]:
    alias_index: dict[str, set[str]] = {}

    for gfn_key, game in gfn_catalog.items():
        for title in title_variants(game["title"]):
            alias_key = normalize_name(title)
            alias_index.setdefault(alias_key, set()).add(gfn_key)

    return alias_index


def generate_product_candidates(product: dict) -> list[tuple[str, int, str]]:
    ordered_values = [
        ("title", 120, product_text(product, "ProductTitle")),
        ("short", 110, product_text(product, "ShortTitle")),
        ("sort", 100, product_text(product, "SortTitle")),
    ]

    candidates = []

    for source, score, value in ordered_values:
        if not value:
            continue

        for variant in title_variants(value):
            candidates.append((variant, score, source))

    for value in get_search_titles(product):
        for variant in title_variants(value):
            candidates.append((variant, 90, "search"))

    seen = set()
    unique_candidates = []

    for title, score, source in candidates:
        normalized = normalize_name(title)

        if not normalized or normalized in seen:
            continue

        seen.add(normalized)
        unique_candidates.append((title, score, source))

    return unique_candidates


def find_product_match(
    product: dict,
    alias_index: dict[str, set[str]],
    gfn_catalog: dict[str, dict],
) -> MatchResult | None:
    candidates = generate_product_candidates(product)

    for title, score, source in candidates:
        matches = alias_index.get(normalize_name(title), set())

        if len(matches) == 1:
            return MatchResult(
                gfn_key=next(iter(matches)),
                match_type=f"exact-{source}",
                score=score,
            )

    partial_matches: dict[str, int] = {}

    for title, score, source in candidates:
        normalized = normalize_name(title)

        if (
            len(normalized) < 12
            or normalized.count(" ") < 1
        ):
            continue

        matches = []

        for gfn_key, game in gfn_catalog.items():
            gfn_normalized = game["normalized_title"]

            if (
                normalized in gfn_normalized
                or gfn_normalized in normalized
            ):
                matches.append(gfn_key)

        if len(matches) == 1:
            partial_matches[matches[0]] = max(
                partial_matches.get(matches[0], 0),
                score - 25,
            )

    if len(partial_matches) == 1:
        gfn_key, score = next(iter(partial_matches.items()))
        return MatchResult(
            gfn_key=gfn_key,
            match_type="partial",
            score=score,
        )

    return None


def fetch_xbox_pc_product_ids() -> list[str]:
    url = XBOX_SIGL_URL.format(
        sigl_id=XBOX_PC_SIGL_ID,
        language=XBOX_LANGUAGE,
        market=XBOX_MARKET,
        platform_context=XBOX_PLATFORM_CONTEXT,
        subscription_context=XBOX_SUBSCRIPTION_CONTEXT,
    )
    payload = request_json(url)

    if not isinstance(payload, list):
        raise RuntimeError("Xbox SIGL response was not a list")

    product_ids = []

    for item in payload:
        if not isinstance(item, dict):
            continue

        product_id = item.get("id")

        if isinstance(product_id, str) and product_id.strip():
            product_ids.append(product_id.strip())

    return list(dict.fromkeys(product_ids))


def fetch_xbox_products(product_ids: list[str]) -> list[dict]:
    products = []

    for batch in batched(product_ids, PRODUCT_BATCH_SIZE):
        url = XBOX_PRODUCTS_URL.format(
            big_ids=",".join(batch),
            market=XBOX_MARKET,
            language=XBOX_LANGUAGE,
        )
        payload = request_json(url)

        if not isinstance(payload, dict):
            raise RuntimeError("Xbox products response was not an object")

        batch_products = payload.get("Products")

        if not isinstance(batch_products, list):
            raise RuntimeError(
                "Xbox products response did not contain a products list"
            )

        products.extend(
            product
            for product in batch_products
            if isinstance(product, dict)
        )

    return products


def extract_release_date(product: dict) -> str | None:
    market_properties = first_dict(product.get("MarketProperties"))
    value = market_properties.get("OriginalReleaseDate")

    if isinstance(value, str):
        value = value.strip()
        return value or None

    return None


def extract_release_year(value: str | None) -> int | None:
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        ).year
    except ValueError:
        return None


def build_gfn_catalog(games: list[dict]) -> dict[str, dict]:
    catalog = {}

    for game in games:
        title = get_game_title(game)

        if not title:
            continue

        gfn_key = normalize_name(title)
        stores = get_store_names(game)

        catalog[gfn_key] = {
            "title": title,
            "normalized_title": gfn_key,
            "stores": stores,
        }

    return catalog


def product_has_image(product: dict) -> bool:
    return bool(choose_product_image(product))


def same_origin_url(base_url: str, value: str) -> str | None:
    candidate = urljoin(base_url, value)

    if candidate.startswith(base_url):
        return candidate

    return None


class HltbClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": HLTB_USER_AGENT,
                "Referer": HLTB_BASE_URL,
                "Accept": "*/*",
            }
        )
        self.search_path: str | None = None
        self.auth_payload: dict[str, str] | None = None

    def get_search_path(self) -> str:
        if self.search_path:
            return self.search_path

        response = self.session.get(
            HLTB_BASE_URL,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        scripts = SCRIPT_SRC_PATTERN.findall(response.text)

        for source in scripts:
            script_url = same_origin_url(HLTB_BASE_URL, source)

            if not script_url:
                continue

            script_response = self.session.get(
                script_url,
                timeout=REQUEST_TIMEOUT,
            )

            if script_response.status_code >= 400:
                continue

            match = HLTB_ENDPOINT_PATTERN.search(
                script_response.text
            )

            if match:
                self.search_path = (
                    "/api/" + match.group(1).strip("/")
                )
                return self.search_path

        self.search_path = HLTB_SEARCH_PATH_FALLBACK
        return self.search_path

    def get_auth_payload(self) -> dict[str, str]:
        if self.auth_payload is not None:
            return dict(self.auth_payload)

        search_path = self.get_search_path()
        response = self.session.get(
            HLTB_BASE_URL + search_path.lstrip("/") + "/init",
            params={
                "t": int(time.time() * 1000),
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()

        if not isinstance(payload, dict):
            raise RuntimeError(
                "HowLongToBeat init response was not an object"
            )

        token = payload.get("token")
        hp_key = payload.get("hpKey")
        hp_val = payload.get("hpVal")

        auth_payload = {}

        if isinstance(token, str) and token:
            auth_payload["x-auth-token"] = token

        if isinstance(hp_key, str) and hp_key:
            auth_payload["x-hp-key"] = hp_key

        if isinstance(hp_val, str) and hp_val:
            auth_payload["x-hp-val"] = hp_val

        self.auth_payload = auth_payload
        return dict(auth_payload)

    def build_payload(self, title: str) -> dict:
        auth_headers = self.get_auth_payload()
        payload = {
            "searchType": "games",
            "searchTerms": title.split(),
            "searchPage": 1,
            "size": HLTB_SEARCH_PAGE_SIZE,
            "searchOptions": {
                "games": {
                    "userId": 0,
                    "platform": "",
                    "sortCategory": "popular",
                    "rangeCategory": "main",
                    "rangeTime": {
                        "min": 0,
                        "max": 0,
                    },
                    "gameplay": {
                        "perspective": "",
                        "flow": "",
                        "genre": "",
                        "difficulty": "",
                    },
                    "rangeYear": {
                        "max": "",
                        "min": "",
                    },
                    "modifier": "hide_dlc",
                },
                "users": {
                    "sortCategory": "postcount",
                },
                "lists": {
                    "sortCategory": "follows",
                },
                "filter": "",
                "sort": 0,
                "randomizer": 0,
            },
            "useCache": True,
        }

        hp_key = auth_headers.get("x-hp-key")
        hp_val = auth_headers.get("x-hp-val")

        if hp_key and hp_val:
            payload[hp_key] = hp_val

        return payload

    def search(self, title: str) -> list[dict]:
        search_path = self.get_search_path()
        auth_headers = self.get_auth_payload()
        response = self.session.post(
            HLTB_BASE_URL + search_path.lstrip("/"),
            headers={
                "Content-Type": "application/json",
                "Origin": HLTB_BASE_URL.rstrip("/"),
                **auth_headers,
            },
            json=self.build_payload(title),
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code in (401, 403):
            self.auth_payload = None
            auth_headers = self.get_auth_payload()
            response = self.session.post(
                HLTB_BASE_URL + search_path.lstrip("/"),
                headers={
                    "Content-Type": "application/json",
                    "Origin": HLTB_BASE_URL.rstrip("/"),
                    **auth_headers,
                },
                json=self.build_payload(title),
                timeout=REQUEST_TIMEOUT,
            )

        response.raise_for_status()
        payload = response.json()

        if not isinstance(payload, dict):
            raise RuntimeError(
                "HowLongToBeat search response was not an object"
            )

        rows = payload.get("data", [])

        if not isinstance(rows, list):
            raise RuntimeError(
                "HowLongToBeat search response did not contain a results list"
            )

        return [
            row
            for row in rows
            if isinstance(row, dict)
        ]


class OpenCriticClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json, text/plain, */*",
            }
        )
        self.api_base_url: str | None = None
        self.api_key: str | None = None
        self.game_cache: dict[int, dict] = {}

    def get_client_config(self) -> tuple[str, str]:
        if self.api_base_url and self.api_key:
            return self.api_base_url, self.api_key

        response = self.session.get(
            OPENCRITIC_BASE_URL,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        scripts = SCRIPT_SRC_PATTERN.findall(response.text)

        for source in scripts:
            script_url = same_origin_url(OPENCRITIC_BASE_URL, source)

            if not script_url:
                continue

            script_response = self.session.get(
                script_url,
                timeout=REQUEST_TIMEOUT,
            )

            if script_response.status_code >= 400:
                continue

            match = OPENCRITIC_CLIENT_CONFIG_PATTERN.search(
                script_response.text
            )

            if match:
                self.api_base_url = match.group(1).rstrip("/")
                self.api_key = match.group(2)
                return self.api_base_url, self.api_key

        raise RuntimeError(
            "OpenCritic client config could not be discovered"
        )

    def build_auth_header(self) -> str:
        _, api_key = self.get_client_config()
        token = base64.b64encode(
            api_key.encode("utf-8")
        ).decode("ascii")
        return "Bearer " + token

    def request_json(self, path: str) -> object:
        api_base_url, _ = self.get_client_config()
        last_error: requests.RequestException | None = None

        for attempt in range(3):
            try:
                response = self.session.get(
                    api_base_url + path,
                    headers={
                        "Authorization": self.build_auth_header(),
                    },
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_error = exc

                if attempt == 2:
                    break

                time.sleep(1 + attempt)

        assert last_error is not None
        raise last_error

    def search(self, title: str) -> list[dict]:
        payload = self.request_json(
            OPENCRITIC_SEARCH_PATH.format(
                criteria=quote(title),
            )
        )

        if not isinstance(payload, list):
            raise RuntimeError(
                "OpenCritic search response was not a list"
            )

        return [
            row
            for row in payload
            if isinstance(row, dict)
        ]

    def get_game(self, game_id: int) -> dict | None:
        if game_id in self.game_cache:
            return self.game_cache[game_id]

        payload = self.request_json(
            OPENCRITIC_GAME_PATH.format(game_id=game_id)
        )

        if not isinstance(payload, dict):
            raise RuntimeError(
                "OpenCritic game response was not an object"
            )

        self.game_cache[game_id] = payload
        return payload


def score_hltb_candidate(
    row: dict,
    desired_titles: list[str],
    release_year: int | None,
) -> HltbMatchResult | None:
    game_name = row.get("game_name")

    if not isinstance(game_name, str) or not game_name.strip():
        return None

    row_variants = {
        normalize_name(variant)
        for variant in title_variants(game_name)
    }
    alias_text = row.get("game_alias")

    if isinstance(alias_text, str) and alias_text.strip():
        for alias in alias_text.split(","):
            for variant in title_variants(alias.strip()):
                row_variants.add(normalize_name(variant))

    desired_variants = set()

    for title in desired_titles:
        for variant in title_variants(title):
            desired_variants.add(normalize_name(variant))

    match_type = ""
    score = -1

    if row_variants & desired_variants:
        match_type = "exact"
        score = HLTB_MATCH_EXACT_SCORE
    else:
        partial = False

        for desired in desired_variants:
            if len(desired) < 10:
                continue

            for candidate in row_variants:
                if len(candidate) < 10:
                    continue

                if desired in candidate or candidate in desired:
                    partial = True
                    break

            if partial:
                break

        if partial:
            match_type = "partial"
            score = HLTB_MATCH_PARTIAL_SCORE
        else:
            return None

    result_year = row.get("release_world")

    if isinstance(result_year, int) and release_year is not None:
        if result_year == release_year:
            score += 12
        elif abs(result_year - release_year) == 1:
            score += 6

    platforms = row.get("profile_platform")

    if isinstance(platforms, str) and "PC" in platforms:
        score += 5

    popularity = row.get("profile_popular")

    if isinstance(popularity, int):
        score += min(popularity, 50) // 10

    game_id = row.get("game_id")

    if not isinstance(game_id, int):
        return None

    return HltbMatchResult(
        data={
            "title": game_name.strip(),
            "game_id": game_id,
            "url": HLTB_GAME_URL + str(game_id),
            "image_url": (
                HLTB_IMAGE_URL + str(row["game_image"])
                if isinstance(row.get("game_image"), str)
                and row.get("game_image")
                else None
            ),
            "main_story_seconds": (
                row.get("comp_main")
                if isinstance(row.get("comp_main"), int)
                and row.get("comp_main") > 0
                else None
            ),
            "review_score": (
                row.get("review_score")
                if isinstance(row.get("review_score"), int)
                and row.get("review_score") > 0
                else None
            ),
            "completion_count": (
                row.get("count_comp")
                if isinstance(row.get("count_comp"), int)
                and row.get("count_comp") > 0
                else None
            ),
            "release_year": (
                row.get("release_world")
                if isinstance(row.get("release_world"), int)
                and row.get("release_world") > 0
                else None
            ),
            "profile_platform": (
                platforms.strip()
                if isinstance(platforms, str) and platforms.strip()
                else None
            ),
        },
        score=score,
        match_type=match_type,
    )


def find_hltb_match(
    client: HltbClient,
    game: dict,
) -> dict | None:
    search_titles = [game["title"]]

    if (
        isinstance(game.get("xbox_title"), str)
        and game["xbox_title"] != game["title"]
    ):
        search_titles.append(game["xbox_title"])

    desired_titles = list(dict.fromkeys(search_titles))
    release_year = extract_release_year(game.get("release_date"))
    best_match: HltbMatchResult | None = None

    for search_title in desired_titles:
        rows = client.search(search_title)

        for row in rows:
            candidate = score_hltb_candidate(
                row=row,
                desired_titles=desired_titles,
                release_year=release_year,
            )

            if candidate is None:
                continue

            if (
                best_match is None
                or candidate.score > best_match.score
            ):
                best_match = candidate

        if (
            best_match is not None
            and best_match.score >= HLTB_MATCH_EXACT_SCORE
        ):
            break

    if best_match is None or best_match.score < HLTB_MATCH_MIN_SCORE:
        return None

    return {
        **best_match.data,
        "match_type": best_match.match_type,
        "match_score": best_match.score,
    }


def score_opencritic_candidate(
    search_row: dict,
    detail: dict,
    desired_titles: list[str],
    release_year: int | None,
) -> OpenCriticMatchResult | None:
    game_name = detail.get("name") or search_row.get("name")

    if not isinstance(game_name, str) or not game_name.strip():
        return None

    row_variants = {
        normalize_name(variant)
        for variant in title_variants(game_name)
    }
    desired_variants = set()

    for title in desired_titles:
        for variant in title_variants(title):
            desired_variants.add(normalize_name(variant))

    match_type = ""
    score = -1

    if row_variants & desired_variants:
        match_type = "exact"
        score = OPENCRITIC_MATCH_EXACT_SCORE
    else:
        partial = False

        for desired in desired_variants:
            if len(desired) < 10:
                continue

            for candidate in row_variants:
                if len(candidate) < 10:
                    continue

                if desired in candidate or candidate in desired:
                    partial = True
                    break

            if partial:
                break

        if partial:
            match_type = "partial"
            score = OPENCRITIC_MATCH_PARTIAL_SCORE
        else:
            return None

    distance = to_float(search_row.get("dist"))

    if distance is not None:
        normalized_distance = min(max(distance, 0.0), 1.0)
        score += round((1 - normalized_distance) * 15)

    result_year = extract_release_year(detail.get("firstReleaseDate"))

    if result_year is not None and release_year is not None:
        if result_year == release_year:
            score += 12
        elif abs(result_year - release_year) == 1:
            score += 6

    review_count = to_int(detail.get("numReviews"))

    if review_count is not None and review_count > 0:
        score += min(review_count, 200) // 20

    game_id = to_int(detail.get("id"))

    if game_id is None:
        return None

    return OpenCriticMatchResult(
        data={
            "title": game_name.strip(),
            "game_id": game_id,
            "url": detail.get("url"),
            "top_critic_score": to_float(
                detail.get("topCriticScore")
            ),
            "percent_recommended": to_float(
                detail.get("percentRecommended")
            ),
            "tier": (
                detail.get("tier").strip()
                if isinstance(detail.get("tier"), str)
                and detail.get("tier").strip()
                else None
            ),
            "median_score": to_int(detail.get("medianScore")),
            "review_count": review_count,
            "release_year": result_year,
        },
        score=score,
        match_type=match_type,
    )


def find_opencritic_match(
    client: OpenCriticClient,
    game: dict,
) -> dict | None:
    search_titles = [game["title"]]

    if (
        isinstance(game.get("xbox_title"), str)
        and game["xbox_title"] != game["title"]
    ):
        search_titles.append(game["xbox_title"])

    desired_titles = list(dict.fromkeys(search_titles))
    release_year = extract_release_year(game.get("release_date"))
    best_match: OpenCriticMatchResult | None = None

    for search_title in desired_titles:
        rows = client.search(search_title)

        for row in rows:
            game_id = to_int(row.get("id"))

            if game_id is None:
                continue

            detail = client.get_game(game_id)

            if not isinstance(detail, dict):
                continue

            candidate = score_opencritic_candidate(
                search_row=row,
                detail=detail,
                desired_titles=desired_titles,
                release_year=release_year,
            )

            if candidate is None:
                continue

            if (
                best_match is None
                or candidate.score > best_match.score
            ):
                best_match = candidate

        if (
            best_match is not None
            and best_match.score >= OPENCRITIC_MATCH_EXACT_SCORE
        ):
            break

    if (
        best_match is None
        or best_match.score < OPENCRITIC_MATCH_MIN_SCORE
    ):
        return None

    return {
        **best_match.data,
        "match_type": best_match.match_type,
        "match_score": best_match.score,
    }


def is_hltb_cache_fresh(
    entry: dict,
    now: datetime,
) -> bool:
    fetched_at = parse_iso_datetime(entry.get("fetched_at"))

    if fetched_at is None:
        return False

    max_age = (
        HLTB_NOT_FOUND_CACHE_MAX_AGE
        if entry.get("status") == "not_found"
        else HLTB_CACHE_MAX_AGE
    )

    return now - fetched_at <= max_age


def is_opencritic_cache_fresh(
    entry: dict,
    now: datetime,
) -> bool:
    fetched_at = parse_iso_datetime(entry.get("fetched_at"))

    if fetched_at is None:
        return False

    max_age = (
        HLTB_NOT_FOUND_CACHE_MAX_AGE
        if entry.get("status") == "not_found"
        else HLTB_CACHE_MAX_AGE
    )

    return now - fetched_at <= max_age


def get_hltb_data(
    client: HltbClient,
    cache_entries: dict[str, dict],
    game: dict,
    now: datetime,
) -> dict | None:
    cache_key = game["normalized_title"]
    cached_entry = cache_entries.get(cache_key)

    if (
        isinstance(cached_entry, dict)
        and is_hltb_cache_fresh(cached_entry, now)
    ):
        sanitized = sanitize_hltb_data(cached_entry.get("data"))
        cache_entries[cache_key] = {
            **cached_entry,
            "data": sanitized,
        }
        return sanitized

    try:
        hltb_data = find_hltb_match(
            client=client,
            game=game,
        )
    except requests.RequestException as exc:
        if isinstance(cached_entry, dict):
            print(
                "WARNING: failed to refresh HLTB data for "
                f"{game['title']}: {exc}; keeping cached value"
            )
            return sanitize_hltb_data(cached_entry.get("data"))

        raise RuntimeError(
            f"Failed to fetch HowLongToBeat data for {game['title']}: {exc}"
        ) from exc

    cache_entries[cache_key] = {
        "title": game["title"],
        "status": (
            "matched"
            if hltb_data is not None
            else "not_found"
        ),
        "fetched_at": now.isoformat(),
        "data": sanitize_hltb_data(hltb_data),
    }

    return sanitize_hltb_data(hltb_data)


def get_opencritic_data(
    client: OpenCriticClient,
    cache_entries: dict[str, dict],
    game: dict,
    now: datetime,
) -> dict | None:
    cache_key = game["normalized_title"]
    cached_entry = cache_entries.get(cache_key)

    if (
        isinstance(cached_entry, dict)
        and is_opencritic_cache_fresh(cached_entry, now)
    ):
        sanitized = sanitize_opencritic_data(
            cached_entry.get("data")
        )
        cache_entries[cache_key] = {
            **cached_entry,
            "data": sanitized,
        }
        return sanitized

    try:
        opencritic_data = find_opencritic_match(
            client=client,
            game=game,
        )
    except requests.RequestException as exc:
        if isinstance(cached_entry, dict):
            print(
                "WARNING: failed to refresh OpenCritic data for "
                f"{game['title']}: {exc}; keeping cached value"
            )
            return sanitize_opencritic_data(
                cached_entry.get("data")
            )

        raise RuntimeError(
            f"Failed to fetch OpenCritic data for {game['title']}: {exc}"
        ) from exc

    cache_entries[cache_key] = {
        "title": game["title"],
        "status": (
            "matched"
            if opencritic_data is not None
            else "not_found"
        ),
        "fetched_at": now.isoformat(),
        "data": sanitize_opencritic_data(opencritic_data),
    }

    return sanitize_opencritic_data(opencritic_data)


def build_match_entry(
    gfn_key: str,
    gfn_game: dict,
    product: dict,
    match: MatchResult,
) -> dict:
    title = product_text(product, "ProductTitle") or gfn_game["title"]
    publisher = product_text(product, "PublisherName")
    developer = product_text(product, "DeveloperName")
    description = product_text(product, "ShortDescription")
    image_url = choose_product_image(product)
    xbox_product_id = str(product.get("ProductId", "")).strip()

    return {
        "catalog_group": "intersection",
        "is_on_gfn": True,
        "title": gfn_game["title"],
        "normalized_title": gfn_key,
        "gfn_stores": gfn_game["stores"],
        "gfn_has_xbox_store": "XBOX" in gfn_game["stores"],
        "xbox_title": title,
        "xbox_product_id": xbox_product_id,
        "xbox_url": (
            "https://www.microsoft.com/store/productId/"
            + quote(xbox_product_id)
            if xbox_product_id
            else None
        ),
        "publisher": publisher,
        "developer": developer,
        "release_date": extract_release_date(product),
        "image_url": image_url,
        "description": description,
        "match_type": match.match_type,
        "match_score": match.score,
    }


def build_xbox_only_entry(product: dict) -> dict | None:
    title = product_text(product, "ProductTitle")

    if not title:
        return None

    normalized_title = normalize_name(title)
    publisher = product_text(product, "PublisherName")
    developer = product_text(product, "DeveloperName")
    description = product_text(product, "ShortDescription")
    image_url = choose_product_image(product)
    xbox_product_id = str(product.get("ProductId", "")).strip()

    return {
        "catalog_group": "xbox-only",
        "is_on_gfn": False,
        "title": title,
        "normalized_title": normalized_title,
        "gfn_stores": [],
        "gfn_has_xbox_store": False,
        "xbox_title": title,
        "xbox_product_id": xbox_product_id,
        "xbox_url": (
            "https://www.microsoft.com/store/productId/"
            + quote(xbox_product_id)
            if xbox_product_id
            else None
        ),
        "publisher": publisher,
        "developer": developer,
        "release_date": extract_release_date(product),
        "image_url": image_url,
        "description": description,
        "match_type": "xbox-only",
        "match_score": 0,
    }


def prefer_match(candidate: dict, current: dict | None) -> bool:
    if current is None:
        return True

    if candidate["match_score"] != current["match_score"]:
        return candidate["match_score"] > current["match_score"]

    if candidate["gfn_has_xbox_store"] != current["gfn_has_xbox_store"]:
        return candidate["gfn_has_xbox_store"]

    if bool(candidate["image_url"]) != bool(current["image_url"]):
        return bool(candidate["image_url"])

    return len(candidate["xbox_title"]) < len(current["xbox_title"])


def prefer_xbox_only_entry(candidate: dict, current: dict | None) -> bool:
    if current is None:
        return True

    if bool(candidate["image_url"]) != bool(current["image_url"]):
        return bool(candidate["image_url"])

    if bool(candidate["description"]) != bool(current["description"]):
        return bool(candidate["description"])

    candidate_release = candidate.get("release_date") or ""
    current_release = current.get("release_date") or ""

    if candidate_release != current_release:
        return candidate_release > current_release

    return len(candidate["title"]) < len(current["title"])


def summarize_matches(matches: list[dict]) -> dict:
    store_counts = Counter()
    publisher_counts = Counter()
    exact_matches = 0
    hltb_matches = 0
    opencritic_matches = 0
    short_game_score_matches = 0
    intersection_titles = 0
    xbox_only_titles = 0

    for item in matches:
        if item.get("catalog_group") == "intersection":
            intersection_titles += 1
            for store in item["gfn_stores"]:
                store_counts[store] += 1
        else:
            xbox_only_titles += 1

        if item["publisher"]:
            publisher_counts[item["publisher"]] += 1

        if item.get("catalog_group") == "intersection" and item[
            "match_type"
        ].startswith("exact-"):
            exact_matches += 1

        if item.get("howlongtobeat"):
            hltb_matches += 1

        if item.get("opencritic"):
            opencritic_matches += 1

        if item.get("short_game_score"):
            short_game_score_matches += 1

    return {
        "store_counts": dict(
            sorted(
                store_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ),
        "top_publishers": [
            {
                "name": name,
                "count": count,
            }
            for name, count in publisher_counts.most_common(12)
        ],
        "gfn_xbox_store_matches": sum(
            1
            for item in matches
            if item["gfn_has_xbox_store"]
        ),
        "intersection_titles": intersection_titles,
        "xbox_only_titles": xbox_only_titles,
        "hltb_matches": hltb_matches,
        "opencritic_matches": opencritic_matches,
        "short_game_score_matches": short_game_score_matches,
        "exact_matches": exact_matches,
        "partial_matches": intersection_titles - exact_matches,
    }


def main() -> None:
    timestamp = datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat()

    print("Fetching GeForce NOW catalog...")
    gfn_games = fetch_all_games()
    gfn_catalog = build_gfn_catalog(gfn_games)
    alias_index = build_gfn_alias_index(gfn_catalog)
    hltb_cache_entries = load_hltb_cache()
    opencritic_cache_entries = load_opencritic_cache()
    hltb_client = HltbClient()
    opencritic_client = OpenCriticClient()

    print("Fetching Xbox PC Game Pass catalog...")
    xbox_product_ids = fetch_xbox_pc_product_ids()
    xbox_products = fetch_xbox_products(xbox_product_ids)

    print(
        f"Loaded {len(gfn_catalog)} GFN titles and "
        f"{len(xbox_products)} Xbox PC Game Pass products."
    )

    matches_by_gfn_key = {}
    xbox_only_by_title = {}

    for product in xbox_products:
        match = find_product_match(
            product=product,
            alias_index=alias_index,
            gfn_catalog=gfn_catalog,
        )

        if not match:
            xbox_only_entry = build_xbox_only_entry(product)

            if xbox_only_entry and prefer_xbox_only_entry(
                candidate=xbox_only_entry,
                current=xbox_only_by_title.get(
                    xbox_only_entry["normalized_title"]
                ),
            ):
                xbox_only_by_title[
                    xbox_only_entry["normalized_title"]
                ] = xbox_only_entry
            continue

        entry = build_match_entry(
            gfn_key=match.gfn_key,
            gfn_game=gfn_catalog[match.gfn_key],
            product=product,
            match=match,
        )

        if prefer_match(
            candidate=entry,
            current=matches_by_gfn_key.get(match.gfn_key),
        ):
            matches_by_gfn_key[match.gfn_key] = entry

    matches = sorted(
        [
            *matches_by_gfn_key.values(),
            *xbox_only_by_title.values(),
        ],
        key=lambda item: normalize_name(item["title"]),
    )

    print("Fetching HowLongToBeat data...")

    for index, game in enumerate(matches, start=1):
        print(
            f"  [{index}/{len(matches)}] {game['title']}"
        )
        game["howlongtobeat"] = get_hltb_data(
            client=hltb_client,
            cache_entries=hltb_cache_entries,
            game=game,
            now=datetime.now(timezone.utc).replace(
                microsecond=0
            ),
        )
        game["opencritic"] = get_opencritic_data(
            client=opencritic_client,
            cache_entries=opencritic_cache_entries,
            game=game,
            now=datetime.now(timezone.utc).replace(
                microsecond=0
            ),
        )
        game["short_game_score"] = build_short_game_score(game)

    summary = summarize_matches(matches)

    output = {
        "generated_at": timestamp,
        "gfn_country": os.getenv("GFN_COUNTRY", "CH"),
        "gfn_language": os.getenv("GFN_LANGUAGE", "en_US"),
        "xbox_market": XBOX_MARKET,
        "xbox_language": XBOX_LANGUAGE,
        "preferences": {
            "short_game_target_hours": SHORT_GAME_TARGET_HOURS,
            "short_game_max_hours": SHORT_GAME_MAX_HOURS,
            "short_game_score_weights": {
                "critic": SHORT_GAME_CRITIC_WEIGHT,
                "duration": SHORT_GAME_DURATION_WEIGHT,
            },
        },
        "counts": {
            "gfn_titles": len(gfn_catalog),
            "xbox_pc_titles": len(xbox_products),
            "catalog_titles": len(matches),
            "intersection_titles": len(matches_by_gfn_key),
            "xbox_only_titles": len(xbox_only_by_title),
        },
        "summary": summary,
        "games": matches,
    }

    save_output(output)
    save_hltb_cache(hltb_cache_entries)
    save_opencritic_cache(opencritic_cache_entries)

    print(
        f"Catalog includes {len(matches)} titles "
        f"({len(matches_by_gfn_key)} on GFN, "
        f"{len(xbox_only_by_title)} Xbox-only)."
    )
    print(
        f"Matched {len(matches_by_gfn_key)} overlapping titles "
        f"({summary['exact_matches']} exact, "
        f"{summary['partial_matches']} partial)."
    )
    print(
        "Found HowLongToBeat data for "
        f"{summary['hltb_matches']} titles."
    )
    print(
        "Found OpenCritic data for "
        f"{summary['opencritic_matches']} titles."
    )
    print(f"Wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Interrupted.")
        sys.exit(130)
    except Exception as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)
