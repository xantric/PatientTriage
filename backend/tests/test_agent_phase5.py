"""Phase 5: bounded agent loop, baseline comparison, and HITL."""

from __future__ import annotations

import json

import pytest

from app.agent.context import ToolContext
from app.agent.hitl import ClinicianDecisionInput, HitlError, apply_clinician_decision
from app.agent.loop import BUDGET_EXHAUSTED_MESSAGE, MAX_ITERATIONS
from app.agent.orchestrator import MockAgentLLM, TriageAgentOrchestrator
from app.agent.registry import reset_default_registry
from app.domain.enums import AgentStatus, ArrivalMode, ClinicianAction, Sex, ToolCallStatus
from app.domain.models import Patient, PatientContext, TriageAgentState, Vitals


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_default_registry()
    yield
    reset_default_registry()


def _action(obj: dict) -> str:
    return json.dumps(obj)


def _patient(pid: str = "P-200", **ctx) -> Patient:
    base = dict(
        age_years=68,
        sex=Sex.female,
        arrival_mode=ArrivalMode.walk_in,
        chief_complaint="shortness of breath",
        history=["COPD"],
        onset_minutes=45,
    )
    base.update(ctx)
    return Patient(patient_id=pid, context=PatientContext(**base))


def _state(patient: Patient, vitals: Vitals | None = None) -> TriageAgentState:
    return TriageAgentState(
        patient_id=patient.patient_id,
        patient_context=patient.context,
        latest_vitals=vitals
        or Vitals(heart_rate=118, spo2=91, resp_rate=28),
    )


def _recommend(priority: int = 2, **extra) -> str:
    body = {
        "priority": priority,
        "urgency": "high",
        "care_pathway": "high-acuity area",
        "monitoring_plan": "continuous SpO2",
        "confidence": 0.81,
        "reason_summary": "Hypoxia with tachycardia; SBP missing.",
        "key_evidence": ["SpO2 91", "HR 118"],
    }
    body.update(extra)
    return _action({"action": "RECOMMEND", "recommendation": body})


def test_max_iterations_constant_is_five():
    assert MAX_ITERATIONS == 5


def test_multi_step_tool_use_and_recommendation():
    patient = _patient("P-201")
    state = _state(patient)
    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "get_latest_vitals",
                    "arguments": {"patient_id": "P-201"},
                }
            ),
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "assess_data_completeness",
                    "arguments": {"patient_id": "P-201"},
                }
            ),
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "calculate_shock_index",
                    "arguments": {"patient_id": "P-201"},
                }
            ),
            _recommend(2),
        ]
    )
    result = TriageAgentOrchestrator(mock).run(
        patient=patient, state=state, context=ToolContext()
    )
    assert result.terminal_action == "RECOMMEND"
    assert result.state.status == AgentStatus.AWAITING_HUMAN
    assert result.state.human_review_required is True
    tools = [c.tool_name for c in result.state.tool_history]
    assert tools == [
        "get_latest_vitals",
        "assess_data_completeness",
        "calculate_shock_index",
    ]
    rec = result.state.current_recommendation
    assert rec is not None
    assert rec.priority == 2
    assert rec.urgency == "high"
    assert rec.care_pathway
    assert rec.monitoring_plan
    assert rec.confidence == 0.81
    assert rec.key_evidence


def test_tool_selection_differs_by_script():
    patient = _patient("P-202")
    state = _state(patient, Vitals(heart_rate=80, sbp=120, spo2=98))
    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "get_patient_history",
                    "arguments": {"patient_id": "P-202"},
                }
            ),
            _recommend(4, confidence=0.7, reason_summary="Stable vitals."),
        ]
    )
    result = TriageAgentOrchestrator(mock).run(
        patient=patient, state=state, context=ToolContext()
    )
    assert [c.tool_name for c in result.state.tool_history] == ["get_patient_history"]


def test_information_gathering_awaits_human():
    patient = _patient("P-203")
    state = _state(patient)
    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "REQUEST_INFORMATION",
                    "fields": ["blood_pressure"],
                    "reason": "Need SBP before recommending.",
                }
            )
        ]
    )
    result = TriageAgentOrchestrator(mock).run(
        patient=patient, state=state, context=ToolContext()
    )
    assert result.terminal_action == "REQUEST_INFORMATION"
    assert result.state.status == AgentStatus.AWAITING_HUMAN
    assert any("blood_pressure" in g for g in result.state.information_gaps)


def test_escalation():
    patient = _patient("P-204")
    state = _state(patient)
    mock = MockAgentLLM(
        [_action({"action": "ESCALATE", "reason": "Unsafe uncertainty."})]
    )
    result = TriageAgentOrchestrator(mock).run(
        patient=patient, state=state, context=ToolContext()
    )
    assert result.terminal_action == "ESCALATE"
    assert result.state.status == AgentStatus.ESCALATED


def test_iteration_limit_awaits_human_with_budget_message():
    patient = _patient("P-205")
    state = _state(patient)
    responses = [
        _action(
            {
                "action": "CALL_TOOL",
                "tool": name,
                "arguments": {"patient_id": "P-205"},
            }
        )
        for name in (
            "get_patient",
            "get_latest_vitals",
            "get_vital_history",
            "get_patient_history",
            "assess_data_completeness",
        )
    ]
    mock = MockAgentLLM(responses)
    result = TriageAgentOrchestrator(mock, max_iterations=MAX_ITERATIONS).run(
        patient=patient, state=state, context=ToolContext()
    )
    assert result.stopped_reason == "max_iterations"
    assert result.state.status == AgentStatus.AWAITING_HUMAN
    assert result.state.human_review_required is True
    assert BUDGET_EXHAUSTED_MESSAGE in result.state.uncertainty_reasons
    assert result.iterations == MAX_ITERATIONS


def test_duplicate_tool_prevention():
    patient = _patient("P-206")
    state = _state(patient)
    same = _action(
        {
            "action": "CALL_TOOL",
            "tool": "get_latest_vitals",
            "arguments": {"patient_id": "P-206"},
        }
    )
    mock = MockAgentLLM([same, same, _recommend(2)])
    result = TriageAgentOrchestrator(mock).run(
        patient=patient, state=state, context=ToolContext()
    )
    statuses = [c.status for c in result.state.tool_history]
    assert statuses[0] == ToolCallStatus.ok
    assert statuses[1] == ToolCallStatus.skipped
    assert result.state.tool_history[1].error == "duplicate_tool_call"
    assert result.terminal_action == "RECOMMEND"


def test_invalid_tool_rejected():
    patient = _patient("P-207")
    state = _state(patient)
    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "get_final_triage",
                    "arguments": {"patient_id": "P-207"},
                }
            ),
            _recommend(2),
        ]
    )
    result = TriageAgentOrchestrator(mock).run(
        patient=patient, state=state, context=ToolContext()
    )
    assert result.state.tool_history[0].status == ToolCallStatus.error
    assert "unknown tool" in (result.state.tool_history[0].error or "")


def test_malformed_response_fails_after_retries():
    patient = _patient("P-208")
    state = _state(patient)
    mock = MockAgentLLM(["{", "not-json", '{"action":"NOPE"}'])
    result = TriageAgentOrchestrator(mock, max_parse_retries=2).run(
        patient=patient, state=state, context=ToolContext()
    )
    assert result.state.status == AgentStatus.FAILED
    assert result.stopped_reason == "parse_failure"


def test_baseline_disagreement_stored_without_overwrite():
    patient = _patient("P-209")
    state = _state(patient)
    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "get_baseline_engine_assessment",
                    "arguments": {"patient_id": "P-209"},
                }
            ),
            _recommend(1),  # force disagreement likely vs baseline
        ]
    )
    result = TriageAgentOrchestrator(mock).run(
        patient=patient, state=state, context=ToolContext()
    )
    assert result.state.baseline_assessment is not None
    assert result.state.current_recommendation is not None
    assert result.state.current_recommendation.priority == 1
    baseline_p = result.state.baseline_assessment.priority
    # Agent rec unchanged by baseline value
    assert result.state.current_recommendation.priority == 1
    assert result.state.baseline_assessment.priority == baseline_p

    comparison = result.state.baseline_comparison
    assert comparison is not None
    assert comparison.agent_priority == 1
    assert comparison.baseline_priority == baseline_p
    if baseline_p != 1:
        assert comparison.agrees is False
        assert comparison.disagreement_reason
        assert "differs" in comparison.disagreement_reason.lower()


def _run_to_awaiting(pid: str, priority: int = 2) -> TriageAgentState:
    patient = _patient(pid)
    state = _state(patient)
    mock = MockAgentLLM([_recommend(priority)])
    result = TriageAgentOrchestrator(mock).run(
        patient=patient, state=state, context=ToolContext()
    )
    assert result.state.status == AgentStatus.AWAITING_HUMAN
    return result.state


def test_hitl_accept_preserves_agent_recommendation():
    state = _run_to_awaiting("P-210", priority=2)
    agent_before = state.current_recommendation.model_dump()
    decision = apply_clinician_decision(
        state,
        ClinicianDecisionInput(
            action=ClinicianAction.accept,
            actor="Dr. Lee",
            reason="Agree with agent assessment.",
        ),
    )
    assert decision.action == ClinicianAction.accept
    assert decision.active_priority == 2
    assert state.status == AgentStatus.COMPLETED
    assert state.current_recommendation.model_dump() == agent_before
    assert state.clinician_decision is not None
    assert state.clinician_decision.active_priority == 2


def test_hitl_modify_requires_reason_and_keeps_agent_rec():
    state = _run_to_awaiting("P-211", priority=2)
    agent_before = state.current_recommendation.model_dump()

    with pytest.raises(HitlError):
        apply_clinician_decision(
            state,
            ClinicianDecisionInput(
                action=ClinicianAction.modify,
                actor="Dr. Lee",
                reason="",
                active_priority=3,
            ),
        )

    decision = apply_clinician_decision(
        state,
        ClinicianDecisionInput(
            action=ClinicianAction.modify,
            actor="Dr. Lee",
            reason="Direct clinical assessment indicates patient is stable.",
            active_priority=3,
        ),
    )
    assert decision.active_priority == 3
    assert state.current_recommendation.priority == 2
    assert state.current_recommendation.model_dump() == agent_before
    assert state.clinician_decision.reason.startswith("Direct clinical")
    assert state.status == AgentStatus.COMPLETED


def test_hitl_override_requires_reason():
    state = _run_to_awaiting("P-212", priority=2)
    agent_before = state.current_recommendation.model_dump()

    with pytest.raises(HitlError):
        apply_clinician_decision(
            state,
            ClinicianDecisionInput(
                action=ClinicianAction.override,
                actor="Dr. Lee",
                active_priority=4,
            ),
        )

    apply_clinician_decision(
        state,
        ClinicianDecisionInput(
            action=ClinicianAction.override,
            actor="Dr. Lee",
            reason="Bedside exam overrides hypoxia concern for now.",
            active_priority=4,
        ),
    )
    assert state.current_recommendation.model_dump() == agent_before
    assert state.clinician_decision.action == ClinicianAction.override
    assert state.clinician_decision.active_priority == 4
    assert state.current_recommendation.priority == 2


def test_hitl_request_more_information():
    state = _run_to_awaiting("P-213", priority=2)
    agent_before = state.current_recommendation.model_dump()
    apply_clinician_decision(
        state,
        ClinicianDecisionInput(
            action=ClinicianAction.request_more_information,
            actor="Dr. Lee",
            reason="Need repeat SpO2 on room air.",
            info_provided=[],
        ),
    )
    assert state.status == AgentStatus.GATHERING_INFORMATION
    assert state.human_review_required is True
    assert state.current_recommendation.model_dump() == agent_before


def test_hitl_clinician_escalate():
    state = _run_to_awaiting("P-214", priority=2)
    apply_clinician_decision(
        state,
        ClinicianDecisionInput(
            action=ClinicianAction.escalate,
            actor="Dr. Lee",
            reason="Needs senior review.",
        ),
    )
    assert state.status == AgentStatus.ESCALATED
    assert state.current_recommendation.priority == 2
