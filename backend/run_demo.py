"""Sentinel Phase 8 demos: agent-first triage scenarios.

Usage (from sentinel/backend):
    python run_demo.py
"""

from __future__ import annotations

import json

from app.agent.context import ToolContext
from app.agent.event_watcher import simulate_event_driven
from app.agent.events import apply_triage_event
from app.agent.hitl import ClinicianDecisionInput, apply_clinician_decision
from app.agent.orchestrator import MockAgentLLM, TriageAgentOrchestrator
from app.agent.primary import run_primary_assessment
from app.agent.reassessment import ReassessmentBus
from app.data.generator import build_cohort
from app.domain.enums import (
    AgentStatus,
    ArrivalMode,
    ClinicianAction,
    DecisionSource,
    Sex,
    TriageEventType,
)
from app.domain.models import Patient, PatientContext, TriageAgentState, TriageEvent, Vitals
from app.engine.pipeline import triage as baseline_triage
from app.engine.watcher import simulate as legacy_simulate


def _action(obj: dict) -> str:
    return json.dumps(obj)


def _banner(title: str) -> None:
    print("\n" + "=" * 88)
    print(title)
    print("=" * 88)


def demo_1_information_gathering() -> None:
    _banner("DEMO 1: INFORMATION GATHERING (missing BP)")
    patient = Patient(
        patient_id="DEMO-1",
        context=PatientContext(
            age_years=72,
            sex=Sex.female,
            arrival_mode=ArrivalMode.walk_in,
            chief_complaint="dizziness",
            history=["hypertension"],
        ),
    )
    state = TriageAgentState(
        patient_id="DEMO-1",
        patient_context=patient.context,
        latest_vitals=Vitals(heart_rate=116, spo2=95),
    )
    ctx = ToolContext(
        patients={patient.patient_id: patient},
        agent_states={patient.patient_id: state},
    )
    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "REQUEST_INFORMATION",
                    "fields": ["blood_pressure"],
                    "reason": "Cannot safely stratify dizziness with tachycardia without SBP.",
                }
            )
        ]
    )
    first = TriageAgentOrchestrator(mock).run(
        patient=patient, state=state, context=ctx
    )
    print(f"  status after gap detect : {first.state.status.value}")
    print(f"  gaps                    : {first.state.information_gaps}")

    # Clinician / nurse supplies BP, then agent re-evaluates.
    state.latest_vitals = Vitals(heart_rate=116, spo2=95, sbp=88, dbp=56)
    state.information_gaps = [g for g in state.information_gaps if "blood" not in g.lower()]
    mock2 = MockAgentLLM(
        [
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "calculate_shock_index",
                    "arguments": {"patient_id": "DEMO-1"},
                }
            ),
            _action(
                {
                    "action": "RECOMMEND",
                    "recommendation": {
                        "priority": 2,
                        "urgency": "high",
                        "care_pathway": "high-acuity area",
                        "monitoring_plan": "continuous vitals",
                        "confidence": 0.84,
                        "reason_summary": "Dizziness with tachycardia and hypotension after BP obtained.",
                        "key_evidence": ["HR 116", "SBP 88", "SpO2 95"],
                    },
                }
            ),
        ]
    )
    second = TriageAgentOrchestrator(mock2).run(
        patient=patient, state=state, context=ctx
    )
    print(f"  after BP supplied       : {second.terminal_action}")
    print(f"  recommendation          : P{second.state.current_recommendation.priority}")
    print(f"  HITL status             : {second.state.status.value}")
    apply_clinician_decision(
        second.state,
        ClinicianDecisionInput(
            action=ClinicianAction.accept,
            actor="Demo Clinician",
            reason="Accepted after BP confirmed.",
        ),
    )
    print(f"  clinician               : {second.state.status.value}")


def demo_2_agent_vs_baseline() -> None:
    _banner("DEMO 2: AGENT vs BASELINE (agent not a wrapper)")
    # Build a relatively stable-looking adult; force agent P2 vs whatever baseline is.
    legacy = next(p for p in build_cohort(surge_factor=1) if p.patient_id == "P-009")
    baseline = baseline_triage(legacy)
    print(f"  patient                 : {legacy.patient_id} ({legacy.chief_complaint[:48]})")
    print(f"  deterministic baseline  : P{baseline.adjudicator.acuity}")

    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "get_baseline_engine_assessment",
                    "arguments": {"patient_id": legacy.patient_id},
                }
            ),
            _action(
                {
                    "action": "RECOMMEND",
                    "recommendation": {
                        "priority": 2,
                        "urgency": "high",
                        "care_pathway": "urgent assessment bay",
                        "monitoring_plan": "re-check 15 min",
                        "confidence": 0.79,
                        "reason_summary": (
                            "Independent agent judgment: occult bleeding risk "
                            "higher than baseline resource estimate."
                        ),
                        "key_evidence": ["wound history", "ongoing concern"],
                    },
                }
            ),
        ]
    )
    outcome = run_primary_assessment(legacy, llm=mock)
    print(f"  decision_source         : {outcome.decision_source.value}")
    print(f"  agent recommendation    : P{outcome.state.current_recommendation.priority}")
    print(f"  baseline_used           : {outcome.state.baseline_used}")
    print(f"  agreement               : {outcome.state.evaluation.agent_baseline_agreement}")
    if outcome.state.baseline_comparison and outcome.state.baseline_comparison.disagreement_reason:
        print(f"  disagreement            : {outcome.state.baseline_comparison.disagreement_reason}")
    apply_clinician_decision(
        outcome.state,
        ClinicianDecisionInput(
            action=ClinicianAction.modify,
            actor="Demo Clinician",
            reason="Bedside exam supports agent urgency with minor adjustment.",
            active_priority=2,
        ),
    )
    print(
        f"  clinician final         : P{outcome.state.clinician_decision.active_priority} "
        f"({outcome.state.clinician_decision.action.value})"
    )


def demo_3_waiting_room_deterioration() -> None:
    _banner("DEMO 3: WAITING ROOM DETERIORATION")
    patient = Patient(
        patient_id="DEMO-3",
        context=PatientContext(
            age_years=61,
            sex=Sex.male,
            chief_complaint="shortness of breath",
        ),
    )
    state = TriageAgentState(
        patient_id="DEMO-3",
        patient_context=patient.context,
        latest_vitals=Vitals(heart_rate=98, spo2=96),
        monitoring_priority_floor=3,
    )
    ctx = ToolContext(
        patients={patient.patient_id: patient},
        agent_states={patient.patient_id: state},
    )
    print("  initial                 : SpO2 96, HR 98")

    apply_triage_event(
        state,
        TriageEvent(
            event_type=TriageEventType.NEW_VITALS,
            patient_id="DEMO-3",
            time_min=0,
            summary="Intake vitals.",
            payload={"vitals": Vitals(heart_rate=98, spo2=96).model_dump()},
            source="watcher",
        ),
    )
    apply_triage_event(
        state,
        TriageEvent(
            event_type=TriageEventType.NEW_VITALS,
            patient_id="DEMO-3",
            time_min=20,
            summary="Re-check vitals.",
            payload={"vitals": Vitals(heart_rate=118, spo2=92).model_dump()},
            source="watcher",
        ),
    )
    print("  later                   : SpO2 92, HR 118")

    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "calculate_vital_trends",
                    "arguments": {"patient_id": "DEMO-3"},
                }
            ),
            _action(
                {
                    "action": "RECOMMEND",
                    "recommendation": {
                        "priority": 2,
                        "urgency": "high",
                        "care_pathway": "respiratory support area",
                        "monitoring_plan": "continuous SpO2",
                        "confidence": 0.86,
                        "reason_summary": "Worsening hypoxia and tachycardia while waiting.",
                        "key_evidence": ["SpO2 96→92→88", "HR 98→118→132"],
                    },
                }
            ),
        ]
    )
    bus = ReassessmentBus(llm=mock, invoke_on_arrival=False)
    det = TriageEvent(
        event_type=TriageEventType.DETERIORATION_DETECTED,
        patient_id="DEMO-3",
        time_min=40,
        summary="Deterioration detected: SpO2 92 → 88; HR 118 → 132",
        payload={
            "monitoring_priority": 2,
            "vitals_after": Vitals(heart_rate=132, spo2=88).model_dump(),
        },
        source="watcher",
        triggers_reassessment=True,
    )
    print("  later                   : SpO2 88, HR 132")
    result = bus.handle(event=det, patient=patient, state=state, context=ctx)
    print(f"  watcher event           : DETERIORATION_DETECTED")
    print(f"  agent LLM invocations   : {bus.llm_invocations}")
    print(f"  recommendation          : P{result.state.current_recommendation.priority}")
    print(f"  HITL queue              : {result.state.status.value}")
    assert state.deterioration_events(), "deterioration history preserved"


def demo_4_surge() -> None:
    _banner("DEMO 4: 3x SURGE (Watcher attention + load)")
    normal = legacy_simulate(surge_factor=1)
    surge = legacy_simulate(surge_factor=3)
    print(f"  normal patients         : {normal.total_patients}")
    print(f"  surge patients          : {surge.total_patients}")
    print(f"  normal peak waiting     : {normal.peak_waiting}")
    print(f"  surge peak waiting      : {surge.peak_waiting}")
    print(f"  normal unsafe peak (m)  : {normal.peak_unsafe_wait_min}")
    print(f"  surge unsafe peak (m)   : {surge.peak_unsafe_wait_min}")
    print(f"  normal backstops        : {normal.total_backstops}")
    print(f"  surge backstops         : {surge.total_backstops}")
    print(f"  down-ratchets (must 0)  : normal={normal.down_ratchets} surge={surge.down_ratchets}")

    event_report = simulate_event_driven(surge_factor=1)
    dets = sum(
        1
        for e in event_report.triage_events
        if e.event_type == TriageEventType.DETERIORATION_DETECTED
    )
    print(f"  event-driven deteriorations @x1: {dets}")
    print("  (Agent reassessment fires on meaningful events, not every tick.)")


def demo_cohort_board() -> None:
    _banner("COHORT BOARD (deterministic baseline reference labels)")
    cohort = build_cohort()
    results = [baseline_triage(p) for p in cohort]
    results.sort(key=lambda r: (r.adjudicator.acuity, r.patient.arrival_epoch_min))
    print(f"{'ID':7} {'BASE':4} {'conf':6}  complaint")
    print("-" * 72)
    for r in results[:12]:
        print(
            f"{r.patient.patient_id:7} P{r.adjudicator.acuity:<3} "
            f"{r.interpreter.confidence:<6}  {r.patient.chief_complaint[:48]}"
        )
    print(f"... {len(results)} patients total (baseline for evaluation only)")


def main() -> None:
    print(
        "\nSentinel: agent-first, human-in-the-loop emergency triage "
        "decision-support (not autonomous medical decision-making)."
    )
    demo_cohort_board()
    demo_1_information_gathering()
    demo_2_agent_vs_baseline()
    demo_3_waiting_room_deterioration()
    demo_4_surge()
    print("\nAll demos completed.\n")


if __name__ == "__main__":
    main()
