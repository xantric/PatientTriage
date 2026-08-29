"""Phase 3 agent tools: registry safety, evidence-only outputs, baseline bridge."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from app.agent.bridge import to_legacy_patient
from app.agent.context import ToolContext
from app.agent.registry import ToolResult, get_default_registry, reset_default_registry
from app.agent.tools import FORBIDDEN_TOOL_NAMES, BASELINE_SOURCE, build_registry
from app.domain.enums import (
    AgentStatus,
    ArrivalMode,
    ObservationSource,
    Responsiveness,
    Sex,
    WatcherQueueStatus,
)
from app.domain.models import (
    AgentRecommendation,
    Patient,
    PatientContext,
    TriageAgentState,
    VitalObservation,
    Vitals,
    WatcherState,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_default_registry()
    yield
    reset_default_registry()


def _patient(**kwargs) -> Patient:
    ctx_kwargs = dict(
        age_years=54,
        sex=Sex.female,
        arrival_mode=ArrivalMode.ambulance,
        chief_complaint="chest pain",
        history=["diabetes", "hypertension"],
        medications=["metformin"],
        allergies=["penicillin"],
        onset_minutes=40,
        responsiveness=Responsiveness.alert,
        has_prior_record=True,
    )
    overrides = kwargs.pop("context", {})
    ctx_kwargs.update(overrides)
    return Patient(
        patient_id=kwargs.pop("patient_id", "P-100"),
        context=PatientContext(**ctx_kwargs),
        arrival_epoch_min=kwargs.pop("arrival_epoch_min", 0),
        **kwargs,
    )


def _state(patient: Patient, **kwargs) -> TriageAgentState:
    vitals = kwargs.pop(
        "latest_vitals",
        Vitals(heart_rate=110, resp_rate=22, sbp=90, spo2=94, temp_c=37.2),
    )
    history = kwargs.pop(
        "vital_history",
        [
            VitalObservation(
                recorded_at_min=0,
                vitals=Vitals(heart_rate=96, sbp=100, spo2=96),
                source=ObservationSource.intake,
            ),
            VitalObservation(
                recorded_at_min=10,
                vitals=Vitals(heart_rate=110, sbp=95, spo2=94),
                source=ObservationSource.watcher,
            ),
            VitalObservation(
                recorded_at_min=20,
                vitals=Vitals(heart_rate=122, sbp=90, spo2=92),
                source=ObservationSource.watcher,
            ),
        ],
    )
    base = dict(
        patient_id=patient.patient_id,
        patient_context=patient.context,
        latest_vitals=vitals,
        vital_history=history,
        status=AgentStatus.OBSERVING,
        watcher_state=WatcherState(
            queue_status=WatcherQueueStatus.waiting,
            arrival_min=0,
            next_due_min=30,
            overdue=False,
            unsafe_wait_min=0,
        ),
    )
    base.update(kwargs)
    return TriageAgentState(**base)


def _ctx(patient: Patient | None = None, state: TriageAgentState | None = None) -> ToolContext:
    p = patient or _patient()
    s = state or _state(p)
    return ToolContext(patients={p.patient_id: p}, agent_states={s.patient_id: s})


def test_registry_lists_all_required_tools():
    reg = build_registry()
    required = {
        "get_patient",
        "get_latest_vitals",
        "get_vital_history",
        "get_patient_history",
        "calculate_shock_index",
        "calculate_vital_trends",
        "calculate_time_since_onset",
        "assess_data_completeness",
        "inspect_watcher_state",
        "get_previous_agent_assessment",
        "get_baseline_engine_assessment",
    }
    assert set(reg.names()) == required
    for forbidden in FORBIDDEN_TOOL_NAMES:
        assert not reg.is_registered(forbidden)


def test_unknown_tool_rejected():
    reg = build_registry()
    ctx = _ctx()
    result = reg.execute("get_final_triage", ctx, {"patient_id": "P-100"})
    assert result.ok is False
    assert "unknown tool" in (result.error or "")


def test_invalid_arguments_rejected():
    reg = build_registry()
    ctx = _ctx()
    result = reg.execute("get_patient", ctx, {})
    assert result.ok is False
    assert "invalid arguments" in (result.error or "")


def test_missing_patient_fails_safely():
    reg = build_registry()
    ctx = ToolContext()
    result = reg.execute("get_patient", ctx, {"patient_id": "NOPE"})
    assert result.ok is False
    assert "unknown patient" in (result.error or "").lower() or "NOPE" in (result.error or "")


def test_get_patient_and_history():
    reg = build_registry()
    ctx = _ctx()
    p = reg.execute("get_patient", ctx, {"patient_id": "P-100"})
    assert p.ok
    assert p.evidence["age_years"] == 54
    assert "chest pain" in p.summary

    h = reg.execute("get_patient_history", ctx, {"patient_id": "P-100"})
    assert h.ok
    assert "diabetes" in h.evidence["history"]
    assert "hypertension" in h.summary


def test_latest_vitals_and_history():
    reg = build_registry()
    ctx = _ctx()
    latest = reg.execute("get_latest_vitals", ctx, {"patient_id": "P-100"})
    assert latest.ok
    assert latest.evidence["vitals"]["heart_rate"] == 110

    hist = reg.execute("get_vital_history", ctx, {"patient_id": "P-100", "limit": 2})
    assert hist.ok
    assert hist.evidence["count"] == 2


def test_calculate_shock_index_numeric():
    reg = build_registry()
    ctx = _ctx()
    result = reg.execute("calculate_shock_index", ctx, {"patient_id": "P-100"})
    assert result.ok
    # 110 / 90 ≈ 1.22
    assert result.evidence["shock_index"] == round(110 / 90, 2)
    assert result.evidence["shock_index_flag"] in ("normal", "elevated", "critical")


def test_calculate_shock_index_unknown_without_sbp():
    patient = _patient()
    state = _state(patient, latest_vitals=Vitals(heart_rate=100), vital_history=[])
    ctx = _ctx(patient, state)
    reg = build_registry()
    result = reg.execute("calculate_shock_index", ctx, {"patient_id": "P-100"})
    assert result.ok
    assert result.evidence["shock_index"] is None
    assert result.evidence["shock_index_flag"] == "unknown"


def test_vital_trends_series_text():
    reg = build_registry()
    ctx = _ctx()
    result = reg.execute(
        "calculate_vital_trends",
        ctx,
        {"patient_id": "P-100", "vitals": ["heart_rate"]},
    )
    assert result.ok
    assert result.evidence["trends"]["heart_rate"]["series_text"] == "96 → 110 → 122"
    assert "HR 96 → 110 → 122" in result.summary
    assert result.evidence["trends"]["heart_rate"]["direction"] == "rising"


def test_time_since_onset():
    reg = build_registry()
    ctx = _ctx()
    result = reg.execute(
        "calculate_time_since_onset",
        ctx,
        {"patient_id": "P-100", "now_min": 15},
    )
    assert result.ok
    # onset 40 at intake + 15 wait
    assert result.evidence["minutes_since_onset"] == 55


def test_assess_data_completeness_missing_sbp():
    patient = _patient()
    state = _state(
        patient,
        latest_vitals=Vitals(heart_rate=100, resp_rate=18, spo2=98, temp_c=36.8),
        vital_history=[],
    )
    ctx = _ctx(patient, state)
    reg = build_registry()
    result = reg.execute("assess_data_completeness", ctx, {"patient_id": "P-100"})
    assert result.ok
    assert "sbp" in result.evidence["missing_vitals"]
    assert "missing SBP" in result.summary


def test_inspect_watcher_state():
    reg = build_registry()
    ctx = _ctx()
    result = reg.execute("inspect_watcher_state", ctx, {"patient_id": "P-100"})
    assert result.ok
    assert result.evidence["queue_status"] == "waiting"


def test_previous_agent_assessment():
    patient = _patient()
    prior_rec = AgentRecommendation(
        priority=3,
        confidence=0.7,
        reason_summary="Stable vitals at first pass.",
    )
    prior_state = _state(
        patient,
        previous_recommendation=None,
        current_recommendation=prior_rec,
        assessment_id="as-prior",
        status=AgentStatus.COMPLETED,
    )
    current = _state(patient, previous_recommendation=prior_rec)
    ctx = ToolContext(
        patients={patient.patient_id: patient},
        agent_states={patient.patient_id: current},
        assessments_by_patient={patient.patient_id: [prior_state]},
    )
    reg = build_registry()
    result = reg.execute(
        "get_previous_agent_assessment", ctx, {"patient_id": "P-100"}
    )
    assert result.ok
    assert result.evidence["available"] is True
    assert result.evidence["recommendation"]["priority"] == 3


def test_baseline_engine_assessment_labeled_and_non_mutating():
    patient = _patient()
    state = _state(patient)
    before = copy.deepcopy(state)
    ctx = ToolContext(
        patients={patient.patient_id: patient},
        agent_states={patient.patient_id: state},
    )
    reg = build_registry()
    result = reg.execute(
        "get_baseline_engine_assessment", ctx, {"patient_id": "P-100"}
    )
    assert result.ok
    assert result.evidence["source"] == BASELINE_SOURCE
    assert result.evidence["priority"] in range(1, 6)
    assert "deterministic baseline" in result.summary.lower()
    assert "SOURCE=deterministic_baseline" in result.summary
    # Must not auto-write into agent state
    assert state.baseline_assessment is None
    assert state.model_dump() == before.model_dump()


def test_bridge_strips_deteriorates():
    patient = _patient(deteriorates="sepsis")
    legacy = to_legacy_patient(
        patient, Vitals(heart_rate=100, sbp=120)
    )
    assert legacy.deteriorates is None
    assert legacy.vitals.heart_rate == 100


def test_default_registry_singleton():
    a = get_default_registry()
    b = get_default_registry()
    assert a is b
    assert a.is_registered("get_patient")


def test_tool_result_shape():
    reg = build_registry()
    ctx = _ctx()
    result = reg.execute("get_patient", ctx, {"patient_id": "P-100"})
    assert isinstance(result, ToolResult)
    assert set(result.model_dump().keys()) >= {
        "ok",
        "tool_name",
        "evidence",
        "summary",
        "error",
    }
