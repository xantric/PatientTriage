"""Phase 6: event-driven Watcher, TriageEvents, agent reassessment."""

from __future__ import annotations

import json

import pytest

from app.agent.context import ToolContext
from app.agent.event_watcher import (
    EventDrivenWatcher,
    _vitals_worsened,
    simulate_event_driven,
)
from app.agent.events import apply_triage_event, raise_monitoring_floor
from app.agent.orchestrator import MockAgentLLM, TriageAgentOrchestrator
from app.agent.reassessment import ReassessmentBus
from app.agent.registry import reset_default_registry
from app.domain.enums import (
    AgentStatus,
    ArrivalMode,
    Sex,
    TriageEventType,
)
from app.domain.models import (
    AgentRecommendation,
    Patient,
    PatientContext,
    TriageAgentState,
    TriageEvent,
    Vitals,
)
from app.engine.watcher import simulate as legacy_simulate


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_default_registry()
    yield
    reset_default_registry()


def _action(obj: dict) -> str:
    return json.dumps(obj)


def _recommend(priority: int, summary: str = "Reassessed after event.") -> str:
    return _action(
        {
            "action": "RECOMMEND",
            "recommendation": {
                "priority": priority,
                "urgency": "high" if priority <= 2 else "semi-urgent",
                "care_pathway": "acute area",
                "monitoring_plan": "frequent re-check",
                "confidence": 0.8,
                "reason_summary": summary,
                "key_evidence": ["watcher event"],
            },
        }
    )


def test_triage_event_types_exist():
    names = {t.name for t in TriageEventType}
    assert names == {
        "PATIENT_ARRIVAL",
        "NEW_VITALS",
        "VITAL_TREND_CHANGE",
        "WAIT_TIMEOUT",
        "DETERIORATION_DETECTED",
        "NEW_INFORMATION",
        "CLINICIAN_DECISION",
    }


def test_vitals_worsened_spo2_hr_example():
    before = Vitals(heart_rate=98, spo2=96)
    after = Vitals(heart_rate=118, spo2=92)
    bad, notes = _vitals_worsened(before, after)
    assert bad is True
    assert any("SpO2" in n for n in notes)
    assert any("HR" in n for n in notes)


def test_deterioration_event_survives_softer_agent_recommendation():
    state = TriageAgentState(
        patient_id="P-X",
        patient_context=PatientContext(age_years=68, chief_complaint="SOB"),
        latest_vitals=Vitals(heart_rate=98, spo2=96),
        monitoring_priority_floor=3,
    )
    det = TriageEvent(
        event_type=TriageEventType.DETERIORATION_DETECTED,
        patient_id="P-X",
        time_min=40,
        summary="Deterioration detected: SpO2 96 → 92; HR 98 → 118",
        payload={"monitoring_priority": 2},
        source="watcher",
        triggers_reassessment=True,
    )
    apply_triage_event(state, det)
    det_id = det.event_id
    assert len(state.deterioration_events()) == 1
    assert state.monitoring_priority_floor == 2

    # Agent recommends softer priority 4; floor must not fall; event must remain.
    state.previous_recommendation = state.current_recommendation
    state.current_recommendation = AgentRecommendation(
        priority=4,
        confidence=0.6,
        reason_summary="Looks calmer at rest.",
    )
    raise_monitoring_floor(state, 4)
    assert state.monitoring_priority_floor == 2
    assert state.deterioration_events()[0].event_id == det_id
    assert state.deterioration_events()[0].summary.startswith("Deterioration")


def test_reassessment_bus_invokes_agent_on_deterioration_only():
    patient = Patient(
        patient_id="P-D1",
        context=PatientContext(
            age_years=55,
            sex=Sex.female,
            arrival_mode=ArrivalMode.walk_in,
            chief_complaint="shortness of breath",
        ),
    )
    state = TriageAgentState(
        patient_id="P-D1",
        patient_context=patient.context,
        latest_vitals=Vitals(heart_rate=98, spo2=96, sbp=120),
        monitoring_priority_floor=3,
    )
    ctx = ToolContext(patients={patient.patient_id: patient}, agent_states={})
    ctx.agent_states[patient.patient_id] = state

    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "get_vital_history",
                    "arguments": {"patient_id": "P-D1"},
                }
            ),
            _recommend(2, "Worsening SpO2 and HR trend."),
        ]
    )
    bus = ReassessmentBus(llm=mock, invoke_on_arrival=False)

    # Stable NEW_VITALS should not invoke LLM.
    stable = TriageEvent(
        event_type=TriageEventType.NEW_VITALS,
        patient_id="P-D1",
        time_min=10,
        summary="Routine re-check.",
        payload={"vitals": Vitals(heart_rate=99, spo2=96, sbp=120).model_dump()},
        source="watcher",
        triggers_reassessment=False,
    )
    bus.handle(event=stable, patient=patient, state=state, context=ctx)
    assert bus.llm_invocations == 0

    det = TriageEvent(
        event_type=TriageEventType.DETERIORATION_DETECTED,
        patient_id="P-D1",
        time_min=40,
        summary="Deterioration detected: SpO2 96 → 92; HR 98 → 118",
        payload={
            "monitoring_priority": 2,
            "vitals_after": Vitals(heart_rate=118, spo2=92, sbp=110).model_dump(),
        },
        source="watcher",
        triggers_reassessment=True,
    )
    result = bus.handle(event=det, patient=patient, state=state, context=ctx)
    assert bus.llm_invocations == 1
    assert result is not None
    assert result.state.current_recommendation is not None
    assert result.state.current_recommendation.priority == 2
    assert result.state.status == AgentStatus.AWAITING_HUMAN
    assert len(state.deterioration_events()) == 1


def test_event_watcher_never_ratchets_down():
    for factor in (1, 3):
        report = simulate_event_driven(surge_factor=factor)
        assert report.down_ratchets == 0


def test_event_watcher_detects_p011_deterioration():
    report = simulate_event_driven(surge_factor=1)
    dets = [
        e
        for e in report.triage_events
        if e.patient_id == "P-011"
        and e.event_type == TriageEventType.DETERIORATION_DETECTED
    ]
    assert dets, "P-011 sepsis wait should emit DETERIORATION_DETECTED"
    state = report.states["P-011"]
    assert state.deterioration_events()
    # Historical events remain after any later activity
    n = len(state.deterioration_events())
    state.current_recommendation = AgentRecommendation(
        priority=5, confidence=0.5, reason_summary="Should not erase history."
    )
    assert len(state.deterioration_events()) == n


def test_surge_preserves_load_pressure():
    normal = simulate_event_driven(surge_factor=1)
    surge = simulate_event_driven(surge_factor=3)
    assert surge.total_patients > normal.total_patients
    assert surge.peak_waiting >= normal.peak_waiting
    assert surge.peak_unsafe_wait_min >= normal.peak_unsafe_wait_min
    assert surge.total_backstops >= normal.total_backstops


def test_surge_sim_does_not_call_llm_every_tick():
    """Without a bus, zero LLM calls. With bus and no arrival invoke, calls << ticks."""

    plain = simulate_event_driven(surge_factor=1)
    assert plain.llm_invocations == 0
    assert plain.ticks > 10

    # Bus with exhausted mock: only meaningful events try to call; failures stop quietly?
    # Use a mock that always recommends so invocations succeed.
    responses = [_recommend(3) for _ in range(200)]
    mock = MockAgentLLM(responses)
    bus = ReassessmentBus(llm=mock, invoke_on_arrival=False)
    report = EventDrivenWatcher(bus=bus).simulate(surge_factor=1)
    assert report.llm_invocations < report.ticks
    # Meaningful events only: deteriorations + timeouts + trend changes, not every NEW_VITALS
    new_vitals = sum(
        1 for e in report.triage_events if e.event_type == TriageEventType.NEW_VITALS
    )
    assert report.llm_invocations <= new_vitals
    assert report.llm_invocations < new_vitals or new_vitals == 0


def test_legacy_watcher_still_works():
    report = legacy_simulate(surge_factor=1)
    assert report.down_ratchets == 0
    assert report.total_patients > 0


def test_full_path_deterioration_to_hitl_status():
    patient = Patient(
        patient_id="P-FLOW",
        context=PatientContext(age_years=68, chief_complaint="SOB"),
    )
    state = TriageAgentState(
        patient_id="P-FLOW",
        patient_context=patient.context,
        latest_vitals=Vitals(heart_rate=98, spo2=96),
        monitoring_priority_floor=3,
        vital_history=[],
    )
    ctx = ToolContext(
        patients={patient.patient_id: patient},
        agent_states={patient.patient_id: state},
    )
    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "calculate_vital_trends",
                    "arguments": {"patient_id": "P-FLOW"},
                }
            ),
            _recommend(2, "Trend worsening; recommend higher urgency."),
        ]
    )
    bus = ReassessmentBus(
        orchestrator=TriageAgentOrchestrator(mock),
        invoke_on_arrival=False,
    )
    # Seed history like the example.
    apply_triage_event(
        state,
        TriageEvent(
            event_type=TriageEventType.NEW_VITALS,
            patient_id="P-FLOW",
            time_min=0,
            summary="Intake vitals.",
            payload={"vitals": Vitals(heart_rate=98, spo2=96).model_dump()},
            source="watcher",
        ),
    )
    result = bus.handle(
        event=TriageEvent(
            event_type=TriageEventType.DETERIORATION_DETECTED,
            patient_id="P-FLOW",
            time_min=30,
            summary="Deterioration detected: SpO2 96 → 92; HR 98 → 118",
            payload={
                "monitoring_priority": 2,
                "vitals_after": Vitals(heart_rate=118, spo2=92).model_dump(),
            },
            source="watcher",
            triggers_reassessment=True,
        ),
        patient=patient,
        state=state,
        context=ctx,
    )
    assert result is not None
    assert state.status == AgentStatus.AWAITING_HUMAN
    assert state.human_review_required is True
    assert state.current_recommendation.priority == 2
    assert state.deterioration_events()
