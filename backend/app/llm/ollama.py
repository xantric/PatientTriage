"""Thin Ollama client over the local HTTP API.

Same generate() contract as GeminiClient so intake, explain, and the agent
loop can swap providers without changing callers. No API key required.
"""

from __future__ import annotations

from dataclasses import replace
from time import perf_counter

import httpx

from app.llm.types import LLMResult


class OllamaClient:
    provider = "ollama"

    def __init__(self, model: str, base_url: str, timeout_ms: int) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_ms = timeout_ms
        self._cache: dict[tuple, LLMResult] = {}

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        json_mode: bool,
        max_tokens: int,
        cache_key: str,
    ) -> LLMResult:
        key = (self.model, json_mode, cache_key)
        if key in self._cache:
            return replace(self._cache[key], cached=True, latency_ms=0.0)

        start = perf_counter()
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_predict": max_tokens,
            },
        }
        if json_mode:
            payload["format"] = "json"

        try:
            with httpx.Client(timeout=self.timeout_ms / 1000.0) as client:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
            message = data.get("message") or {}
            text = (message.get("content") or "").strip()
            # Ollama sometimes wraps JSON in markdown fences even with format=json.
            if json_mode and text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:].strip()
            prompt_eval = int(data.get("prompt_eval_count") or 0)
            eval_count = int(data.get("eval_count") or 0)
            result = LLMResult(
                text=text,
                model=self.model,
                provider="ollama",
                input_tokens=prompt_eval,
                output_tokens=eval_count,
                latency_ms=(perf_counter() - start) * 1000,
                ok=bool(text),
                error=None if text else "empty response",
            )
            if result.ok:
                self._cache[key] = result
            return result
        except Exception as exc:
            return LLMResult(
                text="",
                model=self.model,
                provider="ollama",
                latency_ms=(perf_counter() - start) * 1000,
                ok=False,
                error=f"{type(exc).__name__}: {str(exc)[:160]}",
            )
