"""
Core processor: combines scraping + LLM extraction + validation.
"""

import re
from ollama_client import OllamaClient
from scraper import scrape_news
from config import MAX_RAW_TEXT_LENGTH, DEFAULT_SOURCE


# Valid vehicle types for normalization
VALID_VEHICLE_TYPES = {"Car", "Truck", "Bus", "Bike", "Tractor", "Auto", "Tempo"}

# Indian vehicle number pattern: XX00XX0000 (with optional spaces/hyphens)
VEHICLE_NUM_RE = re.compile(
    r"\b[A-Z]{2}\s*\d{1,2}\s*[A-Z]{0,3}\s*\d{1,4}\b", re.IGNORECASE
)


def validate_record(record: dict) -> dict:
    """Sanitize and validate a single accident record."""

    # Ensure required fields exist with correct types
    record.setdefault("accident", True)
    record.setdefault("location", None)
    record.setdefault("city", None)
    record.setdefault("district", None)
    record.setdefault("state", None)
    record.setdefault("police_station", None)
    record.setdefault("vehicle_number", [])
    record.setdefault("vehicle_type", [])
    record.setdefault("persons", [])
    record.setdefault("fatalities", 0)
    record.setdefault("injuries", 0)
    record.setdefault("date", None)
    record.setdefault("time", None)
    record.setdefault("language_detected", None)
    record.setdefault("source", DEFAULT_SOURCE)
    record.setdefault("raw_text", None)
    record.setdefault("confidence_score", 0.0)

    # ── Type coercion ───────────────────────────────────────────
    if not isinstance(record["vehicle_number"], list):
        record["vehicle_number"] = []
    if not isinstance(record["vehicle_type"], list):
        record["vehicle_type"] = []
    if not isinstance(record["persons"], list):
        record["persons"] = []

    record["fatalities"] = _to_int(record["fatalities"])
    record["injuries"] = _to_int(record["injuries"])
    record["confidence_score"] = _to_float(record["confidence_score"])

    # ── Validate vehicle numbers against Indian format ──────────
    record["vehicle_number"] = [
        v for v in record["vehicle_number"]
        if VEHICLE_NUM_RE.fullmatch(v.replace(" ", "").replace("-", ""))
           or VEHICLE_NUM_RE.search(v)
    ]

    # ── Normalize vehicle types ─────────────────────────────────
    normalized = []
    for vt in record["vehicle_type"]:
        vt_title = vt.strip().title()
        if vt_title in VALID_VEHICLE_TYPES:
            normalized.append(vt_title)
        else:
            # Try fuzzy match
            match = _fuzzy_vehicle(vt)
            if match:
                normalized.append(match)
    record["vehicle_type"] = normalized

    # ── Truncate raw_text ───────────────────────────────────────
    if record["raw_text"] and len(record["raw_text"]) > MAX_RAW_TEXT_LENGTH:
        record["raw_text"] = record["raw_text"][:MAX_RAW_TEXT_LENGTH] + "..."

    return record


def process_url(url: str, client: OllamaClient | None = None) -> list[dict]:
    """Scrape a URL and extract accident records."""
    client = client or OllamaClient()
    text = scrape_news(url)
    if not text.strip():
        return []
    return process_text(text, client=client, source=url)


def process_text(text: str, client: OllamaClient | None = None, source: str = "news") -> list[dict]:
    """Extract accident records from raw text."""
    client = client or OllamaClient()
    records = client.extract_json(text)

    validated = []
    for rec in records:
        if not rec.get("accident", False):
            continue
        rec["source"] = source
        validated.append(validate_record(rec))

    return validated


# ── helpers ─────────────────────────────────────────────────────

_VEHICLE_ALIASES = {
    "motorcycle": "Bike",
    "motorbike": "Bike",
    "two-wheeler": "Bike",
    "two wheeler": "Bike",
    "scooty": "Bike",
    "scooter": "Bike",
    "lorry": "Truck",
    "trailer": "Truck",
    "tanker": "Truck",
    "mini bus": "Bus",
    "minibus": "Bus",
    "school bus": "Bus",
    "autorickshaw": "Auto",
    "auto rickshaw": "Auto",
    "auto-rickshaw": "Auto",
    "three wheeler": "Auto",
    "three-wheeler": "Auto",
    "suv": "Car",
    "sedan": "Car",
    "hatchback": "Car",
    "jeep": "Car",
    "van": "Car",
    "pickup": "Truck",
}


def _fuzzy_vehicle(raw: str) -> str | None:
    key = raw.strip().lower()
    return _VEHICLE_ALIASES.get(key)


def _to_int(val) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return 0


def _to_float(val) -> float:
    try:
        return round(float(val), 2)
    except (TypeError, ValueError):
        return 0.0
