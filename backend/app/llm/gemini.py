"""Thin Gemini client over the google-genai SDK.

Everything is guarded. If the package is missing, the key is wrong, or the call
times out, `generate` returns an LLMResult with ok=False and the caller falls
back to the deterministic path. The SDK is imported lazily so the app runs with
google-genai uninstalled. Calls use temperature 0 and an in-process cache, so
the same demo produces the same words and burns almost no quota on a rerun.
"""

from __future__ import annotations

from dataclasses import replace
from time import perf_counter

from app.llm.types import LLMResult


class GeminiClient:
    provider = "gemini"

    def __init__(self, api_key: str, model: str, timeout_ms: int) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout_ms = timeout_ms
        self._client = None
        self._cache: dict[tuple, LLMResult] = {}
        self._call_history: list[float] = []

    def _ensure_client(self):
        if self._client is None:
            from google import genai  # lazy: only needed on a live call

            self._client = genai.Client(api_key=self.api_key)
        return self._client

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

        import time
        now = time.time()
        self._call_history = [t for t in self._call_history if now - t < 86400]
        
        if len(self._call_history) >= 500:
            return LLMResult(
                text="", model=self.model, provider="gemini",
                latency_ms=0.0, ok=False,
                error="RateLimitExceeded: Daily limit of 500 requests reached",
            )
            
        recent = [t for t in self._call_history if now - t < 60]
        if len(recent) >= 15:
            # We reached the 15 RPM limit. Wait until the oldest of the 15 calls expires.
            sleep_time = 60 - (now - recent[0])
            if sleep_time > 0:
                time.sleep(sleep_time + 0.1)
                now = time.time()
                
        self._call_history.append(now)

        start = perf_counter()
        try:
            from google.genai import types

            client = self._ensure_client()
            # We use JSON action protocol in the orchestrator, not Gemini AFC.
            # Newer google-genai SDKs warn on generate_content unless AFC is disabled.
            config = types.GenerateContentConfig(
                system_instruction=system,
                temperature=0.0,
                max_output_tokens=max_tokens,
                response_mime_type="application/json" if json_mode else None,
                http_options=types.HttpOptions(timeout=self.timeout_ms),
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            )
            resp = client.models.generate_content(
                model=self.model, contents=prompt, config=config
            )
            text = (getattr(resp, "text", None) or "").strip()
            usage = getattr(resp, "usage_metadata", None)
            result = LLMResult(
                text=text,
                model=self.model,
                provider="gemini",
                input_tokens=int(getattr(usage, "prompt_token_count", 0) or 0),
                output_tokens=int(getattr(usage, "candidates_token_count", 0) or 0),
                latency_ms=(perf_counter() - start) * 1000,
                ok=bool(text),
                error=None if text else "empty response",
            )
            if result.ok:
                self._cache[key] = result
            return result
        except Exception as exc:  # any SDK/network/auth failure degrades to fallback
            return LLMResult(
                text="",
                model=self.model,
                provider="gemini",
                latency_ms=(perf_counter() - start) * 1000,
                ok=False,
                error=f"{type(exc).__name__}: {str(exc)[:160]}",
            )
