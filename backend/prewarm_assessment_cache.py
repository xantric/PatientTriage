"""Warm the SQLite assessment cache for normal and 3x surge cohorts.

Run once with GEMINI_API_KEY set. Respects SENTINEL_GEMINI_RPM (default 12).
Already-cached patients are skipped. Safe to re-run; it only fills gaps.

Usage (from backend/):

    python prewarm_assessment_cache.py
    python prewarm_assessment_cache.py --surge 3
"""

from __future__ import annotations

import argparse
import sys

from app.agent.primary import run_primary_assessment
from app.cache.assessment_cache import assessment_cache
from app.data.generator import build_cohort
from app.domain.enums import DecisionSource
from app.llm.config import gemini_rpm


def _expected_size(surge_factor: int) -> int:
    return len(build_cohort(surge_factor=surge_factor))


def prewarm_surge(surge_factor: int) -> tuple[int, int, int]:
    """Return (skipped, assessed, failed) for one surge level."""

    skipped = assessed = failed = 0
    cohort = build_cohort(surge_factor=surge_factor)

    for patient in cohort:
        key = assessment_cache.make_key(patient.patient_id, surge_factor)
        if assessment_cache.get(key) is not None:
            skipped += 1
            continue

        if surge_factor != 1:
            base_key = assessment_cache.make_key(patient.patient_id, 1)
            inherited = assessment_cache.get(base_key)
            if inherited is not None:
                assessment_cache.put(key, inherited)
                skipped += 1
                continue

        outcome = run_primary_assessment(patient, force_fallback=False)
        if outcome.decision_source == DecisionSource.agent:
            assessment_cache.put(key, outcome)
            assessed += 1
            print(f"  cached {patient.patient_id} (surge x{surge_factor})")
        else:
            failed += 1
            reason = outcome.fallback_reason or "unknown"
            print(f"  skip {patient.patient_id}: {reason}", file=sys.stderr)

    return skipped, assessed, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="Warm Gemini assessment SQLite cache")
    parser.add_argument(
        "--surge",
        type=int,
        action="append",
        dest="surges",
        help="Surge factor to warm (default: 1 and 3)",
    )
    args = parser.parse_args()
    surges = args.surges or [1, 3]

    if not assessment_cache.enabled():
        print("SENTINEL_ASSESSMENT_CACHE is disabled.", file=sys.stderr)
        return 1

    print(f"Gemini rate limit: {gemini_rpm()} requests/minute")
    print(f"Cache DB: {assessment_cache._path()}")

    for surge in surges:
        expected = _expected_size(surge)
        print(f"\nSurge x{surge}: {expected} patients")
        skipped, assessed, failed = prewarm_surge(surge)
        print(f"  done: {assessed} new, {skipped} already cached, {failed} fallback")

    by_surge = assessment_cache.count_by_surge()
    print(f"\nCache totals by surge: {by_surge}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
