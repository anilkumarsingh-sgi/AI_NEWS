"""
LLM client — supports Ollama (local) and Groq (cloud) with automatic fallback.
"""

import json
import requests
from config import (
    OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT,
    GROQ_API_KEY, GROQ_MODEL, LLM_PROVIDER,
)
from prompts import SYSTEM_PROMPT

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"


class OllamaClient:
    """Handles communication with Ollama (local) or Groq (cloud)."""

    def __init__(self, base_url=None, model=None, timeout=None, provider=None):
        self.base_url = (base_url or OLLAMA_BASE_URL).rstrip("/")
        self.model = model or OLLAMA_MODEL
        self.timeout = timeout or OLLAMA_TIMEOUT
        self._provider = provider  # None = auto-detect

    @property
    def provider(self) -> str:
        if self._provider and self._provider != "auto":
            return self._provider
        if LLM_PROVIDER and LLM_PROVIDER != "auto":
            return LLM_PROVIDER
        # Auto: prefer Ollama if reachable, else Groq
        if self._ollama_reachable():
            return "ollama"
        if GROQ_API_KEY:
            return "groq"
        return "ollama"  # will show offline

    def _ollama_reachable(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return resp.status_code == 200
        except Exception:
            return False

    # ── health check ────────────────────────────────────────────
    def is_available(self) -> bool:
        if self.provider == "groq":
            return bool(GROQ_API_KEY)
        return self._ollama_reachable()

    def list_models(self) -> list[str]:
        if self.provider == "groq":
            return [GROQ_MODEL, "llama-3.1-8b-instant", "llama-3.3-70b-versatile", "mixtral-8x7b-32768"]
        resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]

    def get_provider_name(self) -> str:
        return self.provider.title()

    # ── core generation ─────────────────────────────────────────
    def generate(self, user_prompt: str) -> str:
        if self.provider == "groq":
            return self._generate_groq(user_prompt)
        return self._generate_ollama(user_prompt)

    def _generate_ollama(self, user_prompt: str) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 4096},
        }
        resp = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    def _generate_groq(self, user_prompt: str) -> str:
        model = self.model if self.provider == "groq" else GROQ_MODEL
        # Groq uses standard OpenAI-compatible API
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 4096,
        }
        resp = requests.post(
            GROQ_API_URL,
            json=payload,
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    # ── structured extraction ───────────────────────────────────
    def extract_json(self, user_prompt: str) -> list[dict]:
        raw = self.generate(user_prompt)
        return self._parse_json_array(raw)

    @staticmethod
    def _parse_json_array(text: str) -> list[dict]:
        """Robustly extract a JSON array from LLM output."""
        text = text.strip()

        if text.startswith("```"):
            lines = text.splitlines()
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
        except json.JSONDecodeError:
            pass

        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

        return []
