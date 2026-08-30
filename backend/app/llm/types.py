"""Internal result type shared by the LLM client and the service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class LLMResult:
    text: str
    model: str
    provider: str  # gemini | rule-based
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cached: bool = False
    ok: bool = True
    error: Optional[str] = None
