import os
import smtplib
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup
from site_history import (
    load_history_document,
    write_history_document,
)

SITEMAP = "https://www.hbomax.com/ch/en/sitemap/shows"
SHOW = "hacks"
REQUEST_TIMEOUT = 30
HISTORY_FILE = (
    Path("site")
    / "data"
    / "hbo-max-history.json"
)


def find_matches() -> list[dict[str, str]]:
    response = requests.get(
        SITEMAP,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/139.0 Safari/537.36"
            )
        },
        timeout=REQUEST_TIMEOUT,
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    matches = []

    for anchor in soup.select("a[href]"):
        title = anchor.get_text(" ", strip=True)
        url = anchor["href"]

        normalized_title = unquote(title).casefold()
        normalized_url = unquote(url).casefold()

        if SHOW in normalized_title or SHOW in normalized_url:
            matches.append(
                {
                    "title": title,
                    "url": url,
                }
            )

    return matches


def build_result_lines(
    matches: list[dict[str, str]],
) -> list[str]:
    if matches:
        lines = [
            f"Found {len(matches)} match(es) for {SHOW!r} "
            "in the HBO Max Switzerland show sitemap:",
            "",
        ]

        for match in matches:
            lines.append(
                f"- {match['title']}: {match['url']}"
            )

        return lines

    return [
        "Hacks is not listed in the HBO Max Switzerland "
        "show sitemap.",
    ]


def save_result_history(
    timestamp: str,
    matches: list[dict[str, str]],
) -> None:
    history = load_history_document(HISTORY_FILE)
    entries = history["entries"]
    entries.append(
        {
            "timestamp": timestamp,
            "available": bool(matches),
            "match_count": len(matches),
            "matches": [
                {
                    "title": match["title"],
                    "url": match["url"],
                }
                for match in matches
            ],
        }
    )
    write_history_document(
        HISTORY_FILE,
        {
            "generated_at": timestamp,
            "show": SHOW,
            "sitemap_url": SITEMAP,
            "entries": entries,
        },
    )


def send_email(
    subject: str,
    lines: list[str],
) -> None:
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(
        os.environ.get("SMTP_PORT", "587")
    )
    smtp_username = os.environ["SMTP_USERNAME"]
    smtp_password = os.environ["SMTP_PASSWORD"]

    email_from = os.environ["EMAIL_FROM"]
    email_to = os.environ["EMAIL_TO"]

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = email_from
    message["To"] = email_to
    message.set_content("\n".join(lines))

    print(
        f"Sending HBO result notification to {email_to}"
    )

    with smtplib.SMTP(
        smtp_host,
        smtp_port,
        timeout=30,
    ) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()

        try:
            smtp.login(
                smtp_username,
                smtp_password,
            )
        except smtplib.SMTPAuthenticationError as exc:
            raise RuntimeError(
                "SMTP authentication failed. "
                "If you are using Gmail, set SMTP_PASSWORD "
                "to a Gmail App Password instead of your "
                "normal account password."
            ) from exc

        smtp.send_message(message)


def main() -> None:
    timestamp = datetime.now(timezone.utc).replace(
        microsecond=0
    ).isoformat()
    matches = find_matches()
    result_lines = build_result_lines(matches)

    print("\n".join(result_lines))
    save_result_history(timestamp, matches)

    if not matches:
        print(
            "Hacks is unavailable; skipping email notification."
        )
        return

    subject = "HBO Max CH: Hacks found"

    send_email(subject, result_lines)


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
