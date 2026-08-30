"""Evaluation snapshots and baseline storage helpers.

Used for analysis only. Never rewrite the agent recommendation to match baseline.
"""

from __future__ import annotations

from typing import Optional

from app.domain.enums import DecisionSource
from app.domain.models import (
    AgentRecommendation,
    BaselineAssessment,
    BaselineComparison,
    ConfidenceBand,
    EvaluationSnapshot,
    TriageAgentState,
)
from app.models import TriageResult


def store_baseline_from_engine(
    state: TriageAgentState, result: TriageResult
) -> BaselineAssessment:
    """Persist deterministic output as baseline/evaluation reference only."""

    adj = result.adjudicator
    baseline = BaselineAssessment(
        source="deterministic_baseline",
        priority=adj.acuity,
        placement=adj.placement,
        monitoring_tier=adj.monitoring_tier.value,
        confidence=adj.confidence,
        drivers=list(adj.top_drivers),
        summary=(
            f"Deterministic baseline P{adj.acuity} "
            f"(SOURCE=deterministic_baseline); placement {adj.placement}."
        )[:500],
    )
    state.baseline_assessment = baseline
    return baseline


def recommendation_from_fallback(result: TriageResult) -> AgentRecommendation:
    """Build a recommendation from the engine when the agent cannot complete.

    Marked via decision_source=deterministic_fallback by the caller. This is
    not a silent primary path; it is explicit degradation.
    """

    from app.llm.stub import template_explanation

    adj = result.adjudicator
    band = ConfidenceBand(adj.confidence_band.value)
    return AgentRecommendation(
        priority=adj.acuity,
        urgency="immediate" if adj.acuity <= 2 else "semi-urgent" if adj.acuity == 3 else "non-urgent",
        care_pathway=adj.placement,
        monitoring_plan=adj.monitoring_tier.value,
        confidence=adj.confidence,
        confidence_band=band,
        reason_summary=template_explanation(result)[:500],
        key_evidence=list(adj.top_drivers[:5]),
        information_gaps=list(result.interpreter.data_gaps[:5]),
        human_review_required=True,
    )


def refresh_evaluation(
    state: TriageAgentState,
    *,
    clinician_priority: Optional[int] = None,
) -> EvaluationSnapshot:
    """Recompute agreement flags. Does not change recommendations."""

    agent_p = (
        state.current_recommendation.priority
        if state.current_recommendation is not None
        else None
    )
    baseline_p = (
        state.baseline_assessment.priority
        if state.baseline_assessment is not None
        else None
    )
    clin_p = clinician_priority
    if clin_p is None and state.clinician_decision is not None:
        clin_p = state.clinician_decision.active_priority

    def _agree(a: Optional[int], b: Optional[int]) -> Optional[bool]:
        if a is None or b is None:
            return None
        return a == b

    snap = EvaluationSnapshot(
        agent_priority=agent_p,
        baseline_priority=baseline_p,
        clinician_priority=clin_p,
        agent_baseline_agreement=_agree(agent_p, baseline_p),
        agent_clinician_agreement=_agree(agent_p, clin_p),
        baseline_clinician_agreement=_agree(baseline_p, clin_p),
    )
    state.evaluation = snap

    if agent_p is not None and baseline_p is not None:
        agrees = agent_p == baseline_p
        state.baseline_comparison = BaselineComparison(
            baseline_priority=baseline_p,
            agent_priority=agent_p,
            agrees=agrees,
            delta=agent_p - baseline_p,
            disagreement_reason=(
                None
                if agrees
                else f"Agent priority {agent_p} differs from deterministic baseline {baseline_p}."
            ),
            summary=f"agent={agent_p}, baseline={baseline_p}, agrees={agrees}"[:280],
        )
    return snap


def mark_baseline_used_from_tools(state: TriageAgentState) -> bool:
    """True if the agent called get_baseline_engine_assessment successfully."""

    used = any(
        c.tool_name == "get_baseline_engine_assessment" and c.status.value == "ok"
        for c in state.tool_history
    )
    state.baseline_used = used
    return used
