import requests
from bs4 import BeautifulSoup
from urllib.parse import unquote

SITEMAP = "https://www.hbomax.com/ch/en/sitemap/shows"
SHOW = "hacks"

response = requests.get(
    SITEMAP,
    headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/139.0 Safari/537.36"
    },
    timeout=30
)
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")

matches = []

for a in soup.select("a[href]"):
    text = unquote(a.get_text(" ", strip=True)).casefold()
    url = unquote(a["href"]).casefold()

    if SHOW in text or SHOW in url:
        matches.append({
            "title": a.get_text(" ", strip=True),
            "url": a["href"]
        })

if matches:
    print(f"Found {len(matches)} match(es):")
    for match in matches:
        print(f"- {match['title']}: {match['url']}")
else:
    print("Hacks is not listed in the HBO Max Switzerland show sitemap.")