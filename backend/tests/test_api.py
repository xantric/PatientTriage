"""Tests for the API, the override flow, and the audit trail.

The checklist item these cover is "capture a clinician override and log it".
The properties worth protecting: an override cannot be recorded without a
reason, it never erases what the engine said, and the trail cannot be edited.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.state import DEPARTMENT


@pytest.fixture(autouse=True)
def fresh_department():
    """Each test starts from a clean board at normal load."""

    DEPARTMENT.load(surge_factor=1)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_health(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["patients"] > 0


def test_board_is_sorted_and_summarised(client):
    board = client.get("/api/board").json()
    acuities = [r["acuity"] for r in board["rows"]]
    assert acuities == sorted(acuities), "board must show the sickest first"
    assert board["summary"]["total"] == len(board["rows"])
    # Every row ships a confidence band; nothing is scored without one.
    assert all(r["confidence_band"] in ("high", "medium", "low") for r in board["rows"])


def test_patient_detail_exposes_reasoning(client):
    detail = client.get("/api/patients/P-002").json()
    adj = detail["result"]["adjudicator"]
    assert adj["rationale"], "the drill-down must show why the score was given"
    assert any(c["name"] == "stemi" for c in adj["time_critical_clocks"])


def test_unknown_patient_is_404(client):
    assert client.get("/api/patients/P-999").status_code == 404


def test_override_requires_a_reason(client):
    res = client.post("/api/overrides", json={"patient_id": "P-009", "new_acuity": 2, "reason": ""})
    assert res.status_code == 422


def test_override_changes_board_but_keeps_engine_score(client):
    before = client.get("/api/patients/P-009").json()["row"]
    engine_acuity = before["engine_acuity"]

    res = client.post("/api/overrides", json={
        "patient_id": "P-009",
        "new_acuity": 2,
        "reason": "wound is deeper than described and still bleeding",
        "actor": "R. Okafor",
        "actor_role": "triage nurse",
    })
    assert res.status_code == 200
    row = res.json()["row"]
    assert row["acuity"] == 2
    assert row["engine_acuity"] == engine_acuity, "the engine's own score must survive an override"
    assert row["overridden"] is True
    assert row["override_direction"] == "escalate"


def test_override_is_written_to_the_audit_trail(client):
    client.post("/api/overrides", json={
        "patient_id": "P-007",
        "new_acuity": 3,
        "reason": "patient also reports chest tightness not captured at intake",
        "actor": "J. Bell",
    })
    records = client.get("/api/audit").json()
    entry = [r for r in records if r["patient_id"] == "P-007"][-1]
    assert entry["action"] == "override"
    assert entry["actor"] == "J. Bell"
    assert entry["reason"].startswith("patient also reports")
    assert entry["model_version"]
    assert entry["timestamp_utc"]
    assert entry["before_acuity"] == 5 and entry["after_acuity"] == 3
    assert entry["direction"] == "escalate"
    # The engine's confidence at the time of the decision is captured too.
    assert entry["engine_confidence"] is not None


def test_de_escalation_is_allowed_but_recorded(client):
    client.post("/api/overrides", json={
        "patient_id": "P-003",
        "new_acuity": 3,
        "reason": "child alert and playing on review, fever responded to antipyretic",
        "actor": "S. Mehta",
        "actor_role": "attending",
    })
    entry = client.get("/api/audit").json()[-1]
    assert entry["direction"] == "de-escalate"
    assert entry["actor_role"] == "attending"


def test_audit_trail_cannot_be_mutated_by_callers(client):
    client.post("/api/overrides", json={
        "patient_id": "P-009", "new_acuity": 2, "reason": "still bleeding",
    })
    snapshot = DEPARTMENT.audit.all()
    snapshot[0].reason = "tampered"
    assert DEPARTMENT.audit.all()[0].reason != "tampered"


def test_reset_clears_overrides_but_keeps_the_trail(client):
    client.post("/api/overrides", json={
        "patient_id": "P-009", "new_acuity": 2, "reason": "still bleeding",
    })
    assert client.get("/api/board").json()["summary"]["overrides"] == 1

    client.post("/api/board/reset?surge_factor=3")
    board = client.get("/api/board").json()
    assert board["summary"]["overrides"] == 0
    assert board["surge_factor"] == 3
    # History survives a reload, and the reload itself is recorded.
    actions = [r["action"] for r in client.get("/api/audit").json()]
    assert "override" in actions and "reset" in actions


def test_simulation_endpoint_returns_a_report(client):
    report = client.get("/api/simulation?surge_factor=3").json()
    assert report["down_ratchets"] == 0
    assert report["metrics"]
    assert all(e["kind"] in ("ratchet", "backstop") for e in report["events"])


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
