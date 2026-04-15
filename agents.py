"""
Multi-Agent Async Crawler
==========================
Agent-based architecture where independent agents crawl different states
concurrently. Each agent handles one state, with sub-tasks for districts.

Agents:
  - DiscoveryAgent:  Finds accident article links from Bhaskar pages
  - ExtractionAgent: Scrapes articles and runs Ollama LLM extraction
  - StorageAgent:    Persists results to SQLite + Excel
  - OrchestratorAgent: Coordinates all agents, manages concurrency

Uses asyncio + aiohttp for non-blocking I/O, with semaphores for rate limiting.
"""

import asyncio
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import aiohttp
import pandas as pd
from bs4 import BeautifulSoup
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import (
    OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT,
    GROQ_API_KEY, GROQ_MODEL, LLM_PROVIDER,
    OUTPUT_DIR, MAX_CONCURRENT_STATES, MAX_CONCURRENT_DISTRICTS,
    REQUEST_DELAY, MAX_ARTICLES_PER_DISTRICT,
)
from database import AccidentDB
from keywords import is_accident_content, HINDI_KEYWORDS, ENG_URL_PATTERN, ENG_HEADLINE_PATTERN
from processor import validate_record
from prompts import SYSTEM_PROMPT

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


def _detect_llm_provider() -> str:
    """Detect which LLM provider to use: ollama or groq."""
    if LLM_PROVIDER and LLM_PROVIDER != "auto":
        return LLM_PROVIDER
    try:
        import requests
        r = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if r.status_code == 200:
            return "ollama"
    except Exception:
        pass
    if GROQ_API_KEY:
        return "groq"
    return "ollama"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
}

STATES_FILE = Path(__file__).parent / "states_districts_full.json"


def load_states() -> dict:
    with open(STATES_FILE, encoding="utf-8") as f:
        return json.load(f)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DISCOVERY AGENT — finds accident links from listing pages
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class DiscoveryAgent:
    """Asynchronously discovers accident article links from Bhaskar pages."""

    def __init__(self, session: aiohttp.ClientSession, rate_limiter: asyncio.Semaphore):
        self.session = session
        self.rate_limiter = rate_limiter

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
    )
    async def fetch_page(self, url: str) -> str | None:
        async with self.rate_limiter:
            try:
                async with self.session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        logger.warning(f"HTTP {resp.status} for {url}")
                        return None
                    return await resp.text()
            except Exception as e:
                logger.warning(f"Fetch failed: {url} — {e}")
                raise

    def find_accident_links(self, html: str, state_slug: str) -> list[dict]:
        """Parse HTML for accident-related article links."""
        soup = BeautifulSoup(html, "html.parser")
        found, seen = [], set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/"):
                href = "https://www.bhaskar.com" + href

            # Must be from this state
            if f"/local/{state_slug}/" not in href and f"/g/local/{state_slug}/" not in href:
                continue
            if "/news/" not in href:
                continue
            if href in seen:
                continue

            headline = a.get_text(strip=True)
            if not headline or len(headline) < 10:
                continue

            if is_accident_content(headline, href):
                seen.add(href)
                found.append({"url": href, "headline": headline})

        return found

    async def discover_state(self, state_slug: str, state_info: dict) -> list[dict]:
        """Discover all accident links for a state (main page + district pages)."""
        all_links = []
        seen_urls = set()

        # Main state page
        html = await self.fetch_page(state_info["url"])
        if html:
            links = self.find_accident_links(html, state_slug)
            for lnk in links:
                if lnk["url"] not in seen_urls:
                    seen_urls.add(lnk["url"])
                    # Infer district from URL
                    m = re.search(rf"/(?:g/)?local/{re.escape(state_slug)}/([^/]+)/", lnk["url"])
                    lnk["district_slug"] = m.group(1) if m else None
                    all_links.append(lnk)

        # District pages
        for dist_slug, dist_info in state_info.get("districts", {}).items():
            await asyncio.sleep(REQUEST_DELAY)
            html = await self.fetch_page(dist_info["url"])
            if not html:
                continue
            links = self.find_accident_links(html, state_slug)
            for lnk in links:
                if lnk["url"] not in seen_urls:
                    seen_urls.add(lnk["url"])
                    lnk["district_slug"] = dist_slug
                    all_links.append(lnk)

        logger.info(f"DiscoveryAgent: {state_info['name']} → {len(all_links)} links")
        return all_links


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  EXTRACTION AGENT — scrapes articles and runs Ollama
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class ExtractionAgent:
    """Scrapes article content and extracts accident data via LLM (Ollama or Groq)."""

    def __init__(self, session: aiohttp.ClientSession, rate_limiter: asyncio.Semaphore,
                 llm_provider: str = "auto"):
        self.session = session
        self.rate_limiter = rate_limiter
        self.llm_provider = llm_provider if llm_provider != "auto" else _detect_llm_provider()
        self.ollama_url = f"{OLLAMA_BASE_URL}/api/chat"
        logger.info(f"ExtractionAgent using LLM provider: {self.llm_provider}")

    async def scrape_article(self, url: str) -> str:
        async with self.rate_limiter:
            try:
                async with self.session.get(url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        return ""
                    html = await resp.text()
            except Exception as e:
                logger.warning(f"Scrape failed: {url} — {e}")
                return ""

        soup = BeautifulSoup(html, "html.parser")
        for t in soup(["script", "style", "nav", "footer", "header", "aside", "form"]):
            t.decompose()
        art = (
            soup.find("article")
            or soup.find("div", class_="article-body")
            or soup.find("div", class_="story-content")
            or soup.find("div", class_="post-content")
            or soup.find("div", {"id": "article-body"})
        )
        text = art.get_text("\n", strip=True) if art else soup.get_text("\n", strip=True)
        return "\n".join(l.strip() for l in text.splitlines() if l.strip())

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=2, min=3, max=15),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
    )
    async def call_llm(self, text: str) -> list[dict]:
        """Send text to LLM (Ollama or Groq) and parse JSON."""
        if self.llm_provider == "groq":
            return await self._call_groq(text)
        return await self._call_ollama(text)

    async def _call_ollama(self, text: str) -> list[dict]:
        payload = {
            "model": OLLAMA_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 4096},
        }
        try:
            async with self.session.post(
                self.ollama_url, json=payload,
                timeout=aiohttp.ClientTimeout(total=OLLAMA_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    logger.error(f"Ollama HTTP {resp.status}")
                    return []
                data = await resp.json()
                raw = data.get("message", {}).get("content", "")
                return self._parse_json(raw)
        except Exception as e:
            logger.error(f"Ollama call failed: {e}")
            raise

    async def _call_groq(self, text: str) -> list[dict]:
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
        }
        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        try:
            async with self.session.post(
                GROQ_API_URL, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=OLLAMA_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.error(f"Groq HTTP {resp.status}: {body[:200]}")
                    return []
                data = await resp.json()
                raw = data["choices"][0]["message"]["content"]
                return self._parse_json(raw)
        except Exception as e:
            logger.error(f"Groq call failed: {e}")
            raise

    def _parse_json(self, text: str) -> list[dict]:
        text = text.strip()
        if text.startswith("```"):
            lines = [l for l in text.splitlines() if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()
        try:
            data = json.loads(text)
            return data if isinstance(data, list) else [data] if isinstance(data, dict) else []
        except json.JSONDecodeError:
            pass
        start, end = text.find("["), text.rfind("]")
        if start != -1 and end > start:
            try:
                data = json.loads(text[start:end + 1])
                return data if isinstance(data, list) else []
            except json.JSONDecodeError:
                pass
        return []

    async def extract_article(self, link: dict, state_name: str, district_name: str) -> list[dict]:
        """Full pipeline: scrape → Ollama → validate."""
        url = link["url"]
        headline = link["headline"]

        text = await self.scrape_article(url)
        if not text:
            return []

        try:
            raw_records = await self.call_llm(text)
        except Exception:
            return []

        results = []
        for rec in raw_records:
            if not rec.get("accident", False):
                continue
            rec = validate_record(rec)
            rec["state"] = rec.get("state") or state_name
            rec["district"] = rec.get("district") or district_name
            rec["source_url"] = url
            rec["headline"] = headline
            rec["crawl_date"] = datetime.now().strftime("%Y-%m-%d")
            rec["crawl_timestamp"] = datetime.now().isoformat()
            results.append(rec)

        if results:
            logger.info(f"  ✓ {len(results)} records — {headline[:60]}")
        return results


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  STORAGE AGENT — persists to DB + Excel
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class StorageAgent:
    """Handles all persistence: SQLite database + per-state Excel files."""

    def __init__(self, db: AccidentDB, output_dir: str = OUTPUT_DIR):
        self.db = db
        self.output_dir = output_dir

    async def store_records(self, records: list[dict]) -> tuple[int, int]:
        return await self.db.insert_records(records)

    def save_state_excel(self, state_slug: str, state_name: str,
                         district_records: dict[str, list[dict]]) -> str | None:
        """Save one Excel per state with district sheets."""
        all_recs = [r for recs in district_records.values() for r in recs]
        if not all_recs:
            return None

        os.makedirs(self.output_dir, exist_ok=True)
        fname = f"{state_slug}_accidents_{datetime.now().strftime('%Y%m%d')}.xlsx"
        fpath = os.path.join(self.output_dir, fname)

        col_order = [
            "district", "headline", "source_url",
            "location", "city", "police_station",
            "vehicle_type", "vehicle_number", "persons",
            "fatalities", "injuries",
            "date", "time", "language_detected", "confidence_score",
            "raw_text", "crawl_timestamp",
        ]

        # Flatten lists for Excel
        for r in all_recs:
            for f in ("vehicle_type", "vehicle_number", "persons"):
                if isinstance(r.get(f), list):
                    r[f] = ", ".join(str(v) for v in r[f])

        all_df = pd.DataFrame(all_recs)
        cols = [c for c in col_order if c in all_df.columns]
        extra = [c for c in all_df.columns if c not in cols]
        all_df = all_df[cols + extra]

        with pd.ExcelWriter(fpath, engine="openpyxl") as writer:
            # Summary sheet
            summary_data = {
                "Metric": ["State", "Total Accidents", "Total Fatalities",
                           "Total Injuries", "Districts", "Crawl Date"],
                "Value": [
                    state_name, len(all_df),
                    int(all_df["fatalities"].sum()) if "fatalities" in all_df.columns else 0,
                    int(all_df["injuries"].sum()) if "injuries" in all_df.columns else 0,
                    len(district_records),
                    datetime.now().strftime("%Y-%m-%d %H:%M"),
                ],
            }
            pd.DataFrame(summary_data).to_excel(writer, sheet_name="Summary", index=False)

            # District overview
            dist_rows = []
            for dn, recs in sorted(district_records.items()):
                df = pd.DataFrame(recs)
                dist_rows.append({
                    "District": dn,
                    "Accidents": len(recs),
                    "Fatalities": int(df["fatalities"].sum()) if "fatalities" in df.columns else 0,
                    "Injuries": int(df["injuries"].sum()) if "injuries" in df.columns else 0,
                })
            pd.DataFrame(dist_rows).to_excel(writer, sheet_name="District Overview", index=False)

            # All data
            all_df.to_excel(writer, sheet_name="All Data", index=False)

            # Per-district sheets
            for dn, recs in sorted(district_records.items()):
                sheet = re.sub(r'[\\/*?\[\]:]', '', dn)[:31]
                df = pd.DataFrame(recs)
                df.to_excel(writer, sheet_name=sheet, index=False)

            # Links
            if "source_url" in all_df.columns:
                links = all_df[["district", "headline", "source_url"]].drop_duplicates()
                links.to_excel(writer, sheet_name="Article Links", index=False)

        logger.success(f"Excel saved: {fpath} ({len(all_df)} records)")
        return fpath


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  ORCHESTRATOR AGENT — coordinates everything
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class OrchestratorAgent:
    """
    Master agent that coordinates:
      1. DiscoveryAgent — find links (concurrent across states)
      2. ExtractionAgent — scrape + LLM (controlled concurrency)
      3. StorageAgent — persist results
    """

    def __init__(self, states: dict | None = None, max_articles: int = MAX_ARTICLES_PER_DISTRICT):
        self.all_states = states or load_states()
        self.max_articles = max_articles
        self.db = AccidentDB()
        self.stats = {
            "states": 0, "districts": 0, "articles": 0,
            "new": 0, "dup": 0, "errors": [],
        }

    async def run(self, state_slugs: list[str] | None = None):
        """Run the full crawl pipeline."""
        await self.db.init()
        run_id = await self.db.start_run()

        states = (
            {k: v for k, v in self.all_states.items() if k in state_slugs}
            if state_slugs else self.all_states
        )

        logger.info(f"🚀 Orchestrator starting: {len(states)} states, model={OLLAMA_MODEL}")
        start = time.time()

        # Rate limiters
        http_limiter = asyncio.Semaphore(MAX_CONCURRENT_DISTRICTS)
        state_limiter = asyncio.Semaphore(MAX_CONCURRENT_STATES)

        async with aiohttp.ClientSession() as session:
            discovery = DiscoveryAgent(session, http_limiter)
            extraction = ExtractionAgent(session, http_limiter)
            storage = StorageAgent(self.db)

            tasks = []
            for slug, info in states.items():
                tasks.append(
                    self._process_state(slug, info, discovery, extraction, storage, state_limiter)
                )

            await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = time.time() - start
        self.stats["status"] = "completed"
        await self.db.finish_run(run_id, self.stats)

        logger.success(
            f"✅ Crawl complete in {elapsed:.0f}s — "
            f"{self.stats['new']} new, {self.stats['dup']} duplicates"
        )
        return self.stats

    async def _process_state(self, slug, info, discovery, extraction, storage, limiter):
        """Process a single state (discovery → extraction → storage)."""
        async with limiter:
            state_name = info["name"]
            logger.info(f"🏛  Processing: {state_name} ({slug})")

            try:
                # Phase 1: Discovery
                links = await discovery.discover_state(slug, info)
                self.stats["articles"] += len(links)

                # Limit per district
                district_links = {}
                districts = info.get("districts", {})
                slug_to_name = {s: d["name"] for s, d in districts.items()}

                for lnk in links:
                    ds = lnk.get("district_slug")
                    dn = slug_to_name.get(ds, ds.replace("-", " ").title() if ds else state_name)
                    district_links.setdefault(dn, []).append(lnk)

                # Phase 2: Extraction (with limit per district)
                district_records = {}
                for dn, d_links in district_links.items():
                    for lnk in d_links[:self.max_articles]:
                        await asyncio.sleep(REQUEST_DELAY)
                        recs = await extraction.extract_article(lnk, state_name, dn)
                        if recs:
                            district_records.setdefault(dn, []).extend(recs)

                # Phase 3: Storage
                all_recs = [r for recs in district_records.values() for r in recs]
                if all_recs:
                    new, dup = await storage.store_records(all_recs)
                    self.stats["new"] += new
                    self.stats["dup"] += dup
                    storage.save_state_excel(slug, state_name, district_records)

                self.stats["states"] += 1
                self.stats["districts"] += len(district_records)
                logger.success(f"  ✅ {state_name}: {len(all_recs)} records, {len(district_records)} districts")

            except Exception as e:
                logger.error(f"  ❌ {state_name}: {e}")
                self.stats["errors"].append(f"{slug}: {e}")


# ── CLI entry point ─────────────────────────────────────────────

async def async_main():
    import argparse
    parser = argparse.ArgumentParser(description="Multi-Agent Async Crawler")
    parser.add_argument("--states", "-s", default=None, help="Comma-separated state slugs")
    parser.add_argument("--max-articles", "-m", type=int, default=MAX_ARTICLES_PER_DISTRICT)
    parser.add_argument("--list", action="store_true", help="List states and exit")
    args = parser.parse_args()

    if args.list:
        states = load_states()
        for slug, info in sorted(states.items()):
            nd = len(info.get("districts", {}))
            print(f"  {slug:<23} {info['name']:<25} {nd:>3} districts")
        return

    slugs = [s.strip() for s in args.states.split(",")] if args.states else None
    orch = OrchestratorAgent(max_articles=args.max_articles)
    stats = await orch.run(slugs)
    print(json.dumps(stats, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(async_main())
