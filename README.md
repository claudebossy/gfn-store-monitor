# GeForce NOW Store Monitor

Monitors selected games on GeForce NOW and sends an email when a
new supported game store becomes available.

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

The previous store state is stored in:

    state.json

GitHub Actions commits state.json after each successful run.

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

## Schedule

The workflow normally runs every six hours.

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