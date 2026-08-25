"""Simulation-only deterioration model for the waiting room.

Given a patient's baseline vitals at arrival and the minutes elapsed since,
this returns the vitals a fresh re-recording would show. It is deterministic,
so the demo tells the same story every run. The ground truth lives here and in
the generator's `deteriorates` tag; the triage engine never sees it. The
Watcher only ever reads the re-recorded vitals, exactly like a nurse taking a
fresh set of obs would.
"""

from __future__ import annotations

from typing import Optional

from app.models import Vitals

# Per-minute drift by profile. Negative means the value falls over time.
# These are illustrative rates chosen so a waiting patient crosses a
# recognised danger threshold within a realistic wait, not clinical constants.
_PROFILES: dict[str, dict[str, float]] = {
    "sepsis": {"heart_rate": +0.12, "resp_rate": +0.08, "sbp": -0.10, "spo2": -0.03, "temp_c": +0.006},
    "resp": {"heart_rate": +0.06, "resp_rate": +0.12, "sbp": -0.02, "spo2": -0.07, "temp_c": 0.0},
    "cardiac": {"heart_rate": +0.06, "resp_rate": +0.03, "sbp": -0.08, "spo2": -0.03, "temp_c": 0.0},
    "bleed": {"heart_rate": +0.14, "resp_rate": +0.05, "sbp": -0.14, "spo2": -0.02, "temp_c": 0.0},
}

# Plausible bounds so a re-recording never returns something physiologically absurd.
_CLAMP: dict[str, tuple[float, float]] = {
    "heart_rate": (30, 220),
    "resp_rate": (4, 70),
    "sbp": (50, 240),
    "spo2": (60, 100),
    "temp_c": (34.0, 42.0),
}


def observe_vitals(baseline: Vitals, profile: Optional[str], elapsed_min: int) -> Vitals:
    """Return the vitals a re-recording at `elapsed_min` after arrival would show.

    `baseline` is the arrival snapshot and is never mutated. A stable patient
    (no profile) reads the same obs every time; a deteriorating one drifts
    linearly from baseline along their profile.
    """

    fresh = baseline.model_copy(deep=True)
    if not profile or profile not in _PROFILES or elapsed_min <= 0:
        return fresh

    rates = _PROFILES[profile]
    for field, rate in rates.items():
        value = getattr(baseline, field)
        if value is None or rate == 0.0:
            continue
        drifted = value + rate * elapsed_min
        low, high = _CLAMP[field]
        drifted = max(low, min(high, drifted))
        drifted = round(drifted, 1) if field == "temp_c" else float(round(drifted))
        setattr(fresh, field, drifted)
    return fresh
