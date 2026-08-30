"""Tests for the LLM layer.

These run in rule-based (stub) mode so they need no key and no network. The
properties that matter: the model never sets the acuity, a note with no data
reads as low confidence, and a generated explanation that names the wrong ESI
is rejected in favour of the template.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.engine.pipeline import triage
from app.llm.service import LLM, _mentions_wrong_acuity
from app.llm.stub import rule_parse, template_explanation
from app.state import DEPARTMENT


@pytest.fixture(autouse=True)
def stub_mode():
    """Force rule-based mode and a clean board for every test."""

    LLM.configure(mode="stub")
    LLM._telemetry.clear()
    DEPARTMENT.load(surge_factor=1)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def test_rule_parser_extracts_vitals_and_context():
    note = ("78F brought in by ambulance, feeling generally unwell and short of "
            "breath, sweaty. HR 104, BP 118/70, SpO2 93%, temp 36.6. hx of "
            "diabetes and coronary artery disease.")
    x = rule_parse(note)
    assert x.age_years == 78
    assert x.sex == "F"
    assert x.arrival_mode == "ambulance"
    assert x.heart_rate == 104
    assert x.sbp == 118 and x.dbp == 70
    assert x.spo2 == 93
    assert x.temp_c == 36.6
    assert any("diabet" in h.lower() for h in x.history)


def test_labeled_intake_form_keeps_name_and_complaint_separate():
    note = (
        "Patient Name: Ishan Kamboj\n"
        "Age: 21\n"
        "Sex: M\n"
        "Vitals: HR 98\n"
        "Note: Skin itching, redness"
    )
    x = rule_parse(note)
    assert x.patient_name == "Ishan Kamboj"
    assert x.age_years == 21
    assert x.sex == "M"
    assert x.heart_rate == 98
    assert "itch" in (x.chief_complaint or "").lower()
    assert "Patient Name" not in (x.chief_complaint or "")
    parsed, _ = LLM.parse_intake(note, "TEST-LABEL")
    assert parsed.patient.display_name == "Ishan Kamboj"
    assert parsed.patient.sex.value == "M"
    assert "itch" in parsed.patient.chief_complaint.lower()


def test_fahrenheit_is_converted_to_celsius():
    x = rule_parse("child with fever, temp 101.3")
    assert x.temp_c is not None and 38.0 <= x.temp_c <= 39.0


def test_status_is_rule_based_without_a_key():
    status = LLM.status()
    assert status.live is False
    assert status.provider_mode == "rule-based"


def test_intake_is_scored_by_the_engine_not_the_model():
    note = "59M crushing chest pain radiating to left arm, sweaty, HR 102, BP 140/90, onset 50 min ago"
    parsed, calls = LLM.parse_intake(note, "TEST-1")
    result = triage(parsed.patient)
    # The service must return exactly what the deterministic engine produces.
    assert result.adjudicator.acuity == triage(parsed.patient).adjudicator.acuity
    assert result.adjudicator.acuity <= 2  # clear cardiac story
    assert calls and calls[0].task == "parse"


def test_sparse_note_reads_low_confidence_and_routes_to_nurse():
    parsed, _ = LLM.parse_intake("feels a bit off", "TEST-2")
    result = triage(parsed.patient)
    assert result.interpreter.confidence_band.value == "low"
    assert result.adjudicator.routed_to_nurse is True


def test_explanation_names_the_actual_level():
    result = triage(DEPARTMENT.patients["P-001"])
    text = template_explanation(result)
    assert f"ESI {result.adjudicator.acuity}" in text


def test_wrong_acuity_mention_is_detected():
    assert _mentions_wrong_acuity("This is an ESI 4 patient.", 2) is True
    assert _mentions_wrong_acuity("This is an ESI 2 patient.", 2) is False


def test_explanation_never_reports_a_different_level():
    for pid in ("P-001", "P-002", "P-007"):
        result = triage(DEPARTMENT.patients[pid])
        explanation, _ = LLM.explain(result)
        assert not _mentions_wrong_acuity(explanation.text, result.adjudicator.acuity)


def test_telemetry_records_parse_and_explain():
    LLM.triage_intake("46M ankle pain after a fall, can bear weight", "TEST-3")
    summary = LLM.telemetry()
    assert summary.total_calls >= 2
    assert "parse" in summary.by_task and "explain" in summary.by_task


def test_sparse_wellness_note_asks_for_more_information():
    """Thin notes must not present a firm ESI with dramatic model prose."""

    parsed, result, explanation, calls, meta = LLM.triage_intake("21M, no disease", "TEST-SPARSE")
    assert parsed.patient.age_years == 21
    assert meta["needs_more_information"] is True
    assert any("vital" in g.lower() or "wellness" in g.lower() or "reason" in g.lower()
               for g in meta["information_gaps"])
    assert "Not enough information" in explanation.text
    assert explanation.source == "template"
    # Engine may still compute a reference score, but it should not escalate
    # a wellness phrase into ESI 2 via default resource guessing.
    assert result.adjudicator.acuity >= 4


def test_rule_parse_reads_compact_age_and_wellness_complaint():
    x = rule_parse("21M, no disease")
    assert x.age_years == 21
    assert x.sex == "M"
    assert "no disease" in (x.chief_complaint or "").lower()


def test_intake_endpoint_can_add_to_board(client):
    before = client.get("/api/board").json()["summary"]["total"]
    res = client.post("/api/intake", json={
        "text": "81F wheelchair, weak and confused, HR 110, BP 104/60, temp 38.3, hx UTI",
        "add_to_board": True,
    })
    assert res.status_code == 200
    body = res.json()
    assert body["added_patient_id"] is not None
    assert body["explanation"]["text"]
    assert 1 <= body["result"]["adjudicator"]["acuity"] <= 5
    after = client.get("/api/board").json()["summary"]["total"]
    assert after == before + 1


def test_intake_preview_does_not_touch_the_board(client):
    before = client.get("/api/board").json()["summary"]["total"]
    res = client.post("/api/intake", json={"text": "sore throat two days", "add_to_board": False})
    assert res.status_code == 200
    assert res.json()["added_patient_id"] is None
    assert client.get("/api/board").json()["summary"]["total"] == before


def test_telemetry_and_status_endpoints(client):
    assert client.get("/api/llm/status").json()["provider_mode"] == "rule-based"
    client.post("/api/intake", json={"text": "headache", "add_to_board": False})
    summary = client.get("/api/telemetry").json()
    assert summary["total_calls"] >= 2
    assert summary["est_cost_usd"] == 0.0  # rule-based mode is free


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
