import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup

CONFIG_FILE = Path("instant_gaming_products.json")
OUTPUT_FILE = (
    Path("site")
    / "data"
    / "instant-gaming-price-history.json"
)
REQUEST_TIMEOUT = 60
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/139.0 Safari/537.36"
)


@dataclass(frozen=True)
class ProductConfig:
    id: str
    label: str
    url: str


session = requests.Session()
session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml",
    }
)


def load_config() -> tuple[str, list[ProductConfig]]:
    if not CONFIG_FILE.exists():
        raise RuntimeError(f"{CONFIG_FILE} does not exist")

    try:
        raw_config = json.loads(
            CONFIG_FILE.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{CONFIG_FILE} contains invalid JSON: {exc}"
        ) from exc

    if not isinstance(raw_config, dict):
        raise RuntimeError(
            f"{CONFIG_FILE} must contain a JSON object"
        )

    currency = raw_config.get("currency", "EUR")

    if not isinstance(currency, str) or not currency.strip():
        raise RuntimeError(
            f"{CONFIG_FILE} field 'currency' must be a non-empty string"
        )

    products_value = raw_config.get("products", [])

    if not isinstance(products_value, list):
        raise RuntimeError(
            f"{CONFIG_FILE} field 'products' must be a list"
        )

    products = []
    seen_ids = set()

    for index, item in enumerate(products_value, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(
                f"{CONFIG_FILE} product #{index} must be an object"
            )

        product_id = require_string(
            item,
            "id",
            f"{CONFIG_FILE} product #{index}",
        )
        label = require_string(
            item,
            "label",
            f"{CONFIG_FILE} product #{index}",
        )
        url = require_string(
            item,
            "url",
            f"{CONFIG_FILE} product #{index}",
        )

        if product_id in seen_ids:
            raise RuntimeError(
                f"{CONFIG_FILE} contains duplicate product id: {product_id}"
            )

        seen_ids.add(product_id)
        products.append(
            ProductConfig(
                id=product_id,
                label=label,
                url=url,
            )
        )

    return currency.strip().upper(), products


def require_string(
    container: dict,
    key: str,
    context: str,
) -> str:
    value = container.get(key)

    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(
            f"{context} field '{key}' must be a non-empty string"
        )

    return value.strip()


def load_existing_history() -> dict[str, dict]:
    if not OUTPUT_FILE.exists():
        return {}

    try:
        raw_history = json.loads(
            OUTPUT_FILE.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{OUTPUT_FILE} contains invalid JSON: {exc}"
        ) from exc

    if not isinstance(raw_history, dict):
        raise RuntimeError(
            f"{OUTPUT_FILE} must contain a JSON object"
        )

    products = raw_history.get("products", [])

    if not isinstance(products, list):
        raise RuntimeError(
            f"{OUTPUT_FILE} field 'products' must be a list"
        )

    existing_by_id = {}

    for product in products:
        if not isinstance(product, dict):
            continue

        product_id = product.get("id")

        if isinstance(product_id, str) and product_id.strip():
            existing_by_id[product_id] = product

    return existing_by_id


def save_history(data: dict) -> None:
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


def with_currency(url: str, currency: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["currency"] = currency

    return urlunparse(
        parsed._replace(query=urlencode(query))
    )


def extract_json_assignment(
    html: str,
    marker: str,
) -> dict:
    marker_index = html.find(marker)

    if marker_index == -1:
        raise RuntimeError(
            f"Could not find {marker!r} in page"
        )

    json_start = marker_index + len(marker)

    try:
        value, _ = json.JSONDecoder().raw_decode(
            html[json_start:]
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Could not decode JSON assigned to {marker!r}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise RuntimeError(
            f"JSON assigned to {marker!r} is not an object"
        )

    return value


def parse_title(soup: BeautifulSoup) -> str | None:
    meta = soup.find("meta", attrs={"property": "og:title"})

    if meta and meta.get("content"):
        title = str(meta["content"]).strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()
    else:
        return None

    return re.sub(r"^Buy\s+", "", title, flags=re.IGNORECASE)


def get_meta_content(
    soup: BeautifulSoup,
    property_name: str,
) -> str | None:
    meta = soup.find(
        "meta",
        attrs={"property": property_name},
    )

    if meta and meta.get("content"):
        value = str(meta["content"]).strip()
        return value or None

    return None


def get_canonical_url(soup: BeautifulSoup) -> str | None:
    link = soup.find(
        "link",
        attrs={"rel": lambda value: value and "canonical" in value},
    )

    if link and link.get("href"):
        href = str(link["href"]).strip()
        return href or None

    return None


def parse_price(
    value: object,
    field_name: str,
    product: ProductConfig,
) -> float:
    if isinstance(value, (int, float)):
        return round(float(value), 2)

    if isinstance(value, str):
        stripped = value.strip()

        if stripped:
            try:
                return round(float(stripped), 2)
            except ValueError as exc:
                raise RuntimeError(
                    f"{product.id}: invalid {field_name} value {value!r}"
                ) from exc

    raise RuntimeError(
        f"{product.id}: missing or invalid {field_name}"
    )


def parse_optional_int(value: object) -> int | None:
    if value is None:
        return None

    if isinstance(value, bool):
        return int(value)

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    if isinstance(value, str):
        stripped = value.strip()

        if not stripped:
            return None

        return int(float(stripped))

    return None


def build_history_entry(
    timestamp: str,
    product_model: dict,
    product: ProductConfig,
) -> dict:
    price = parse_price(product_model.get("price"), "price", product)
    retail_price = product_model.get("retail")

    entry = {
        "timestamp": timestamp,
        "price": price,
        "available": bool(product_model.get("has_stock")),
        "discount_percent": parse_optional_int(
            product_model.get("discount")
        ),
        "preorder": bool(product_model.get("preorder")),
    }

    if retail_price is not None:
        entry["retail_price"] = parse_price(
            retail_price,
            "retail",
            product,
        )

    return entry


def clean_history_entries(entries: object) -> list[dict]:
    if not isinstance(entries, list):
        return []

    cleaned = []

    for entry in entries:
        if (
            isinstance(entry, dict)
            and isinstance(entry.get("timestamp"), str)
            and isinstance(entry.get("price"), (int, float))
        ):
            cleaned.append(entry)

    return cleaned


def fetch_product_snapshot(
    product: ProductConfig,
    currency: str,
    timestamp: str,
) -> dict:
    page_url = with_currency(product.url, currency)
    response = session.get(
        page_url,
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    html = response.text
    soup = BeautifulSoup(html, "html.parser")
    product_model = extract_json_assignment(
        html,
        "window.productModel = ",
    )

    history_entry = build_history_entry(
        timestamp,
        product_model,
        product,
    )

    title = parse_title(soup) or product.label

    return {
        "id": product.id,
        "label": product.label,
        "title": title,
        "source_url": product.url,
        "page_url": page_url,
        "image_url": get_meta_content(soup, "og:image"),
        "current": history_entry,
        "history": history_entry,
        "canonical_url": get_canonical_url(soup) or product.url,
    }


def merge_product_history(
    product: ProductConfig,
    snapshot: dict,
    existing_record: dict | None,
) -> dict:
    existing_history = clean_history_entries(
        (existing_record or {}).get("history")
    )
    existing_history.append(snapshot.pop("history"))

    return {
        "id": product.id,
        "label": snapshot["label"],
        "title": snapshot["title"],
        "source_url": snapshot["source_url"],
        "page_url": snapshot["page_url"],
        "canonical_url": snapshot["canonical_url"],
        "image_url": snapshot["image_url"],
        "current": snapshot["current"],
        "history": existing_history,
    }


def main() -> None:
    currency, products = load_config()
    existing_by_id = load_existing_history()
    timestamp = datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat()

    output_products = []

    if not products:
        print("No Instant Gaming products configured.")

    for product in products:
        print(f"Fetching {product.label}...")
        snapshot = fetch_product_snapshot(
            product=product,
            currency=currency,
            timestamp=timestamp,
        )
        output_products.append(
            merge_product_history(
                product=product,
                snapshot=snapshot,
                existing_record=existing_by_id.get(product.id),
            )
        )
        print(
            f"  {snapshot['current']['price']:.2f} {currency}"
            + (
                " (in stock)"
                if snapshot["current"]["available"]
                else " (out of stock)"
            )
        )

    save_history(
        {
            "generated_at": timestamp,
            "currency": currency,
            "products": output_products,
        }
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
