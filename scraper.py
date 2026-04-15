"""
Web scraper to fetch news content from URLs.
"""

import requests
from bs4 import BeautifulSoup


def scrape_news(url: str, timeout: int = 15) -> str:
    """Fetch a news URL and return the main text content."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding  # handle Hindi/regional encodings

    soup = BeautifulSoup(resp.text, "html.parser")

    # Remove non-content tags
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
        tag.decompose()

    # Try common article containers first
    article = (
        soup.find("article")
        or soup.find("div", class_="article-body")
        or soup.find("div", class_="story-content")
        or soup.find("div", class_="post-content")
        or soup.find("div", {"id": "article-body"})
    )

    text = article.get_text(separator="\n", strip=True) if article else soup.get_text(separator="\n", strip=True)

    # Collapse blank lines
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)
