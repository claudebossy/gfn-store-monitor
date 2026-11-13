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

Each HBO run also appends its result to:

    site/data/hbo-max-history.json

There is also a dedicated GitHub Actions workflow for it:

    Actions
    -> Monitor HBO Max Availability
    -> Run workflow

## Schedule

The HBO Max, GeForce NOW, and GitHub Pages data workflows all run every
six hours on staggered minutes so their history commits do not collide.

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

Each GeForce NOW run also appends a compact change log for GitHub Pages
to:

    site/data/gfn-catalog-history.json

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

### Adding more products

Edit `instant_gaming_products.json` directly on GitHub using the pencil
button and append an entry to the `products` array, for example:

```json
{
  "id": "007-first-light-pc-steam",
  "label": "007 First Light - PC (Steam)",
  "url": "https://www.instant-gaming.com/fr/22735-acheter-007-first-light-pc-steam/"
}
```

Use a unique, stable `id` for each product or edition: it links future
snapshots to the existing price history. The `label` is the name shown
on the chart. Separate entries with commas, but do not add a trailing
comma after the last entry.

Commit the change to `main`, then either wait for the next scheduled
run (every six hours) or start **Actions -> Update GitHub Pages Data ->
Run workflow**. New products appear on the page after the data update
and subsequent GitHub Pages deployment; no Python or HTML changes are
needed.

The generated history file is stored in:

    site/data/instant-gaming-price-history.json

### Exploring prices

Use the product search to match game names, editions, or stores. Search
ignores case, accents, and punctuation, and matches all entered words in
any order. Filter to in-stock products, recorded lows, or recent price
drops, and sort products and price cards by name, price, discount, or
price change.

Filters only change the product list, not the games already compared.
Use **Select matches** or **Clear matches** to change the comparison for
the current results, **Clear all** to start fresh, or **Show only** to
focus on one game. **Compared products only** helps manage a large
selection. Colors stay consistent when changing the comparison.

The chart offers 7-, 30-, and 90-day views ending at the latest selected
sample. Drag over a time interval to zoom, Ctrl + scroll or pinch to
zoom, and Shift + drag to pan. Zoom and Earlier/Later buttons also work
with keyboard or touch. **Reset zoom** restores all history for the
compared products. For large comparisons, the chart legend is hidden;
use the product checkboxes to manage the lines.

Price cards show the lowest recorded in-stock price across all history
and the change from the previous sample. Recorded lows are sampled
prices, not a guarantee of the store's all-time low.

The **Price insights** section highlights in-stock products at their
recorded low and products whose latest price dropped since the previous
sample. It always includes all tracked products, independent of filters
and comparisons. Lows are ordered by current price; drops by the size
of the price decrease. **View history** focuses the chart on that game
and restores its full history.

UI regression checks use Node.js 18 or newer and its built-in test
runner: `node --test`.

The repository also builds a second static catalog page showing the
overlap between the GeForce NOW catalog and Xbox Game Pass Ultimate, along
with cached HowLongToBeat main-story durations and OpenCritic scores
when matches are available. It also derives a short-game score that
blends 70% OpenCritic score with 30% main-story length fit, giving full
credit to games at 15 hours or under and tapering longer games down
toward 40 hours. The page can switch between the overlap and the Xbox PC
Game Pass titles that are still missing from GeForce NOW. The
generated dataset is stored in:

    site/data/gfn-xbox-game-pass-ultimate-catalog.json

The generator also stores a HowLongToBeat lookup cache so scheduled
GitHub Actions runs can refresh playtime data without starting from
scratch every time:

    site/data/howlongtobeat-cache.json

and an OpenCritic lookup cache for the review metadata:

    site/data/opencritic-cache.json

and the page itself lives at:

    site/game-pass-catalog.html

The workflow that updates the Instant Gaming and catalog data is:

    .github/workflows/instant-gaming-price-monitor.yml

The repository now also includes a monitor history page at:

    site/monitor-history.html

It shows the HBO Max check history and the GeForce NOW catalog change
history.

GitHub Pages is deployed by:

    .github/workflows/deploy-github-pages.yml

That deployment runs after the data-update workflows complete
successfully. The existing Instant Gaming tracker remains at the site
root and links to both the GFN x Xbox Game Pass Ultimate catalog page and the
monitor history page.