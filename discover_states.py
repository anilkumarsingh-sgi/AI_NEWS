"""
Discover all states and districts from Dainik Bhaskar.
Scrapes each state page to find district links.
Outputs updated states_districts.json.
"""
import json
import re
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}

# Known Bhaskar state slugs (from nav menu + sitemap)
KNOWN_STATES = {
    "rajasthan": "राजस्थान",
    "mp": "मध्य प्रदेश",
    "uttar-pradesh": "उत्तर प्रदेश",
    "bihar": "बिहार",
    "chhattisgarh": "छत्तीसगढ़",
    "jharkhand": "झारखंड",
    "haryana": "हरियाणा",
    "punjab": "पंजाब",
    "himachal": "हिमाचल प्रदेश",
    "uttarakhand": "उत्तराखंड",
    "gujarat": "गुजरात",
    "maharashtra": "महाराष्ट्र",
    "chandigarh": "चंडीगढ़",
    "jammu-kashmir": "जम्मू-कश्मीर",
}


def fetch(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        r.encoding = r.apparent_encoding
        return r.text
    except Exception as e:
        print(f"  FAIL: {url} -> {e}")
        return None


def discover_districts(state_slug, state_name):
    """Scrape a state page to find all district links."""
    url = f"https://www.bhaskar.com/local/{state_slug}/"
    html = fetch(url)
    if not html:
        return {}

    soup = BeautifulSoup(html, "html.parser")
    districts = {}

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.startswith("/"):
            href = "https://www.bhaskar.com" + href

        # Match district page links: /local/{state}/{district}/
        # But NOT news articles
        pattern = rf"https://www\.bhaskar\.com/local/{re.escape(state_slug)}/([a-z0-9\-]+)/?$"
        m = re.match(pattern, href)
        if not m:
            continue

        dist_slug = m.group(1)
        # Skip non-district slugs
        if dist_slug in ("news", "videos", "photos", "web-stories"):
            continue

        dist_name = a.get_text(strip=True)
        # Clean up "XYZ News" -> "XYZ"
        dist_name = re.sub(r"\s*News\s*$", "", dist_name, flags=re.IGNORECASE).strip()
        if not dist_name or len(dist_name) < 2:
            dist_name = dist_slug.replace("-", " ").title()

        if dist_slug not in districts:
            districts[dist_slug] = {
                "name": dist_name,
                "url": f"https://www.bhaskar.com/local/{state_slug}/{dist_slug}/",
            }

    return districts


def main():
    all_data = {}

    for slug, hindi_name in KNOWN_STATES.items():
        print(f"\n{'='*50}")
        print(f"State: {hindi_name} ({slug})")
        print(f"{'='*50}")

        districts = discover_districts(slug, hindi_name)
        print(f"  Found {len(districts)} district(s)")

        if districts:
            for ds, di in sorted(districts.items()):
                print(f"    {ds}: {di['name']}")

        all_data[slug] = {
            "name": hindi_name,
            "url": f"https://www.bhaskar.com/local/{slug}/",
            "districts": districts,
        }

    # Save
    out = "states_districts_full.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_data, f, indent=2, ensure_ascii=False)

    total_d = sum(len(v["districts"]) for v in all_data.values())
    print(f"\n\nSaved {len(all_data)} states, {total_d} districts -> {out}")


if __name__ == "__main__":
    main()
