"""Optional LLM layer for Sentinel.

Two jobs only: read a free-text note into structured intake, and rewrite a
decision the engine already made into plain language. The model never sets an
acuity. Every call is best-effort with a deterministic fallback, so the system
runs with no API key.
"""
