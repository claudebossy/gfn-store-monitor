# GeForce NOW Store Monitor

Monitors selected games on GeForce NOW and sends an email when a
new supported game store becomes available.

The repository also includes an Instant Gaming price tracker that
stores periodic price snapshots and publishes a static page with a
multi-product price history chart.

For example:

Before:

    Cyberpunk 2077
    STEAM

After:

    Cyberpunk 2077
    STEAM
    EPIC

The monitor sends an email containing:

    Cyberpunk 2077
    New store(s): EPIC

## How it works

The script queries NVIDIA's GeForce NOW game-list API:

    https://api-prod.nvidia.com/services/gfngames/v1/gameList

The catalog is fetched page-by-page using the API's cursor-based
pagination.

The country is configured as:

    CH

The language defaults to:

    en_US

The games to monitor are listed in:

    games.txt

The previous catalog state is stored in:

    catalog_state.json

GitHub Actions commits catalog_state.json after each successful run.

## First run

The first run establishes a baseline.

It does NOT send notifications for stores that already exist.

For example, if the first run sees:

    Cyberpunk 2077
    STEAM
    EPIC

then no email is sent.

If a later run sees:

    Cyberpunk 2077
    STEAM
    EPIC
    GOG

an email is sent because GOG is new.

## GitHub Secrets

Add these repository secrets under:

    Settings
    -> Secrets and variables
    -> Actions

Required:

    SMTP_HOST
    SMTP_PORT
    SMTP_USERNAME
    SMTP_PASSWORD
    EMAIL_FROM
    EMAIL_TO

For Gmail, for example:

    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587

Use a Gmail App Password rather than your normal Gmail password.

## Manual test

The workflow can be started manually from:

    Actions
    -> Monitor GeForce NOW Store Availability
    -> Run workflow

The separate HBO Max check can be run manually with:

    python is_on_hbo.py

It uses the same SMTP_* and EMAIL_* environment variables and sends an
email only when Hacks is present in the HBO Max Switzerland show
sitemap.

There is also a dedicated GitHub Actions workflow for it:

    Actions
    -> Monitor HBO Max Availability
    -> Run workflow

## Schedule

Both workflows normally run every six hours.

GitHub Actions scheduled workflows are not guaranteed to start at
the exact minute specified, so the cron expression should be viewed
as a target schedule rather than an exact polling time.

## Changing the country

The repository is configured for Switzerland:

    GFN_COUNTRY: CH

This can be changed in the workflow if needed.

## Changing the language

The default is:

    GFN_LANGUAGE: en_US

This is independent of the country.

## Important behavior

A game that temporarily disappears from the API is NOT treated as
having lost all of its stores.

Likewise, a game for which NVIDIA temporarily returns an empty
variants list is not used to overwrite the previous state.

This prevents a temporary/incomplete API response from generating
false notifications.

Only newly observed stores generate emails.

## Instant Gaming price tracker

The Instant Gaming automation fetches the current price of each
configured product, appends a new snapshot to the history file, and
publishes a static page from:

    site/index.html

The tracked products are configured in:

    instant_gaming_products.json

Each entry needs:

    id
    label
    url

The URL should be the full Instant Gaming product page URL. The
tracker adds the configured currency automatically before fetching
the page.

The generated history file is stored in:

    site/data/instant-gaming-price-history.json

The repository also builds a second static catalog page showing the
overlap between the GeForce NOW catalog and Xbox PC Game Pass, along
with cached HowLongToBeat main-story durations and OpenCritic scores
when matches are available. It also derives a short-game score that
blends 70% OpenCritic score with 30% main-story length fit, giving full
credit to games at 15 hours or under and tapering longer games down
toward 40 hours. The page can switch between the overlap and the Xbox PC
Game Pass titles that are still missing from GeForce NOW. The
generated dataset is stored in:

    site/data/gfn-xbox-pc-game-pass-catalog.json

The generator also stores a HowLongToBeat lookup cache so scheduled
GitHub Actions runs can refresh playtime data without starting from
scratch every time:

    site/data/howlongtobeat-cache.json

and an OpenCritic lookup cache for the review metadata:

    site/data/opencritic-cache.json

and the page itself lives at:

    site/game-pass-catalog.html

The workflow that updates the history and deploys the static page is:

    .github/workflows/instant-gaming-price-monitor.yml

After the workflow has run successfully, the page can be published
with GitHub Pages using the GitHub Actions source. The existing
Instant Gaming tracker remains at the site root and links to the
GFN x PC Game Pass catalog page.