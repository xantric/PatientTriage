"""Pytest hooks shared across the backend test suite."""

from __future__ import annotations

import os

# Board load runs at import time; keep tests deterministic and offline.
os.environ["SENTINEL_ASSESSMENT_CACHE"] = "0"
