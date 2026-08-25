"""Safety-oriented tests for the Sentinel engine.

The guiding rule of this track is that under-triage is categorically worse
than over-triage. These tests therefore assert lower bounds on urgency
(acuity must be at least this urgent) rather than exact matches.
"""

from __future__ import annotations

import pytest

from app.data.generator import build_cohort
from app.engine.interpreter import interpret
from app.engine.pipeline import triage
from app.engine.thresholds import AgeBand, age_band
from app.models import (
    ArrivalMode,
    ConfidenceBand,
    Patient,
    Responsiveness,
    Sex,
    Vitals,
)


def _by_id(pid: str) -> Patient:
    for p in build_cohort():
        if p.patient_id == pid:
            return p
    raise KeyError(pid)


def test_age_bands():
    assert age_band(0.5) == AgeBand.infant
    assert age_band(3) == AgeBand.child
    assert age_band(15) == AgeBand.adolescent
    assert age_band(40) == AgeBand.adult
    assert age_band(78) == AgeBand.geriatric


def test_unresponsive_is_esi1():
    r = triage(_by_id("P-001"))
    assert r.adjudicator.acuity == 1


def test_anaphylaxis_is_esi1():
    r = triage(_by_id("P-008"))
    assert r.adjudicator.acuity == 1


def test_geriatric_silent_mi_not_undertriaged():
    # Atypical presentation, mild pain: must still be ESI <= 2.
    r = triage(_by_id("P-002"))
    assert r.adjudicator.acuity <= 2
    assert any(c.name == "stemi" for c in r.adjudicator.time_critical_clocks)


def test_febrile_toddler_uses_pediatric_thresholds():
    r = triage(_by_id("P-003"))
    # HR 158 and RR 42 are normal-ish for an infant band but dangerous framing
    # for a 3yo child; the child band must flag them and drive acuity up.
    assert r.interpreter.age_band == "child"
    assert r.adjudicator.acuity <= 2


def test_zero_history_walkin_low_confidence_routes_to_nurse():
    r = triage(_by_id("P-004"))
    assert r.interpreter.confidence_band == ConfidenceBand.low
    assert r.adjudicator.routed_to_nurse is True


def test_ambiguous_geriatric_biases_up_with_sepsis_clock():
    r = triage(_by_id("P-005"))
    assert r.adjudicator.acuity <= 2
    assert any(c.name == "sepsis" for c in r.adjudicator.time_critical_clocks)


def test_stroke_in_window_flagged():
    r = triage(_by_id("P-006"))
    clocks = {c.name: c for c in r.adjudicator.time_critical_clocks}
    assert "stroke" in clocks
    assert clocks["stroke"].in_window is True
    assert r.adjudicator.acuity <= 2


def test_refill_is_low_acuity():
    r = triage(_by_id("P-007"))
    assert r.adjudicator.acuity >= 4


def test_every_result_has_confidence():
    for p in build_cohort():
        r = triage(p)
        assert 0.0 <= r.interpreter.confidence <= 1.0
        assert r.adjudicator.confidence_band in ConfidenceBand


def test_no_named_case_is_undertriaged():
    # For labelled named cases, the engine must never be LESS urgent than the
    # expected acuity (a higher number = less urgent = under-triage).
    for pid in [f"P-{i:03d}" for i in range(1, 12)]:
        p = _by_id(pid)
        r = triage(p)
        if p.expected_acuity is not None:
            assert r.adjudicator.acuity <= p.expected_acuity, (
                f"{pid} under-triaged: got {r.adjudicator.acuity}, "
                f"expected <= {p.expected_acuity}"
            )


def test_missing_vitals_lowers_confidence():
    full = Patient(
        patient_id="T-1", age_years=40, sex=Sex.male,
        chief_complaint="abdominal pain",
        responsiveness=Responsiveness.alert,
        vitals=Vitals(heart_rate=80, resp_rate=16, sbp=120, spo2=98, temp_c=37.0),
        has_prior_record=True, onset_minutes=120,
    )
    sparse = Patient(
        patient_id="T-2", age_years=40, sex=Sex.male,
        chief_complaint="abdominal pain",
        responsiveness=Responsiveness.alert,
        vitals=Vitals(heart_rate=80),
        has_prior_record=False,
    )
    assert interpret(full).confidence > interpret(sparse).confidence


def test_hypoxia_is_life_threat():
    p = Patient(
        patient_id="T-3", age_years=50, sex=Sex.female,
        arrival_mode=ArrivalMode.ambulance,
        chief_complaint="short of breath",
        responsiveness=Responsiveness.alert,
        vitals=Vitals(heart_rate=110, resp_rate=26, sbp=110, spo2=86, temp_c=37.0),
    )
    assert triage(p).adjudicator.acuity == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
