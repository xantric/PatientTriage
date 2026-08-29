"""Phase 8 failure cases and HITL / audit integration checks."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.agent.orchestrator import MockAgentLLM
from app.agent.primary import run_primary_assessment
from app.agent.registry import get_default_registry, reset_default_registry
from app.api import app
from app.data.generator import build_cohort
from app.domain.enums import DecisionSource
from app.models import HitlRequest
from app.state import DEPARTMENT


@pytest.fixture(autouse=True)
def _reset():
    reset_default_registry()
    DEPARTMENT.agent_llm = None
    DEPARTMENT.force_fallback = False
    DEPARTMENT.load(surge_factor=1)
    yield
    reset_default_registry()


def _action(obj: dict) -> str:
    return json.dumps(obj)


def test_gemini_unavailable_path_is_deterministic_fallback():
    patient = build_cohort(surge_factor=1)[0]
    outcome = run_primary_assessment(patient, force_fallback=True)
    assert outcome.decision_source == DecisionSource.deterministic_fallback
    assert outcome.state.current_recommendation is not None


def test_malformed_output_falls_back_safely():
    patient = build_cohort(surge_factor=1)[0]
    mock = MockAgentLLM(["{", "nope", '{"action":"NOPE"}'])
    outcome = run_primary_assessment(patient, llm=mock)
    assert outcome.decision_source == DecisionSource.deterministic_fallback
    assert outcome.state.status.value == "AWAITING_HUMAN"


def test_unknown_tool_does_not_fabricate_then_can_recommend():
    patient = build_cohort(surge_factor=1)[0]
    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "invent_vitals",
                    "arguments": {"patient_id": patient.patient_id},
                }
            ),
            _action(
                {
                    "action": "RECOMMEND",
                    "recommendation": {
                        "priority": 3,
                        "confidence": 0.6,
                        "reason_summary": "Proceeding with observed intake only.",
                        "key_evidence": ["intake complaint"],
                    },
                }
            ),
        ]
    )
    outcome = run_primary_assessment(patient, llm=mock)
    assert outcome.decision_source == DecisionSource.agent
    assert outcome.state.tool_history[0].error
    assert "unknown" in (outcome.state.tool_history[0].error or "").lower()


def test_iteration_budget_awaits_human():
    from app.agent.loop import BUDGET_EXHAUSTED_MESSAGE, MAX_ITERATIONS
    from app.agent.orchestrator import TriageAgentOrchestrator
    from app.agent.bridge import from_legacy_patient, from_legacy_vitals
    from app.agent.context import ToolContext
    from app.domain.models import TriageAgentState

    legacy = build_cohort(surge_factor=1)[0]
    domain = from_legacy_patient(legacy)
    state = TriageAgentState(
        patient_id=domain.patient_id,
        patient_context=domain.context,
        latest_vitals=from_legacy_vitals(legacy.vitals),
    )
    tools = [
        "get_patient",
        "get_latest_vitals",
        "get_vital_history",
        "get_patient_history",
        "assess_data_completeness",
    ]
    responses = [
        _action(
            {
                "action": "CALL_TOOL",
                "tool": tools[i % len(tools)],
                "arguments": {"patient_id": domain.patient_id},
            }
        )
        for i in range(MAX_ITERATIONS)
    ]
    mock = MockAgentLLM(responses)
    result = TriageAgentOrchestrator(mock, max_iterations=MAX_ITERATIONS).run(
        patient=domain,
        state=state,
        context=ToolContext(
            patients={domain.patient_id: domain},
            agent_states={domain.patient_id: state},
        ),
    )
    assert result.state.status.value == "AWAITING_HUMAN"
    assert BUDGET_EXHAUSTED_MESSAGE in result.state.uncertainty_reasons


def test_missing_information_requests_then_hitl_api():
    client = TestClient(app)
    pid = "P-009"
    detail = client.get(f"/api/patients/{pid}").json()
    assert detail["agent"] is not None
    assert detail["agent"]["status_label"]

    res = client.post(
        "/api/hitl",
        json={
            "patient_id": pid,
            "action": "request_more_information",
            "actor": "A. Nurse",
            "reason": "Need repeat SpO2 on room air.",
        },
    )
    assert res.status_code == 200
    body = res.json()
    assert body["agent"]["status_label"] in (
        "Gathering information",
        "Awaiting clinician",
    )


def test_clinician_override_logged_with_agent_fields():
    client = TestClient(app)
    pid = "P-007"
    before = client.get(f"/api/patients/{pid}").json()["row"]
    res = client.post(
        "/api/hitl",
        json={
            "patient_id": pid,
            "action": "override",
            "actor": "Dr. Lee",
            "reason": "Direct clinical assessment indicates higher urgency.",
            "active_priority": 2,
        },
    )
    assert res.status_code == 200
    row = res.json()["row"]
    assert row["acuity"] == 2
    assert row["engine_acuity"] == before["engine_acuity"]
    audit = client.get("/api/audit").json()
    hitl = [a for a in audit if a["patient_id"] == pid and a["action"].startswith("hitl_")][-1]
    assert hitl["clinician_action"] == "override"
    assert hitl["clinician_reason"]
    assert hitl["final_priority"] == 2
    assert hitl["agent_priority"] is not None or hitl["baseline_priority"] is not None


def test_hitl_modify_requires_reason():
    client = TestClient(app)
    res = client.post(
        "/api/hitl",
        json={
            "patient_id": "P-009",
            "action": "modify",
            "actor": "A. Nurse",
            "reason": "",
            "active_priority": 3,
        },
    )
    assert res.status_code == 400


def test_registry_rejects_unknown_tools_without_fabrication():
    reg = get_default_registry()
    from app.agent.context import ToolContext

    result = reg.execute("fabricate_spo2", ToolContext(), {"patient_id": "X"})
    assert result.ok is False
    assert "unknown tool" in (result.error or "")
