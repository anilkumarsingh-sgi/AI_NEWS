"""
Ollama client wrapper for sending prompts and receiving structured responses.
"""

import json
import requests
from config import OLLAMA_BASE_URL, OLLAMA_MODEL, OLLAMA_TIMEOUT
from prompts import SYSTEM_PROMPT


class OllamaClient:
    """Handles communication with the local Ollama API."""

    def __init__(self, base_url=None, model=None, timeout=None):
        self.base_url = (base_url or OLLAMA_BASE_URL).rstrip("/")
        self.model = model or OLLAMA_MODEL
        self.timeout = timeout or OLLAMA_TIMEOUT

    # ── health check ────────────────────────────────────────────
    def is_available(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return resp.status_code == 200
        except requests.ConnectionError:
            return False

    def list_models(self) -> list[str]:
        resp = requests.get(f"{self.base_url}/api/tags", timeout=5)
        resp.raise_for_status()
        return [m["name"] for m in resp.json().get("models", [])]

    # ── core generation ─────────────────────────────────────────
    def generate(self, user_prompt: str) -> str:
        """Send system + user prompt to Ollama and return raw text response."""
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {
                "temperature": 0.1,      # low temp for deterministic extraction
                "num_predict": 4096,
            },
        }
        resp = requests.post(
            f"{self.base_url}/api/chat",
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    # ── structured extraction ───────────────────────────────────
    def extract_json(self, user_prompt: str) -> list[dict]:
        """Send prompt, parse the response as JSON array."""
        raw = self.generate(user_prompt)
        return self._parse_json_array(raw)

    @staticmethod
    def _parse_json_array(text: str) -> list[dict]:
        """Robustly extract a JSON array from LLM output."""
        text = text.strip()

        # Strip markdown fences if the model wraps output
        if text.startswith("```"):
            lines = text.splitlines()
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        # Try direct parse
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return [data]
        except json.JSONDecodeError:
            pass

        # Try to find the JSON array within surrounding text
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(text[start : end + 1])
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

        # If no JSON found, the model likely found no accidents — return empty
        return []
