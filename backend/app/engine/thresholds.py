"""Age-banded vital-sign reference ranges and danger zones.

Values are illustrative and adapted from widely used pediatric and adult
references (PALS/APLS age bands, the Emergency Severity Index danger-zone
vitals, and standard shock-index cutoffs). They are tuned for a prototype
that must bias toward escalation, not for clinical deployment. Every number
here is intended to be reviewable and easily reconfigured per hospital.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AgeBand(str, Enum):
    infant = "infant"          # < 1 year
    child = "child"            # 1 to < 12 years
    adolescent = "adolescent"  # 12 to < 18 years
    adult = "adult"            # 18 to < 65 years
    geriatric = "geriatric"    # >= 65 years


def age_band(age_years: float) -> AgeBand:
    if age_years < 1:
        return AgeBand.infant
    if age_years < 12:
        return AgeBand.child
    if age_years < 18:
        return AgeBand.adolescent
    if age_years < 65:
        return AgeBand.adult
    return AgeBand.geriatric


@dataclass(frozen=True)
class Range:
    low: float
    high: float

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high


# Normal (reassuring) awake ranges per band.
HR_NORMAL: dict[AgeBand, Range] = {
    AgeBand.infant: Range(100, 160),
    AgeBand.child: Range(80, 130),
    AgeBand.adolescent: Range(60, 105),
    AgeBand.adult: Range(60, 100),
    AgeBand.geriatric: Range(60, 100),
}

# Critical tachycardia (at or above -> danger-zone vital).
HR_CRIT_HIGH: dict[AgeBand, float] = {
    AgeBand.infant: 180,
    AgeBand.child: 160,
    AgeBand.adolescent: 130,
    AgeBand.adult: 120,
    AgeBand.geriatric: 120,
}

# Critical bradycardia (at or below -> danger-zone vital).
HR_CRIT_LOW: dict[AgeBand, float] = {
    AgeBand.infant: 80,
    AgeBand.child: 60,
    AgeBand.adolescent: 45,
    AgeBand.adult: 40,
    AgeBand.geriatric: 40,
}

RR_NORMAL: dict[AgeBand, Range] = {
    AgeBand.infant: Range(30, 60),
    AgeBand.child: Range(20, 40),
    AgeBand.adolescent: Range(12, 20),
    AgeBand.adult: Range(12, 20),
    AgeBand.geriatric: Range(12, 22),
}

RR_CRIT_HIGH: dict[AgeBand, float] = {
    AgeBand.infant: 70,
    AgeBand.child: 50,
    AgeBand.adolescent: 30,
    AgeBand.adult: 28,
    AgeBand.geriatric: 28,
}

RR_CRIT_LOW: dict[AgeBand, float] = {
    AgeBand.infant: 20,
    AgeBand.child: 15,
    AgeBand.adolescent: 8,
    AgeBand.adult: 8,
    AgeBand.geriatric: 8,
}

# Profoundly low respiratory rate: near-apnoeic, an immediate airway threat on
# its own. A merely low rate in an alert, well-saturated patient is a
# danger-zone vital (ESI 2), not an ESI-1 resuscitation call, so the two
# thresholds are kept separate.
RR_APNEIC: dict[AgeBand, float] = {
    AgeBand.infant: 12,
    AgeBand.child: 10,
    AgeBand.adolescent: 6,
    AgeBand.adult: 6,
    AgeBand.geriatric: 6,
}

# Systolic hypotension threshold (below -> shock concern) per band.
SBP_HYPOTENSION: dict[AgeBand, float] = {
    AgeBand.infant: 70,
    AgeBand.child: 80,
    AgeBand.adolescent: 90,
    AgeBand.adult: 90,
    AgeBand.geriatric: 100,  # relative hypotension matters more in the elderly
}

# SpO2 is treated the same across ages for a prototype.
SPO2_ABNORMAL = 94.0   # below this is abnormal
SPO2_CRITICAL = 90.0   # below this is a life-threat / ESI-1 trigger

# Temperature (Celsius).
FEVER = 38.0
HIGH_FEVER = 39.5
HYPOTHERMIA = 35.0

# Age-adjusted shock index (HR / SBP). Above cutoff -> elevated.
# Pediatric cutoffs follow the Shock Index Pediatric Age-adjusted (SIPA) idea.
def shock_index_cutoff(band: AgeBand) -> float:
    return {
        AgeBand.infant: 1.2,
        AgeBand.child: 1.2,
        AgeBand.adolescent: 1.0,
        AgeBand.adult: 0.9,
        AgeBand.geriatric: 0.9,
    }[band]


def shock_index_critical(band: AgeBand) -> float:
    """Shock index that suggests profound shock, by age band.

    A single flat cutoff cannot work here. Children run fast heart rates against
    lower blood pressures, so a healthy toddler sits at a shock index that would
    be alarming in an adult. These follow the same SIPA logic as the elevated
    cutoffs above, set roughly 45% above them.
    """

    return {
        AgeBand.infant: 2.2,
        AgeBand.child: 1.8,
        AgeBand.adolescent: 1.4,
        AgeBand.adult: 1.3,
        AgeBand.geriatric: 1.3,
    }[band]


# Maximum safe wait (minutes) before the Watcher must re-assess, by acuity.
# Used by the deterioration / timer backstop logic downstream.
SAFE_WAIT_MINUTES: dict[int, int] = {
    1: 0,
    2: 10,
    3: 30,
    4: 60,
    5: 120,
}
