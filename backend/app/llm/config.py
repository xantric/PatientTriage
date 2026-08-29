"""LLM configuration, read from the environment (and an optional .env file).

Nothing here is required. With no key set, the whole layer runs in rule-based
mode and the app behaves exactly as it did before Phase 4.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

# app/llm/config.py -> parents[2] is the backend/ directory.
BACKEND_DIR = Path(__file__).resolve().parents[2]


def _load_dotenv() -> None:
    """Load backend/.env if python-dotenv is installed. Optional by design."""

    try:
        from dotenv import load_dotenv
    except Exception:
        return
    load_dotenv(BACKEND_DIR / ".env")


_load_dotenv()


def configured_mode() -> str:
    """auto (default), gemini, ollama, or stub."""

    return (os.getenv("SENTINEL_LLM") or "auto").strip().lower()


def model_name() -> str:
    mode = configured_mode()
    if mode == "ollama":
        return (os.getenv("OLLAMA_MODEL") or "llama3.1:8b").strip()
    return (os.getenv("GEMINI_MODEL") or "gemini-3.1-flash-lite").strip()


def api_key() -> str | None:
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    return key.strip() if key else None


def ollama_base_url() -> str:
    return (os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").strip().rstrip("/")


def timeout_ms() -> int:
    # Gemini rejects deadlines under 10s. Ollama local runs can need longer.
    try:
        value = int(os.getenv("SENTINEL_LLM_TIMEOUT_MS", "15000"))
    except ValueError:
        value = 15000
    if configured_mode() == "ollama":
        return max(value, 60000)
    return max(value, 10000)


def package_available() -> bool:
    try:
        return importlib.util.find_spec("google.genai") is not None
    except Exception:
        return False


def ollama_reachable() -> bool:
    """Best-effort ping so status can show whether Ollama is up."""

    try:
        import httpx

        with httpx.Client(timeout=2.0) as client:
            r = client.get(f"{ollama_base_url()}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


# Rough public list-prices in USD per one million tokens (input, output). These
# move often and are only used to show an estimated demo cost, never to bill.
# Override or extend as needed; unknown models fall back to a flash-lite guess.
_PRICE_PER_M: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-3.5-flash-lite": (0.25, 1.00),
    "gemini-3.1-flash-lite": (0.25, 1.00),
    "gemini-3.6-flash": (0.50, 3.00),
}
_DEFAULT_PRICE = (0.10, 0.40)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    inp, out = _PRICE_PER_M.get(model, _DEFAULT_PRICE)
    return round((input_tokens / 1_000_000) * inp + (output_tokens / 1_000_000) * out, 6)
