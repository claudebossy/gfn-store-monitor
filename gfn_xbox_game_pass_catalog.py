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
SCRIPT_SRC_PATTERN = re.compile(
    r"<script[^>]+src=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
HLTB_ENDPOINT_PATTERN = re.compile(
    r"fetch\s*\(\s*[\"']/api/([a-zA-Z0-9_/]+)[^\"']*[\"']"
    r"\s*,\s*{[^}]*method:\s*[\"']POST[\"']",
    re.IGNORECASE | re.DOTALL,
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


def same_origin_url(value: str) -> str | None:
    candidate = urljoin(HLTB_BASE_URL, value)

    if candidate.startswith(HLTB_BASE_URL):
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
            script_url = same_origin_url(source)

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
            "main_extra_seconds": (
                row.get("comp_plus")
                if isinstance(row.get("comp_plus"), int)
                and row.get("comp_plus") > 0
                else None
            ),
            "completionist_seconds": (
                row.get("comp_100")
                if isinstance(row.get("comp_100"), int)
                and row.get("comp_100") > 0
                else None
            ),
            "all_styles_seconds": (
                row.get("comp_all")
                if isinstance(row.get("comp_all"), int)
                and row.get("comp_all") > 0
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
        return cached_entry.get("data")

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
            return cached_entry.get("data")

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
        "data": hltb_data,
    }

    return hltb_data


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


def summarize_matches(matches: list[dict]) -> dict:
    store_counts = Counter()
    publisher_counts = Counter()
    exact_matches = 0
    hltb_matches = 0

    for item in matches:
        for store in item["gfn_stores"]:
            store_counts[store] += 1

        if item["publisher"]:
            publisher_counts[item["publisher"]] += 1

        if item["match_type"].startswith("exact-"):
            exact_matches += 1

        if item.get("howlongtobeat"):
            hltb_matches += 1

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
        "hltb_matches": hltb_matches,
        "exact_matches": exact_matches,
        "partial_matches": len(matches) - exact_matches,
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
    hltb_client = HltbClient()

    print("Fetching Xbox PC Game Pass catalog...")
    xbox_product_ids = fetch_xbox_pc_product_ids()
    xbox_products = fetch_xbox_products(xbox_product_ids)

    print(
        f"Loaded {len(gfn_catalog)} GFN titles and "
        f"{len(xbox_products)} Xbox PC Game Pass products."
    )

    matches_by_gfn_key = {}

    for product in xbox_products:
        match = find_product_match(
            product=product,
            alias_index=alias_index,
            gfn_catalog=gfn_catalog,
        )

        if not match:
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
        matches_by_gfn_key.values(),
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

    summary = summarize_matches(matches)

    output = {
        "generated_at": timestamp,
        "gfn_country": os.getenv("GFN_COUNTRY", "CH"),
        "gfn_language": os.getenv("GFN_LANGUAGE", "en_US"),
        "xbox_market": XBOX_MARKET,
        "xbox_language": XBOX_LANGUAGE,
        "counts": {
            "gfn_titles": len(gfn_catalog),
            "xbox_pc_titles": len(xbox_products),
            "intersection_titles": len(matches),
        },
        "summary": summary,
        "games": matches,
    }

    save_output(output)
    save_hltb_cache(hltb_cache_entries)

    print(
        f"Matched {len(matches)} titles "
        f"({summary['exact_matches']} exact, "
        f"{summary['partial_matches']} partial)."
    )
    print(
        "Found HowLongToBeat data for "
        f"{summary['hltb_matches']} titles."
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
