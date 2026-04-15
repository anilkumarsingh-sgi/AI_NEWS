"""
Configuration for AI News Accident Extractor.
Supports .env file overrides, environment variables, and Streamlit Cloud secrets.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).parent
load_dotenv(_PROJECT_ROOT / ".env")


def _get(key: str, default: str = "") -> str:
    """Read from env var first, then Streamlit secrets, then default."""
    val = os.getenv(key)
    if val:
        return val
    try:
        import streamlit as st
        if key in st.secrets:
            return str(st.secrets[key])
    except Exception:
        pass
    return default

# ── Ollama Settings ──────────────────────────────────────────────
OLLAMA_BASE_URL = _get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = _get("OLLAMA_MODEL", "mistral:7b")
OLLAMA_TIMEOUT = int(_get("OLLAMA_TIMEOUT", "120"))

# ── Groq Cloud LLM (fallback when Ollama is unreachable) ────────
GROQ_API_KEY = _get("GROQ_API_KEY", "")
GROQ_MODEL = _get("GROQ_MODEL", "llama-3.1-8b-instant")
LLM_PROVIDER = _get("LLM_PROVIDER", "auto")  # "ollama", "groq", or "auto"

# ── Extraction Settings ─────────────────────────────────────────
MAX_RAW_TEXT_LENGTH = int(_get("MAX_RAW_TEXT_LENGTH", "500"))
DEFAULT_SOURCE = "news"

# ── Output Settings ─────────────────────────────────────────────
OUTPUT_DIR = _get("OUTPUT_DIR", "output")
PRETTY_JSON = True

# ── Database ─────────────────────────────────────────────────────
DB_PATH = _get("DB_PATH", str(_PROJECT_ROOT / "accidents.db"))

# ── Scheduler ────────────────────────────────────────────────────
SCHEDULE_HOUR = int(_get("SCHEDULE_HOUR", "6"))       # 6 AM daily
SCHEDULE_MINUTE = int(_get("SCHEDULE_MINUTE", "0"))
MAX_ARTICLES_PER_DISTRICT = int(_get("MAX_ARTICLES_PER_DISTRICT", "5"))

# ── MCP Server ───────────────────────────────────────────────────
MCP_HOST = _get("MCP_HOST", "127.0.0.1")
MCP_PORT = int(_get("MCP_PORT", "8100"))

# ── Concurrency ──────────────────────────────────────────────────
MAX_CONCURRENT_STATES = int(_get("MAX_CONCURRENT_STATES", "3"))
MAX_CONCURRENT_DISTRICTS = int(_get("MAX_CONCURRENT_DISTRICTS", "5"))
REQUEST_DELAY = float(_get("REQUEST_DELAY", "0.5"))
