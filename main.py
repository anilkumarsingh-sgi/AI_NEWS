"""
AI News — Motor Insurance Accident Data Extractor
===================================================
CLI entry point. Supports URL scraping, raw text input, and batch file processing.

Usage:
  python main.py --url "https://example.com/news-article"
  python main.py --text "ट्रक और कार की टक्कर में 2 लोगों की मौत..."
  python main.py --file input.txt
  python main.py --batch urls.txt
  python main.py --batch urls.txt --output results.json
"""

import argparse
import json
import os
import sys

from config import OUTPUT_DIR, PRETTY_JSON
from ollama_client import OllamaClient
from processor import process_url, process_text


def main():
    parser = argparse.ArgumentParser(
        description="Extract road accident data from Indian news using Ollama LLM",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--url", help="Single news URL to scrape and process")
    group.add_argument("--text", help="Raw news text to process directly")
    group.add_argument("--file", help="Path to a text file containing news content")
    group.add_argument("--batch", help="Path to a file with one URL per line")

    parser.add_argument("--model", default=None, help="Ollama model name (default: from config)")
    parser.add_argument("--output", "-o", default=None, help="Save JSON output to this file")
    parser.add_argument("--pretty", action="store_true", default=PRETTY_JSON, help="Pretty-print JSON")

    args = parser.parse_args()

    # ── Init client ─────────────────────────────────────────────
    client = OllamaClient(model=args.model) if args.model else OllamaClient()

    if not client.is_available():
        print("ERROR: Ollama is not running. Start it with `ollama serve`", file=sys.stderr)
        sys.exit(1)

    # ── Process input ───────────────────────────────────────────
    all_records: list[dict] = []

    if args.url:
        print(f"Processing URL: {args.url}", file=sys.stderr)
        all_records = process_url(args.url, client=client)

    elif args.text:
        print("Processing inline text...", file=sys.stderr)
        all_records = process_text(args.text, client=client)

    elif args.file:
        print(f"Processing file: {args.file}", file=sys.stderr)
        with open(args.file, encoding="utf-8") as f:
            content = f.read()
        all_records = process_text(content, client=client, source=args.file)

    elif args.batch:
        print(f"Batch processing: {args.batch}", file=sys.stderr)
        with open(args.batch, encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        for i, url in enumerate(urls, 1):
            print(f"  [{i}/{len(urls)}] {url}", file=sys.stderr)
            try:
                records = process_url(url, client=client)
                all_records.extend(records)
            except Exception as e:
                print(f"    FAILED: {e}", file=sys.stderr)

    # ── Output ──────────────────────────────────────────────────
    indent = 2 if args.pretty else None
    json_output = json.dumps(all_records, indent=indent, ensure_ascii=False)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(json_output)
        print(f"Saved {len(all_records)} record(s) → {args.output}", file=sys.stderr)
    else:
        print(json_output)

    print(f"\nTotal accidents extracted: {len(all_records)}", file=sys.stderr)


if __name__ == "__main__":
    main()
