import json

with open("gfn_catalog_cache.json", "r", encoding="utf-8") as f:
    catalog = json.load(f)
    for game in catalog:
        if "XBOX" not in [x["appStore"] for x in game["variants"]]:
            print(game['title'])