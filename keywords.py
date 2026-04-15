"""
Shared accident keyword detection and link filtering.
Single source of truth — used by all crawlers.
"""

import re

# ── Hindi accident keywords (substring match is safe for Devanagari) ─
HINDI_KEYWORDS = [
    "दुर्घटना", "हादसा", "टक्कर", "कुचल", "पलट", "मौत", "घायल",
    "एक्सीडेंट", "ट्रक", "बाइक", "ऑटो", "हाइवे",
    "सड़क हादसा", "सड़क दुर्घटना", "हादसे",
    "मृत", "ओवरटर्न", "चपेट", "कार",
]

# ── English word-boundary regex for URL matching ─────────────────
ENG_URL_PATTERN = re.compile(
    r"\b(?:accident|crash|collision|dead|killed|injured|overturned|"
    r"hadsa|takkar|palti|truck|bike|bus|highway|fatal)\b", re.I,
)

# ── English word-boundary regex for headline matching ────────────
ENG_HEADLINE_PATTERN = re.compile(
    r"\b(?:accident|crash|collision|dead|killed|injured|overturned|hit|"
    r"truck|bus|car|bike|auto|highway|fatal|dies|death)\b", re.I,
)


def is_accident_headline(headline: str) -> bool:
    """Check if a headline contains accident-related keywords."""
    if any(k in headline for k in HINDI_KEYWORDS):
        return True
    if ENG_HEADLINE_PATTERN.search(headline):
        return True
    return False


def is_accident_url(url: str) -> bool:
    """Check if a URL slug contains accident-related terms."""
    return bool(ENG_URL_PATTERN.search(url))


def is_accident_content(headline: str, url: str) -> bool:
    """Combined check: headline OR url suggests accident."""
    return is_accident_headline(headline) or is_accident_url(url)
