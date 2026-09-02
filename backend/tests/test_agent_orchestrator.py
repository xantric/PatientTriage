"""Phase 4 Gemini triage agent: mocked LLM, dynamic tools, validated actions."""

from __future__ import annotations

import json

import pytest

from app.agent.context import ToolContext
from app.agent.loop import parse_model_action
from app.agent.orchestrator import MockAgentLLM, TriageAgentOrchestrator
from app.agent.prompt import SYSTEM_PROMPT
from app.agent.registry import reset_default_registry
from app.domain.enums import AgentStatus, ArrivalMode, Sex, ToolCallStatus
from app.domain.models import Patient, PatientContext, TriageAgentState, Vitals


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_default_registry()
    yield
    reset_default_registry()


def _patient_sob(**kwargs) -> Patient:
    """Age 68, SOB, HR 118, SpO2 91, BP missing."""

    return Patient(
        patient_id=kwargs.pop("patient_id", "P001"),
        context=PatientContext(
            age_years=68,
            sex=Sex.female,
            arrival_mode=ArrivalMode.walk_in,
            chief_complaint="shortness of breath",
            history=["COPD"],
            onset_minutes=60,
            **kwargs.pop("context_extra", {}),
        ),
        **kwargs,
    )


def _state(patient: Patient, vitals: Vitals | None = None) -> TriageAgentState:
    v = vitals if vitals is not None else Vitals(heart_rate=118, spo2=91, resp_rate=28)
    return TriageAgentState(
        patient_id=patient.patient_id,
        patient_context=patient.context,
        latest_vitals=v,
    )


def _action(obj: dict) -> str:
    return json.dumps(obj)


def test_system_prompt_requires_independent_recommendation():
    assert "triage decision-support agent" in SYSTEM_PROMPT.lower()
    assert "never invent patient data" in SYSTEM_PROMPT.lower()
    assert "definitive diagnoses" in SYSTEM_PROMPT.lower()
    assert "disagree with deterministic baseline" in SYSTEM_PROMPT.lower()
    assert "clinician review" in SYSTEM_PROMPT.lower()


def test_parse_rejects_invalid_and_unknown_action():
    bad = parse_model_action("not json")
    assert bad.ok is False

    unknown = parse_model_action('{"action":"DECIDE_ESI","priority":1}')
    assert unknown.ok is False

    missing = parse_model_action('{"action":"RECOMMEND"}')
    assert missing.ok is False


def test_parse_accepts_four_actions():
    assert parse_model_action(
        _action({"action": "CALL_TOOL", "tool": "get_latest_vitals", "arguments": {}})
    ).ok
    assert parse_model_action(
        _action(
            {
                "action": "REQUEST_INFORMATION",
                "fields": ["blood_pressure"],
                "reason": "SBP missing",
            }
        )
    ).ok
    assert parse_model_action(
        _action(
            {
                "action": "RECOMMEND",
                "recommendation": {
                    "priority": 2,
                    "confidence": 0.8,
                    "reason_summary": "Hypoxia with tachypnea.",
                    "key_evidence": ["SpO2 91"],
                },
            }
        )
    ).ok
    assert parse_model_action(
        _action({"action": "ESCALATE", "reason": "Unstable and incomplete vitals."})
    ).ok


def test_dynamic_sequence_vitals_then_recommend():
    """Example path: observe → get_latest_vitals → assess completeness → recommend."""

    patient = _patient_sob()
    state = _state(patient)
    ctx = ToolContext()
    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "get_latest_vitals",
                    "arguments": {"patient_id": "P001"},
                }
            ),
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "assess_data_completeness",
                    "arguments": {"patient_id": "P001"},
                }
            ),
            _action(
                {
                    "action": "RECOMMEND",
                    "recommendation": {
                        "priority": 2,
                        "urgency": "high",
                        "care_pathway": "high-acuity / respiratory support area",
                        "monitoring_plan": "continuous SpO2; recheck BP ASAP",
                        "confidence": 0.81,
                        "reason_summary": (
                            "Age 68 with SOB, HR 118, SpO2 91; SBP missing."
                        ),
                        "key_evidence": [
                            "SpO2 91",
                            "HR 118",
                            "missing SBP",
                        ],
                        "information_gaps": ["missing SBP"],
                    },
                }
            ),
        ]
    )
    orch = TriageAgentOrchestrator(mock, max_iterations=8)
    result = orch.run(patient=patient, state=state, context=ctx)

    assert result.terminal_action == "RECOMMEND"
    assert result.state.status == AgentStatus.AWAITING_HUMAN
    assert result.state.current_recommendation is not None
    assert result.state.current_recommendation.priority == 2
    assert result.state.current_recommendation.confidence == 0.81
    tools = [c.tool_name for c in result.state.tool_history]
    assert tools == ["get_latest_vitals", "assess_data_completeness"]
    assert "Missing: blood pressure (SBP)." in " ".join(
        o.summary for o in result.state.current_observations
    )


def test_different_patient_uses_different_tool_sequence():
    """Second patient: baseline inspect then recommend (no vitals tools)."""

    patient = _patient_sob(patient_id="P002")
    state = _state(
        patient,
        Vitals(heart_rate=72, sbp=128, spo2=98, resp_rate=16, temp_c=36.8),
    )
    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "get_baseline_engine_assessment",
                    "arguments": {"patient_id": "P002"},
                }
            ),
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "get_patient_history",
                    "arguments": {"patient_id": "P002"},
                }
            ),
            _action(
                {
                    "action": "RECOMMEND",
                    "recommendation": {
                        "priority": 4,
                        "confidence": 0.7,
                        "reason_summary": "Stable vitals; low urgency complaint context.",
                        "key_evidence": ["SpO2 98", "HR 72"],
                    },
                }
            ),
        ]
    )
    result = TriageAgentOrchestrator(mock).run(
        patient=patient, state=state, context=ToolContext()
    )
    tools = [c.tool_name for c in result.state.tool_history]
    assert tools == [
        "get_baseline_engine_assessment",
        "get_patient_history",
    ]
    assert result.state.current_recommendation.priority == 4
    assert result.state.baseline_assessment is not None
    assert result.state.baseline_assessment.source == "deterministic_baseline"
    # Agent recommendation is independent; baseline stored as reference only.
    assert result.state.current_recommendation.priority is not None


def test_agent_may_disagree_with_baseline():
    patient = _patient_sob(patient_id="P003")
    state = _state(patient)
    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "get_baseline_engine_assessment",
                    "arguments": {"patient_id": "P003"},
                }
            ),
            _action(
                {
                    "action": "RECOMMEND",
                    "recommendation": {
                        "priority": 1,
                        "confidence": 0.6,
                        "reason_summary": (
                            "Independent judgment: severe hypoxia risk; "
                            "disagree with less urgent baseline if present."
                        ),
                        "key_evidence": ["SpO2 91", "HR 118"],
                    },
                }
            ),
        ]
    )
    result = TriageAgentOrchestrator(mock).run(
        patient=patient, state=state, context=ToolContext()
    )
    assert result.state.baseline_assessment is not None
    assert result.state.current_recommendation.priority == 1
    # Live recommendation is the agent's, not an auto-copy of baseline.
    assert result.state.baseline_assessment.priority != 1 or (
        result.state.current_recommendation.reason_summary.startswith("Independent")
    )


def test_unknown_tool_never_executed_then_recover():
    patient = _patient_sob(patient_id="P004")
    state = _state(patient)
    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "get_final_triage",
                    "arguments": {"patient_id": "P004"},
                }
            ),
            _action(
                {
                    "action": "RECOMMEND",
                    "recommendation": {
                        "priority": 2,
                        "confidence": 0.75,
                        "reason_summary": "Proceeding without forbidden tool.",
                        "key_evidence": ["SpO2 91"],
                    },
                }
            ),
        ]
    )
    result = TriageAgentOrchestrator(mock).run(
        patient=patient, state=state, context=ToolContext()
    )
    assert result.terminal_action == "RECOMMEND"
    bad = result.state.tool_history[0]
    assert bad.tool_name == "get_final_triage"
    assert bad.status == ToolCallStatus.error
    assert "unknown tool" in (bad.error or "")


def test_request_information_stops_without_hitl():
    patient = _patient_sob(patient_id="P005")
    state = _state(patient)
    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "REQUEST_INFORMATION",
                    "fields": ["blood_pressure"],
                    "reason": "Cannot safely stratify without SBP.",
                }
            )
        ]
    )
    result = TriageAgentOrchestrator(mock).run(
        patient=patient, state=state, context=ToolContext()
    )
    assert result.terminal_action == "REQUEST_INFORMATION"
    assert result.state.status == AgentStatus.AWAITING_HUMAN
    assert result.state.current_recommendation is None
    assert any("blood_pressure" in g for g in result.state.information_gaps)


def test_escalate_stops():
    patient = _patient_sob(patient_id="P006")
    state = _state(patient)
    mock = MockAgentLLM(
        [_action({"action": "ESCALATE", "reason": "Material uncertainty after review."})]
    )
    result = TriageAgentOrchestrator(mock).run(
        patient=patient, state=state, context=ToolContext()
    )
    assert result.terminal_action == "ESCALATE"
    assert result.state.status == AgentStatus.ESCALATED


def test_llm_failure_marks_failed():
    patient = _patient_sob(patient_id="P007")
    state = _state(patient)
    mock = MockAgentLLM(["unused"], fail_after=0)
    result = TriageAgentOrchestrator(mock).run(
        patient=patient, state=state, context=ToolContext()
    )
    assert result.state.status == AgentStatus.FAILED
    assert result.stopped_reason == "llm_failure"


def test_invalid_json_retries_then_fails():
    patient = _patient_sob(patient_id="P008")
    state = _state(patient)
    mock = MockAgentLLM(["nope", "still bad", "again bad"])
    orch = TriageAgentOrchestrator(mock, max_parse_retries=2)
    result = orch.run(patient=patient, state=state, context=ToolContext())
    assert result.state.status == AgentStatus.FAILED
    assert result.stopped_reason == "parse_failure"


def test_max_iterations_awaits_human():
    patient = _patient_sob(patient_id="P009")
    state = _state(patient)
    # Always call a tool; never terminate. Duplicates after first are skipped.
    forever = [
        _action(
            {
                "action": "CALL_TOOL",
                "tool": "get_patient",
                "arguments": {"patient_id": "P009"},
            }
        ),
        _action(
            {
                "action": "CALL_TOOL",
                "tool": "get_latest_vitals",
                "arguments": {"patient_id": "P009"},
            }
        ),
        _action(
            {
                "action": "CALL_TOOL",
                "tool": "get_patient_history",
                "arguments": {"patient_id": "P009"},
            }
        ),
    ]
    mock = MockAgentLLM(forever)
    result = TriageAgentOrchestrator(mock, max_iterations=3).run(
        patient=patient, state=state, context=ToolContext()
    )
    assert result.state.status == AgentStatus.AWAITING_HUMAN
    assert result.state.human_review_required is True
    assert result.stopped_reason == "max_iterations"
    assert any(
        "reasoning budget" in r for r in result.state.uncertainty_reasons
    )


def test_telemetry_records_triage_agent_calls():
    patient = _patient_sob(patient_id="P010")
    state = _state(patient)
    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "RECOMMEND",
                    "recommendation": {
                        "priority": 3,
                        "confidence": 0.5,
                        "reason_summary": "Limited data; provisional.",
                        "key_evidence": ["HR 118"],
                    },
                }
            )
        ]
    )
    orch = TriageAgentOrchestrator(mock)
    orch.run(patient=patient, state=state, context=ToolContext())
    assert len(orch.telemetry) == 1
    assert orch.telemetry[0].task == "triage_agent"
    assert orch.telemetry[0].ok is True


def test_not_a_fixed_interpreter_adjudicator_pipeline():
    """Orchestrator must not force baseline before recommend."""

    patient = _patient_sob(patient_id="P011")
    state = _state(patient)
    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "RECOMMEND",
                    "recommendation": {
                        "priority": 2,
                        "confidence": 0.77,
                        "reason_summary": "Direct recommend from observation packet.",
                        "key_evidence": ["SpO2 91", "HR 118"],
                    },
                }
            )
        ]
    )
    result = TriageAgentOrchestrator(mock).run(
        patient=patient, state=state, context=ToolContext()
    )
    assert result.state.tool_history == []
    assert result.state.baseline_assessment is None
    assert result.state.current_recommendation.priority == 2
