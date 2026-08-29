"""Tests for Phase 2 agent-first domain models.

No Gemini orchestration. These lock the contracts: operational summaries only,
optional agent priority, and no deterministic priority override on state.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain import (
    AgentAssessment,
    AgentObservation,
    AgentRecommendation,
    AgentStatus,
    AgentToolCall,
    AuditAction,
    AuditEvent,
    BaselineAssessment,
    BaselineComparison,
    ClinicianAction,
    ClinicianDecision,
    ConfidenceBand,
    DerivedMetrics,
    EventKind,
    ObservationSource,
    Patient,
    PatientContext,
    PatientEvent,
    TriageAgentState,
    VitalObservation,
    Vitals,
    WatcherQueueStatus,
    WatcherState,
)


def _context(**kwargs) -> PatientContext:
    base = dict(age_years=54, chief_complaint="found collapsed, not responding")
    base.update(kwargs)
    return PatientContext(**base)


def _state(**kwargs) -> TriageAgentState:
    base = dict(patient_id="P-001", patient_context=_context())
    base.update(kwargs)
    return TriageAgentState(**base)


def test_vitals_present_fields():
    v = Vitals(heart_rate=138, spo2=84)
    assert set(v.present_fields()) == {"heart_rate", "spo2"}
    assert Vitals().is_empty()


def test_vital_observation_rejects_long_note():
    with pytest.raises(ValidationError):
        VitalObservation(
            recorded_at_min=0,
            vitals=Vitals(spo2=91),
            note="x" * 281,
        )


def test_patient_and_event_round_trip():
    patient = Patient(patient_id="P-001", context=_context())
    event = PatientEvent(
        patient_id=patient.patient_id,
        time_min=0,
        kind=EventKind.arrival,
        summary="Patient arrived by ambulance.",
    )
    assert event.kind == EventKind.arrival
    assert "ambulance" in event.summary.lower() or "arrived" in event.summary.lower()


def test_agent_observation_is_concise_operational_fact():
    obs = AgentObservation(
        summary="SpO2 declined from 96 to 91 over 15 minutes.",
        source=ObservationSource.watcher,
    )
    assert "declined" in obs.summary
    with pytest.raises(ValidationError):
        AgentObservation(summary="")


def test_agent_recommendation_priority_is_optional():
    without = AgentRecommendation(
        confidence=0.4,
        reason_summary="Blood pressure is missing.",
        information_gaps=["Blood pressure is missing."],
        human_review_required=True,
    )
    assert without.priority is None

    with_priority = AgentRecommendation(
        priority=2,
        urgency="high",
        care_pathway="high-acuity area",
        monitoring_plan="re-check within 10 minutes",
        confidence=0.72,
        confidence_band=ConfidenceBand.medium,
        reason_summary="Hypoxia and tachycardia against adult band.",
        key_evidence=["SpO2 91%", "HR 128"],
        information_gaps=[],
        human_review_required=True,
    )
    assert with_priority.priority == 2


def test_agent_recommendation_priority_bounds():
    with pytest.raises(ValidationError):
        AgentRecommendation(priority=0, confidence=0.5, reason_summary="bad")
    with pytest.raises(ValidationError):
        AgentRecommendation(priority=6, confidence=0.5, reason_summary="bad")


def test_triage_agent_state_has_required_fields_and_statuses():
    state = _state(
        latest_vitals=Vitals(spo2=91, heart_rate=128),
        vital_history=[
            VitalObservation(
                recorded_at_min=0,
                vitals=Vitals(spo2=96, heart_rate=110),
                note="Initial vitals at arrival.",
            ),
            VitalObservation(
                recorded_at_min=15,
                vitals=Vitals(spo2=91, heart_rate=128),
                note="SpO2 declined from 96 to 91 over 15 minutes.",
            ),
        ],
        current_observations=[
            AgentObservation(summary="SpO2 declined from 96 to 91 over 15 minutes."),
            AgentObservation(summary="Blood pressure is missing."),
        ],
        information_gaps=["Blood pressure is missing."],
        derived_metrics=DerivedMetrics(age_band="adult", shock_index=None),
        agent_confidence=0.55,
        uncertainty_reasons=["Blood pressure is missing."],
        tool_history=[
            AgentToolCall(
                tool_name="interpret",
                arguments={"patient_id": "P-001"},
                result_summary="Age band adult; SpO2 critical.",
                iteration=1,
            )
        ],
        retrieved_context=["Adult SpO2 critical threshold is 90%."],
        status=AgentStatus.GATHERING_INFORMATION,
        iteration_count=1,
    )

    assert state.patient_id == "P-001"
    assert state.human_review_required is True
    assert state.current_recommendation is None
    assert state.clinician_decision is None
    # No deterministic priority field on the state itself.
    assert not hasattr(state, "priority")
    assert not hasattr(state, "effective_priority")
    assert not hasattr(state, "deterministic_priority")


def test_all_agent_statuses_are_defined():
    names = {s.name for s in AgentStatus}
    assert names == {
        "INITIALIZING",
        "OBSERVING",
        "GATHERING_INFORMATION",
        "REASONING",
        "RECOMMENDING",
        "AWAITING_HUMAN",
        "ESCALATED",
        "COMPLETED",
        "FAILED",
    }


def test_recommendation_and_baseline_do_not_override_each_other():
    rec = AgentRecommendation(
        priority=2,
        confidence=0.8,
        reason_summary="Agent proposes ESI 2 for hypoxia trend.",
        human_review_required=True,
    )
    baseline = BaselineAssessment(
        priority=3,
        summary="Baseline reference scored provisional 3.",
        drivers=["2 expected resources"],
    )
    comparison = BaselineComparison(
        baseline_priority=3,
        agent_priority=2,
        agrees=False,
        delta=-1,
        summary="Agent more urgent than baseline by one level.",
    )
    state = _state(
        current_recommendation=rec,
        previous_recommendation=None,
        baseline_assessment=baseline,
        baseline_comparison=comparison,
        status=AgentStatus.AWAITING_HUMAN,
    )
    # Agent recommendation remains the agent priority; baseline is reference only.
    assert state.current_recommendation is not None
    assert state.current_recommendation.priority == 2
    assert state.baseline_assessment is not None
    assert state.baseline_assessment.priority == 3


def test_clinician_decision_is_final_authority_fields():
    decision = ClinicianDecision(
        patient_id="P-001",
        action=ClinicianAction.modify,
        actor="A. Nurse",
        actor_role="triage nurse",
        reason="Still hypoxic on room air after re-check.",
        active_priority=1,
        related_recommendation_id="rec-test",
    )
    state = _state(
        current_recommendation=AgentRecommendation(
            priority=2, confidence=0.7, reason_summary="Proposed 2.", human_review_required=True
        ),
        clinician_decision=decision,
        status=AgentStatus.COMPLETED,
        human_review_required=False,
    )
    assert state.clinician_decision is not None
    assert state.clinician_decision.active_priority == 1
    assert state.clinician_decision.action == ClinicianAction.modify


def test_watcher_state_defaults_and_queue_status():
    ws = WatcherState(
        queue_status=WatcherQueueStatus.waiting,
        arrival_min=35,
        last_contact_min=35,
        next_due_min=65,
        overdue=True,
        unsafe_wait_min=5,
        last_event_summary="Unsafe wait past 30 minute limit.",
    )
    state = _state(watcher_state=ws)
    assert state.watcher_state.overdue is True
    assert state.watcher_state.ratchet_count == 0


def test_audit_event_and_assessment_bundle():
    state = _state(status=AgentStatus.OBSERVING, assessment_id="as-demo")
    assessment = AgentAssessment(
        assessment_id="as-demo",
        patient_id="P-001",
        status=AgentStatus.OBSERVING,
        state=state,
    )
    event = AuditEvent(
        timestamp_utc="2026-08-29T10:00:00+00:00",
        patient_id="P-001",
        assessment_id=assessment.assessment_id,
        action=AuditAction.assessment_started,
        summary="Assessment started for P-001.",
    )
    assert assessment.state.status == AgentStatus.OBSERVING
    assert event.action == AuditAction.assessment_started


def test_operational_summaries_exclude_empty_recommendation():
    state = _state(
        current_observations=[AgentObservation(summary="Blood pressure is missing.")],
        information_gaps=["Blood pressure is missing."],
        uncertainty_reasons=["Incomplete vitals."],
    )
    summaries = state.operational_summaries()
    assert "Blood pressure is missing." in summaries
    assert "Incomplete vitals." in summaries


def test_state_json_round_trip():
    state = _state(
        status=AgentStatus.REASONING,
        iteration_count=2,
        current_recommendation=AgentRecommendation(
            priority=2,
            urgency="high",
            care_pathway="high-acuity area",
            monitoring_plan="obs every 10 min",
            confidence=0.66,
            reason_summary="SpO2 declined from 96 to 91 over 15 minutes.",
            key_evidence=["SpO2 trend"],
            information_gaps=["Blood pressure is missing."],
            human_review_required=True,
        ),
    )
    restored = TriageAgentState.model_validate_json(state.model_dump_json())
    assert restored.status == AgentStatus.REASONING
    assert restored.current_recommendation is not None
    assert restored.current_recommendation.priority == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
