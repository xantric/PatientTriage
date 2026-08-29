"""Synthetic patient cohort for the Sentinel prototype.

The cohort is deterministic (fixed seed) so the live demo tells the same
story every run. It deliberately includes the edge cases the Round 2 brief
requires: an ambiguous presentation, pediatric and geriatric cases, and a
zero-history walk-in, alongside clear high- and low-acuity anchors.

`expected_acuity` and `scenario_note` are labels used only to sanity-check
the engine. They are never fed into scoring.
"""

from __future__ import annotations

import random

from app.engine import thresholds as T
from app.models import (
    ArrivalMode,
    Patient,
    Responsiveness,
    Sex,
    Vitals,
)

SEED = 20260823


def _named_cases() -> list[Patient]:
    """Hand-authored cases that exercise the safety-critical paths."""

    cases: list[Patient] = [
        # Clear ESI-1: life-threatening, must trigger immediate intervention.
        Patient(
            patient_id="P-001",
            display_name="James Wilson",
            age_years=54,
            sex=Sex.male,
            arrival_mode=ArrivalMode.ambulance,
            chief_complaint="found collapsed, not responding",
            responsiveness=Responsiveness.unresponsive,
            vitals=Vitals(heart_rate=138, resp_rate=6, sbp=78, spo2=84, temp_c=36.1),
            onset_minutes=20,
            history=["hypertension"],
            has_prior_record=True,
            arrival_epoch_min=0,
            expected_acuity=1,
            scenario_note="Immediate life-saving intervention needed.",
        ),
        # Geriatric silent MI: atypical, no classic chest pain. Must NOT be under-triaged.
        Patient(
            patient_id="P-002",
            display_name="Martha Stewart",
            age_years=78,
            sex=Sex.female,
            arrival_mode=ArrivalMode.walk_in,
            chief_complaint="just feeling unwell and a bit short of breath, sweaty",
            pain_score=2,
            responsiveness=Responsiveness.alert,
            vitals=Vitals(heart_rate=104, resp_rate=22, sbp=118, spo2=93, temp_c=36.6),
            onset_minutes=40,
            history=["type 2 diabetes", "coronary artery disease"],
            medications=["metformin", "aspirin"],
            has_prior_record=True,
            arrival_epoch_min=3,
            expected_acuity=2,
            scenario_note="Silent MI risk despite mild symptoms; STEMI clock.",
        ),
        # Pediatric febrile toddler.
        Patient(
            patient_id="P-003",
            display_name="Liam Smith",
            age_years=3,
            sex=Sex.male,
            arrival_mode=ArrivalMode.walk_in,
            chief_complaint="high fever and very drowsy since morning",
            responsiveness=Responsiveness.voice,
            vitals=Vitals(heart_rate=158, resp_rate=42, sbp=92, spo2=96, temp_c=39.8),
            onset_minutes=300,
            history=[],
            has_prior_record=False,
            arrival_epoch_min=6,
            expected_acuity=2,
            scenario_note="Pediatric danger-zone HR/RR read against child band.",
        ),
        # Zero-history walk-in with sparse data (missing several vitals).
        Patient(
            patient_id="P-004",
            display_name="Emma Jones",
            age_years=29,
            sex=Sex.female,
            arrival_mode=ArrivalMode.walk_in,
            chief_complaint="dizzy and a little nauseous",
            pain_score=3,
            responsiveness=Responsiveness.alert,
            vitals=Vitals(heart_rate=98, spo2=None, sbp=None, resp_rate=None, temp_c=None),
            onset_minutes=None,
            history=[],
            has_prior_record=False,
            arrival_epoch_min=9,
            expected_acuity=3,
            scenario_note="Low completeness; confidence must drop, route to nurse.",
        ),
        # Ambiguous geriatric: vague weakness, borderline vitals. Sepsis vs benign.
        Patient(
            patient_id="P-005",
            display_name="Beatrice Arthur",
            age_years=81,
            sex=Sex.female,
            arrival_mode=ArrivalMode.wheelchair,
            chief_complaint="generally weak and confused today, not herself",
            responsiveness=Responsiveness.voice,
            vitals=Vitals(heart_rate=110, resp_rate=24, sbp=104, spo2=93, temp_c=38.3),
            onset_minutes=720,
            history=["urinary tract infection last month"],
            has_prior_record=True,
            arrival_epoch_min=12,
            expected_acuity=2,
            scenario_note="Possible sepsis; ambiguity must bias up, sepsis clock.",
        ),
        # Acute stroke within thrombolysis window.
        Patient(
            patient_id="P-006",
            display_name="Robert Clark",
            age_years=67,
            sex=Sex.male,
            arrival_mode=ArrivalMode.ambulance,
            chief_complaint="sudden facial droop and slurred speech, weak right arm",
            responsiveness=Responsiveness.alert,
            vitals=Vitals(heart_rate=88, resp_rate=18, sbp=162, spo2=97, temp_c=36.8),
            onset_minutes=95,
            history=["atrial fibrillation"],
            has_prior_record=True,
            arrival_epoch_min=15,
            expected_acuity=2,
            scenario_note="Stroke signs, within 4.5h window.",
        ),
        # Clear low acuity: prescription refill.
        Patient(
            patient_id="P-007",
            display_name="David Brown",
            age_years=34,
            sex=Sex.male,
            arrival_mode=ArrivalMode.walk_in,
            chief_complaint="need a refill for my blood pressure tablets",
            pain_score=0,
            responsiveness=Responsiveness.alert,
            vitals=Vitals(heart_rate=72, resp_rate=14, sbp=124, dbp=80, spo2=99, temp_c=36.7),
            onset_minutes=None,
            history=["hypertension"],
            has_prior_record=True,
            arrival_epoch_min=18,
            expected_acuity=5,
            scenario_note="No resources needed; clear ESI-5.",
        ),
        # Anaphylaxis: airway threat.
        Patient(
            patient_id="P-008",
            display_name="Sophia Davis",
            age_years=22,
            sex=Sex.female,
            arrival_mode=ArrivalMode.ambulance,
            chief_complaint="lips swelling and wheezing after eating peanuts",
            responsiveness=Responsiveness.alert,
            vitals=Vitals(heart_rate=126, resp_rate=30, sbp=96, spo2=89, temp_c=36.9),
            onset_minutes=25,
            allergies=["peanuts"],
            has_prior_record=False,
            arrival_epoch_min=21,
            expected_acuity=1,
            scenario_note="Airway/breathing threat; SpO2 critical.",
        ),
        # Moderate: minor laceration, one resource.
        Patient(
            patient_id="P-009",
            display_name="Michael Miller",
            age_years=41,
            sex=Sex.male,
            arrival_mode=ArrivalMode.walk_in,
            chief_complaint="cut my hand on a knife, bleeding controlled",
            pain_score=4,
            responsiveness=Responsiveness.alert,
            vitals=Vitals(heart_rate=84, resp_rate=16, sbp=132, dbp=84, spo2=98, temp_c=36.8),
            onset_minutes=60,
            has_prior_record=False,
            arrival_epoch_min=24,
            expected_acuity=4,
            scenario_note="Single resource (sutures); low risk vitals.",
        ),
        # Chest pain, cardiac features, adult.
        Patient(
            patient_id="P-010",
            display_name="Thomas Moore",
            age_years=59,
            sex=Sex.male,
            arrival_mode=ArrivalMode.ambulance,
            chief_complaint="crushing chest pain radiating to left arm, sweating",
            pain_score=8,
            responsiveness=Responsiveness.alert,
            vitals=Vitals(heart_rate=102, resp_rate=20, sbp=140, dbp=90, spo2=95, temp_c=36.7),
            onset_minutes=50,
            history=["hyperlipidemia", "smoker"],
            has_prior_record=True,
            arrival_epoch_min=27,
            expected_acuity=2,
            scenario_note="Classic STEMI features; time-critical clock.",
        ),
        # Waiting-room deterioration: arrives looking like a stable ESI-3 with a
        # urinary infection, but silently slides into sepsis while it waits. This
        # is the case the Watcher must catch and ratchet up on re-check.
        Patient(
            patient_id="P-011",
            display_name="Susan Taylor",
            age_years=63,
            sex=Sex.female,
            arrival_mode=ArrivalMode.walk_in,
            chief_complaint="burning when passing urine and feeling a bit flushed",
            pain_score=3,
            responsiveness=Responsiveness.alert,
            vitals=Vitals(heart_rate=96, resp_rate=20, sbp=118, spo2=95, temp_c=37.6),
            onset_minutes=600,
            history=["recurrent urinary tract infection"],
            has_prior_record=True,
            arrival_epoch_min=33,
            expected_acuity=3,
            scenario_note="Looks ESI-3 at intake; deteriorates to sepsis while waiting.",
            deteriorates="sepsis",
        ),
    ]
    return cases


_COMPLAINTS_LOW = [
    "sore throat for two days",
    "mild ankle sprain, can walk",
    "rash on arm, no other symptoms",
    "needs a tetanus shot",
    "follow-up dressing change",
]
_COMPLAINTS_MID = [
    "abdominal pain, moderate",
    "vomiting since last night",
    "migraine, photophobia",
    "back pain after lifting",
    "fever and cough for three days",
]


# Age-plausible systolic pressure windows for background patients. These shape
# the simulated data only; the clinical thresholds live in engine/thresholds.py.
_SBP_TYPICAL: dict[T.AgeBand, tuple[int, int]] = {
    T.AgeBand.infant: (76, 96),
    T.AgeBand.child: (92, 112),
    T.AgeBand.adolescent: (106, 126),
    T.AgeBand.adult: (110, 140),
    T.AgeBand.geriatric: (118, 152),
}


def _sample_in_band(rng: random.Random, span: T.Range, sicker: bool) -> int:
    """Draw a vital from an age band's normal range.

    Background patients stay inside their own age band's normal range: sicker
    ones sit at the top of it, well ones in the middle. That keeps the filler
    cohort believable and stops the demo manufacturing false ESI-1s, so any
    danger-zone flag on the board comes from a case that earns it.
    """

    width = span.high - span.low
    low, high = (
        (span.low + width * 0.6, span.high) if sicker
        else (span.low + width * 0.15, span.high - width * 0.2)
    )
    return int(round(rng.uniform(low, high)))


def _random_fillers(n: int, start_epoch: int, spacing: float = 3.0) -> list[Patient]:
    rng = random.Random(SEED)
    out: list[Patient] = []
    for i in range(n):
        pid = f"P-{100 + i:03d}"
        age = rng.choice([6, 19, 25, 33, 44, 52, 60, 71, 84])
        band = T.age_band(age)
        low = rng.random() < 0.5
        complaint = rng.choice(_COMPLAINTS_LOW if low else _COMPLAINTS_MID)
        has_record = rng.random() < 0.5

        # Vitals are drawn against the patient's own age band, not an adult default.
        hr = _sample_in_band(rng, T.HR_NORMAL[band], sicker=not low)
        rr = _sample_in_band(rng, T.RR_NORMAL[band], sicker=not low)

        # Pressure is derived from heart rate against a target shock index, so a
        # child's fast pulse does not read as shock purely because of their age.
        cutoff = T.shock_index_cutoff(band)
        target_si = (
            rng.uniform(cutoff - 0.14, cutoff + 0.04) if not low
            else rng.uniform(0.55 * cutoff, cutoff - 0.10)
        )
        sbp_lo, sbp_hi = _SBP_TYPICAL[band]
        sbp = max(sbp_lo, min(sbp_hi, int(round(hr / target_si))))
        if low and hr / sbp >= cutoff:
            sbp = sbp_hi  # a clearly well patient should not read as shocked

        spo2 = rng.choice([95, 96, 97, 98, 99] if low else [92, 94, 95, 96, 97])
        temp = round(rng.uniform(36.4, 39.2 if not low else 37.4), 1)
        # Randomly drop a vital to reflect imperfect intake.
        vit = Vitals(heart_rate=hr, resp_rate=rr, sbp=sbp, spo2=spo2, temp_c=temp)
        if rng.random() < 0.3:
            drop = rng.choice(["resp_rate", "spo2", "temp_c"])
            setattr(vit, drop, None)
        first_names = ["John", "Mary", "Michael", "Sarah", "William", "Jessica", "David", "Emily", "Richard", "Ashley"]
        last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez"]
        fake_name = f"{rng.choice(first_names)} {rng.choice(last_names)}"
        out.append(
            Patient(
                patient_id=pid,
                display_name=fake_name,
                age_years=age,
                sex=rng.choice([Sex.male, Sex.female]),
                arrival_mode=rng.choice([ArrivalMode.walk_in, ArrivalMode.wheelchair]),
                chief_complaint=complaint,
                pain_score=rng.randint(0, 6),
                responsiveness=Responsiveness.alert,
                vitals=vit,
                onset_minutes=rng.choice([None, 120, 240, 480]),
                has_prior_record=has_record,
                arrival_epoch_min=start_epoch + int(round(i * spacing)),
                expected_acuity=5 if low else 3,
                scenario_note="Randomised filler patient.",
            )
        )
    return out


def build_cohort(n_fillers: int = 14, surge_factor: int = 1) -> list[Patient]:
    """Return the full simulated cohort (named cases + randomised fillers).

    `surge_factor` triples (or more) the arrival rate: it scales the number of
    walk-in fillers and compresses the spacing between them by the same factor,
    so the same window sees `surge_factor` times as many arrivals. The named
    edge cases are unchanged so the safety story stays comparable across runs.
    """

    named = _named_cases()
    fillers = _random_fillers(
        n_fillers * surge_factor,
        start_epoch=30,
        spacing=3.0 / surge_factor,
    )
    return named + fillers


if __name__ == "__main__":
    cohort = build_cohort()
    print(f"cohort size: {len(cohort)}")
    for p in cohort:
        present = ", ".join(p.vitals.present_fields()) or "none"
        print(f"  {p.patient_id:8} age {p.age_years:>4}  vitals[{present}]  {p.chief_complaint[:48]}")
