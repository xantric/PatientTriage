"""Safety tests for the Watcher and the waiting-room simulation.

The Watcher's whole job is to make the waiting room safe over time. These
tests assert the properties that matter: it never lowers an acuity, it catches
a deteriorating patient, and under surge the unsafe-wait pressure and the
backstop alerts both go up while the safety invariant still holds.
"""

from __future__ import annotations

import pytest

from app.engine.deterioration import observe_vitals
from app.engine.watcher import simulate
from app.models import Vitals


def test_stable_patient_does_not_drift():
    base = Vitals(heart_rate=80, resp_rate=16, sbp=120, spo2=98, temp_c=37.0)
    later = observe_vitals(base, None, 240)
    assert later.model_dump() == base.model_dump()


def test_sepsis_profile_worsens_over_time():
    base = Vitals(heart_rate=96, resp_rate=20, sbp=118, spo2=95, temp_c=37.6)
    later = observe_vitals(base, "sepsis", 120)
    assert later.heart_rate > base.heart_rate
    assert later.resp_rate > base.resp_rate
    assert later.sbp < base.sbp
    assert later.temp_c > base.temp_c


def test_watcher_never_ratchets_down():
    for factor in (1, 3):
        report = simulate(surge_factor=factor)
        assert report.down_ratchets == 0


def test_deteriorating_patient_is_ratcheted_up():
    report = simulate(surge_factor=1)
    ratchets = [e for e in report.events if e.patient_id == "P-011" and e.kind == "ratchet"]
    assert ratchets, "the waiting-room sepsis case should be caught and escalated"
    # A ratchet only ever moves toward a lower (more urgent) number.
    for e in ratchets:
        assert e.after_acuity < e.before_acuity


def test_surge_increases_load_and_backstops():
    normal = simulate(surge_factor=1)
    surge = simulate(surge_factor=3)
    assert surge.total_patients > normal.total_patients
    assert surge.peak_waiting >= normal.peak_waiting
    assert surge.peak_unsafe_wait_min >= normal.peak_unsafe_wait_min
    assert surge.total_backstops >= normal.total_backstops


def test_every_patient_leaves_the_board():
    report = simulate(surge_factor=1)
    last = report.metrics[-1]
    assert last.waiting == 0
    assert last.in_treatment == 0
    assert last.done == report.total_patients


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
