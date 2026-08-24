import json
import os
import re
import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path

import requests

CATALOG_STATE_FILE = Path("catalog_state.json")
CATALOG_CACHE_FILE = Path("gfn_catalog_cache.json")
CATALOG_CACHE_MAX_AGE = 15 * 60

API_URL = "https://api-prod.nvidia.com/services/gfngames/v1/gameList"

GAMES_FILE = Path("games.txt")
STATE_FILE = Path("state.json")

COUNTRY = os.getenv("GFN_COUNTRY", "CH")
LANGUAGE = os.getenv("GFN_LANGUAGE", "en_US")

REQUEST_TIMEOUT = 60
MAX_PAGES = 200


# This is the query currently used by the GFN website/API.
# We deliberately request only the fields needed by this monitor.
QUERY = """
{
  apps(
    country: "%s"
    language: "%s"
    orderBy: "itemMetadata.gfnPopularityRank:ASC,sortName:ASC"
    after: "%s"
  ) {
    numberReturned

    pageInfo {
      endCursor
      hasNextPage
    }

    items {
      title
      sortName

      gfn {
        playType
        minimumMembershipTierLabel
      }

      variants {
        appStore
        publisherName
        minimumSizeInBytes
      }
    }
  }
}
"""


session = requests.Session()

session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 "
            "Chrome/131.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://play.geforcenow.com",
        "Referer": "https://play.geforcenow.com/",
    }
)


def normalize_name(name: str) -> str:
    """
    Normalize game names so that harmless differences in punctuation,
    capitalization, etc. don't prevent matching.
    """
    name = name.casefold()

    # Replace punctuation with spaces.
    name = re.sub(r"[^\w\s]", " ", name, flags=re.UNICODE)

    # Collapse whitespace.
    name = re.sub(r"\s+", " ", name)

    return name.strip()


def load_watched_games() -> list[str]:
    if not GAMES_FILE.exists():
        raise RuntimeError(f"{GAMES_FILE} does not exist")

    games = []

    for line in GAMES_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line:
            continue

        if line.startswith("#"):
            continue

        games.append(line)

    # Remove duplicates while preserving order.
    return list(dict.fromkeys(games))


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}

    try:
        return json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{STATE_FILE} contains invalid JSON: {exc}"
        ) from exc


def save_state(state: dict) -> None:
    temporary_file = STATE_FILE.with_suffix(".tmp")

    temporary_file.write_text(
        json.dumps(
            state,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    temporary_file.replace(STATE_FILE)


def build_query(cursor: str) -> str:
    return QUERY % (
        COUNTRY,
        LANGUAGE,
        cursor,
    )


def request_page(cursor: str) -> dict:
    query = build_query(cursor)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 "
            "Chrome/131.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Content-Type": "text/plain",
        "Origin": "https://play.geforcenow.com",
        "Referer": "https://play.geforcenow.com/",
    }

    for attempt in range(1, 4):
        try:
            response = requests.post(
                API_URL,
                data=query.encode("utf-8"),
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code >= 400:
                print(
                    f"NVIDIA response "
                    f"{response.status_code}:"
                )
                print(response.text[:2000])

            response.raise_for_status()

            data = response.json()

            if "errors" in data:
                raise RuntimeError(
                    "NVIDIA API returned errors: "
                    + json.dumps(data["errors"])
                )

            if "data" not in data:
                raise RuntimeError(
                    "NVIDIA response has no 'data' field"
                )

            return data["data"]

        except (
            requests.RequestException,
            ValueError,
        ) as exc:
            if attempt == 3:
                raise RuntimeError(
                    f"Failed to request NVIDIA API "
                    f"after {attempt} attempts: {exc}"
                ) from exc

            print(
                f"Request failed "
                f"(attempt {attempt}/3): {exc}"
            )

            time.sleep(attempt * 2)

    raise RuntimeError("Unreachable")


def load_cached_catalog() -> list[dict] | None:
    if not CATALOG_CACHE_FILE.exists():
        return None

    try:
        cache = json.loads(
            CATALOG_CACHE_FILE.read_text(
                encoding="utf-8"
            )
        )

        cached_at = cache["cached_at"]
        catalog = cache["games"]

        age = time.time() - cached_at

        if age < 0 or age >= CATALOG_CACHE_MAX_AGE:
            print(
                f"Catalog cache expired "
                f"({age / 60:.1f} minutes old)."
            )
            return None

        print(
            f"Using cached catalog "
            f"({age:.1f} seconds old, "
            f"{len(catalog)} games)."
        )

        return catalog

    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(
            f"WARNING: invalid catalog cache: {exc}"
        )
        return None


def save_catalog_cache(catalog: list[dict]) -> None:
    cache = {
        "cached_at": time.time(),
        "games": catalog,
    }

    temporary_file = CATALOG_CACHE_FILE.with_suffix(".tmp")

    temporary_file.write_text(
        json.dumps(
            cache,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    temporary_file.replace(CATALOG_CACHE_FILE)


def fetch_all_games() -> list[dict]:
    """
    Retrieve the complete GFN catalog.

    A successful catalog fetch is cached for 15 minutes.
    """

    cached = load_cached_catalog()

    if cached is not None:
        return cached

    all_games = []

    cursor = ""
    seen_cursors = set()

    for page_number in range(1, MAX_PAGES + 1):
        print(
            f"Fetching GFN catalog page {page_number}"
            + (
                f" (cursor={cursor})"
                if cursor
                else " (first page)"
            )
        )

        if cursor in seen_cursors:
            raise RuntimeError(
                f"Pagination loop detected at cursor: {cursor!r}"
            )

        seen_cursors.add(cursor)

        data = request_page(cursor)

        apps = data.get("apps")

        if not isinstance(apps, dict):
            raise RuntimeError(
                "NVIDIA API response does not contain "
                "the expected 'apps' object"
            )

        items = apps.get("items")

        if not isinstance(items, list):
            raise RuntimeError(
                "NVIDIA API response does not contain "
                "an 'items' list"
            )

        number_returned = apps.get(
            "numberReturned",
            len(items),
        )

        all_games.extend(items)

        print(
            f"  received {len(items)} games "
            f"(reported: {number_returned})"
        )
        print(
            f"  total so far: {len(all_games)}"
        )

        # NVIDIA may signal the end either through
        # numberReturned or pageInfo.
        if number_returned == 0:
            print("No more games returned.")
            break

        page_info = apps.get("pageInfo")

        if not isinstance(page_info, dict):
            raise RuntimeError(
                "NVIDIA API response does not contain "
                "'pageInfo'"
            )

        if not page_info.get("hasNextPage", False):
            print("Pagination finished.")
            break

        next_cursor = page_info.get("endCursor")

        if not next_cursor:
            raise RuntimeError(
                "NVIDIA API says another page exists, "
                "but did not provide endCursor"
            )

        if next_cursor == cursor:
            raise RuntimeError(
                "NVIDIA API returned the same cursor twice"
            )

        cursor = next_cursor

    else:
        raise RuntimeError(
            f"Pagination exceeded MAX_PAGES={MAX_PAGES}"
        )

    # Deduplicate the complete catalog.
    unique_games = {}

    for game in all_games:
        title = get_game_title(game)

        if title:
            key = normalize_name(title)
            unique_games.setdefault(key, game)

    catalog = list(unique_games.values())

    print(
        f"Finished fetching catalog: "
        f"{len(catalog)} unique games"
    )

    # Only cache a successfully completed catalog.
    save_catalog_cache(catalog)

    print(
        f"Saved catalog cache to "
        f"{CATALOG_CACHE_FILE}"
    )

    return catalog


def get_game_title(game: dict) -> str | None:
    for key in ("title", "sortName"):
        value = game.get(key)

        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def get_store_names(game: dict) -> list[str]:
    """
    Extract store names from variants[].appStore.

    A game can have multiple variants belonging to the same
    store, so stores are deduplicated.
    """

    variants = game.get("variants")

    if not isinstance(variants, list):
        return []

    stores = set()

    for variant in variants:
        if not isinstance(variant, dict):
            continue

        store = variant.get("appStore")

        if isinstance(store, str):
            store = store.strip()

            if store:
                stores.add(store)

    return sorted(stores, key=str.casefold)


def make_catalog_index(games: list[dict]) -> dict:
    index = {}

    for game in games:
        title = get_game_title(game)

        if not title:
            continue

        key = normalize_name(title)

        # Keep the first occurrence.
        index.setdefault(key, game)

    return index


def find_game(
    requested_name: str,
    catalog_index: dict,
) -> dict | None:
    """
    First try exact normalized matching.

    Then try a conservative unique partial match.
    """

    normalized = normalize_name(requested_name)

    exact = catalog_index.get(normalized)

    if exact:
        return exact

    matches = []

    for key, game in catalog_index.items():
        if (
            normalized in key
            or key in normalized
        ):
            matches.append(game)

    if len(matches) == 1:
        return matches[0]

    return None


def compare_games(
    watched_games: list[str],
    catalog_index: dict,
    old_state: dict,
) -> tuple[dict, list[dict]]:
    """
    Return:
      new_state
      changes

    We only update the state for games that are successfully found
    and have a non-empty store list.

    This is intentional: if NVIDIA temporarily returns an incomplete
    response, we don't want to interpret that as stores disappearing.
    """

    new_state = dict(old_state)
    changes = []

    for requested_name in watched_games:
        game = find_game(
            requested_name,
            catalog_index,
        )

        if game is None:
            print(
                f"WARNING: game not found: {requested_name}"
            )
            continue

        actual_name = (
            get_game_title(game)
            or requested_name
        )

        stores = get_store_names(game)

        if not stores:
            print(
                f"WARNING: no stores returned for "
                f"{actual_name}; keeping previous state"
            )
            continue

        previous = old_state.get(
            requested_name,
            {},
        )

        previous_stores = set(
            previous.get("stores", [])
        )

        current_stores = set(stores)

        new_stores = sorted(
            current_stores - previous_stores,
            key=str.casefold,
        )

        print(
            f"{actual_name}: "
            f"{', '.join(stores)}"
        )

        # Save current state.
        new_state[requested_name] = {
            "name": actual_name,
            "stores": stores,
        }

        if new_stores:
            changes.append(
                {
                    "requested_name": requested_name,
                    "name": actual_name,
                    "new_stores": new_stores,
                    "stores": stores,
                }
            )

    return new_state, changes


def send_email(changes: dict) -> None:
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(
        os.environ.get("SMTP_PORT", "587")
    )
    smtp_username = os.environ["SMTP_USERNAME"]
    smtp_password = os.environ["SMTP_PASSWORD"]

    email_from = os.environ["EMAIL_FROM"]
    email_to = os.environ["EMAIL_TO"]

    new_games = changes["new_games"]
    new_stores = changes["new_stores"]

    total_changes = (
        len(new_games) +
        len(new_stores)
    )

    subject = (
        f"GeForce NOW catalog changed "
        f"({total_changes} change"
        f"{'' if total_changes == 1 else 's'})"
    )

    lines = [
        "GeForce NOW catalog changes detected.",
        "",
        f"Country: {COUNTRY}",
        f"Language: {LANGUAGE}",
        "",
    ]

    if new_games:
        lines.extend(
            [
                "NEW GAMES",
                "=========",
                "",
            ]
        )

        for game in new_games:
            lines.append(
                f"- {game['name']}"
            )

            if game["stores"]:
                lines.append(
                    "  Stores: "
                    + ", ".join(game["stores"])
                )
            else:
                lines.append(
                    "  Stores: unknown"
                )

            lines.append("")

    if new_stores:
        lines.extend(
            [
                "NEW STORE AVAILABILITY",
                "=======================",
                "",
            ]
        )

        for game in new_stores:
            lines.append(
                f"- {game['name']}"
            )

            lines.append(
                "  New stores: "
                + ", ".join(game["new_stores"])
            )

            lines.append(
                "  All stores: "
                + ", ".join(game["stores"])
            )

            lines.append("")

    message = EmailMessage()

    message["Subject"] = subject
    message["From"] = email_from
    message["To"] = email_to

    message.set_content(
        "\n".join(lines)
    )

    print(
        f"Sending catalog-change notification "
        f"to {email_to}"
    )

    with smtplib.SMTP(
        smtp_host,
        smtp_port,
        timeout=30,
    ) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()

        smtp.login(
            smtp_username,
            smtp_password,
        )

        smtp.send_message(message)

def load_catalog_state() -> dict:
    if not CATALOG_STATE_FILE.exists():
        return {}

    try:
        return json.loads(
            CATALOG_STATE_FILE.read_text(
                encoding="utf-8"
            )
        )
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Invalid {CATALOG_STATE_FILE}: {exc}"
        ) from exc


def save_catalog_state(state: dict) -> None:
    temporary_file = CATALOG_STATE_FILE.with_suffix(".tmp")

    temporary_file.write_text(
        json.dumps(
            state,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    temporary_file.replace(CATALOG_STATE_FILE)


def make_catalog_state(catalog: list[dict]) -> dict:
    """
    Convert the complete NVIDIA catalog into a compact structure
    suitable for comparison.

    Key:
        normalized game title

    Value:
        actual title + sorted stores
    """

    state = {}

    for game in catalog:
        title = get_game_title(game)

        if not title:
            continue

        key = normalize_name(title)

        stores = get_store_names(game)

        state[key] = {
            "name": title,
            "stores": stores,
        }

    return state

def compare_catalogs(
    old_catalog: dict,
    new_catalog: dict,
) -> dict:
    """
    Detect additions to the GFN catalog.

    Returns:

    {
        "new_games": [
            {
                "name": "...",
                "stores": [...]
            }
        ],
        "new_stores": [
            {
                "name": "...",
                "stores": [...],
                "new_stores": [...]
            }
        ]
    }
    """

    new_games = []
    new_stores = []

    old_game_keys = set(old_catalog)
    new_game_keys = set(new_catalog)

    # Completely new games.
    for game_key in sorted(
        new_game_keys - old_game_keys
    ):
        game = new_catalog[game_key]

        new_games.append(
            {
                "name": game["name"],
                "stores": game["stores"],
            }
        )

    # Existing games with additional stores.
    for game_key in sorted(
        new_game_keys & old_game_keys
    ):
        old_game = old_catalog[game_key]
        new_game = new_catalog[game_key]

        old_stores = set(
            old_game.get("stores", [])
        )

        new_store_set = set(
            new_game.get("stores", [])
        )

        added_stores = sorted(
            new_store_set - old_stores,
            key=str.casefold,
        )

        if added_stores:
            new_stores.append(
                {
                    "name": new_game["name"],
                    "stores": new_game["stores"],
                    "new_stores": added_stores,
                }
            )

    return {
        "new_games": new_games,
        "new_stores": new_stores,
    }


def main() -> None:
    print("=== GeForce NOW Catalog Monitor ===")
    print(f"Country: {COUNTRY}")
    print(f"Language: {LANGUAGE}")
    print()

    # fetch_all_games() automatically uses the 15-minute
    # catalog cache when available.
    catalog = fetch_all_games()

    new_catalog_state = make_catalog_state(
        catalog
    )

    old_catalog_state = load_catalog_state()

    # Empty state means this is the initial baseline.
    if not old_catalog_state:
        print(
            "No previous catalog state found."
        )

        print(
            f"Creating initial baseline with "
            f"{len(new_catalog_state)} games."
        )

        save_catalog_state(
            new_catalog_state
        )

        print(
            "Initial baseline created. "
            "No notification sent."
        )

        return

    changes = compare_catalogs(
        old_catalog_state,
        new_catalog_state,
    )

    new_games = changes["new_games"]
    new_stores = changes["new_stores"]

    print(
        f"New games: {len(new_games)}"
    )

    print(
        f"New store additions: {len(new_stores)}"
    )

    # Save the new snapshot regardless of whether
    # there were changes.
    save_catalog_state(
        new_catalog_state
    )

    if not new_games and not new_stores:
        print(
            "No catalog additions detected."
        )
        return

    print()
    print("CATALOG CHANGES:")

    for game in new_games:
        print(
            f"  NEW GAME: {game['name']}"
        )

    for game in new_stores:
        print(
            f"  NEW STORE: {game['name']} "
            f"+ {', '.join(game['new_stores'])}"
        )

    # send_email(changes)


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