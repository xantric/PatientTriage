"""Primary triage assessment: Gemini agent owns the recommendation.

Flow:

    PATIENT → AGENT STATE → GEMINI AGENT → TOOLS → RECOMMENDATION → HITL

The deterministic Interpreter/Adjudicator pipeline is BASELINE / FALLBACK /
EVALUATION only. It never silently overwrites a successful agent recommendation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.agent.bridge import from_legacy_patient, from_legacy_vitals
from app.agent.context import ToolContext
from app.agent.evaluation import (
    mark_baseline_used_from_tools,
    recommendation_from_fallback,
    refresh_evaluation,
    store_baseline_from_engine,
)
from app.agent.orchestrator import (
    AgentLLM,
    AgentRunResult,
    GeminiAgentLLM,
    TriageAgentOrchestrator,
    build_default_agent_llm,
)
from app.domain.enums import AgentStatus, DecisionSource, ObservationSource
from app.domain.models import (
    AgentRecommendation,
    TriageAgentState,
    VitalObservation,
)
from app.engine.pipeline import triage as run_baseline_engine
from app.models import Patient as LegacyPatient
from app.models import TriageResult


@dataclass
class PrimaryAssessmentResult:
    """Outcome of one primary assessment pass."""

    state: TriageAgentState
    baseline_result: TriageResult
    decision_source: DecisionSource
    agent_run: Optional[AgentRunResult] = None
    fallback_reason: Optional[str] = None

    @property
    def recommendation(self) -> Optional[AgentRecommendation]:
        return self.state.current_recommendation


def _initial_state(legacy: LegacyPatient) -> tuple[TriageAgentState, object]:
    domain = from_legacy_patient(legacy)
    vitals = from_legacy_vitals(legacy.vitals)
    state = TriageAgentState(
        patient_id=domain.patient_id,
        patient_context=domain.context,
        latest_vitals=vitals,
        vital_history=[
            VitalObservation(
                recorded_at_min=0,
                vitals=vitals,
                source=ObservationSource.intake,
                note="Intake vitals.",
            )
        ]
        if not vitals.is_empty()
        else [],
        status=AgentStatus.INITIALIZING,
        human_review_required=True,
    )
    return state, domain


def _agent_succeeded(run: AgentRunResult) -> bool:
    if run.state.status == AgentStatus.FAILED:
        return False
    if run.stopped_reason in ("llm_failure", "parse_failure", "unhandled_action"):
        return False
    if run.terminal_action == "RECOMMEND" and run.state.current_recommendation is not None:
        return True
    # REQUEST_INFORMATION / ESCALATE / budget are completed agent decisions,
    # not silent engine overrides. Still "agent" source if no rec yet.
    if run.terminal_action in ("REQUEST_INFORMATION", "ESCALATE", "AWAITING_HUMAN"):
        return True
    return False


def apply_deterministic_fallback(
    state: TriageAgentState,
    baseline: TriageResult,
    *,
    reason: str,
) -> None:
    """Explicit fallback. Sets decision_source=deterministic_fallback."""

    store_baseline_from_engine(state, baseline)
    state.current_recommendation = recommendation_from_fallback(baseline)
    state.agent_confidence = state.current_recommendation.confidence
    state.decision_source = DecisionSource.deterministic_fallback
    state.baseline_used = False
    state.status = AgentStatus.AWAITING_HUMAN
    state.human_review_required = True
    state.last_error = reason
    state.uncertainty_reasons.append(f"Fallback: {reason}"[:280])
    refresh_evaluation(state)


def run_primary_assessment(
    legacy_patient: LegacyPatient,
    *,
    llm: Optional[AgentLLM] = None,
    context: Optional[ToolContext] = None,
    force_fallback: bool = False,
) -> PrimaryAssessmentResult:
    """Run the agent-primary path; fall back to the engine only on failure."""

    baseline = run_baseline_engine(legacy_patient)
    state, domain_patient = _initial_state(legacy_patient)
    store_baseline_from_engine(state, baseline)

    ctx = context or ToolContext()
    ctx.patients[domain_patient.patient_id] = domain_patient
    ctx.agent_states[state.patient_id] = state

    if force_fallback:
        apply_deterministic_fallback(
            state, baseline, reason="forced deterministic fallback"
        )
        return PrimaryAssessmentResult(
            state=state,
            baseline_result=baseline,
            decision_source=DecisionSource.deterministic_fallback,
            fallback_reason="forced deterministic fallback",
        )

    agent_llm = llm if llm is not None else build_default_agent_llm()
    # Unavailable provider → fallback (no silent "agent" label).
    if llm is None:
        if isinstance(agent_llm, GeminiAgentLLM) and not agent_llm.live:
            apply_deterministic_fallback(
                state,
                baseline,
                reason="Gemini unavailable (no key, package, or stub mode)",
            )
            return PrimaryAssessmentResult(
                state=state,
                baseline_result=baseline,
                decision_source=DecisionSource.deterministic_fallback,
                fallback_reason="Gemini unavailable",
            )

    orch = TriageAgentOrchestrator(agent_llm)
    run = orch.run(patient=domain_patient, state=state, context=ctx)

    if not _agent_succeeded(run):
        reason = run.state.last_error or run.stopped_reason or "agent loop failed"
        apply_deterministic_fallback(state, baseline, reason=reason)
        return PrimaryAssessmentResult(
            state=state,
            baseline_result=baseline,
            decision_source=DecisionSource.deterministic_fallback,
            agent_run=run,
            fallback_reason=reason,
        )

    # Successful agent path: do NOT copy baseline into the recommendation.
    state.decision_source = DecisionSource.agent
    mark_baseline_used_from_tools(state)
    # Re-store baseline for evaluation if the agent did not fetch it via tool.
    if state.baseline_assessment is None:
        store_baseline_from_engine(state, baseline)
    refresh_evaluation(state)
    if state.current_recommendation is not None:
        state.status = AgentStatus.AWAITING_HUMAN
        state.human_review_required = True

    return PrimaryAssessmentResult(
        state=state,
        baseline_result=baseline,
        decision_source=DecisionSource.agent,
        agent_run=run,
    )
