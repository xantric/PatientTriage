"""Human-in-the-loop decision application.

Clinician actions never overwrite AgentRecommendation. Both remain auditable:
agent recommendation stays on TriageAgentState.current_recommendation;
clinician outcome lives on TriageAgentState.clinician_decision.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.agent.evaluation import refresh_evaluation
from app.domain.enums import AgentStatus, ClinicianAction, EventKind, ObservationSource
from app.domain.models import (
    AgentObservation,
    ClinicianDecision,
    PatientEvent,
    TriageAgentState,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# Actions that require an explicit clinician reason.
_REASON_REQUIRED = frozenset(
    {
        ClinicianAction.modify,
        ClinicianAction.override,
        ClinicianAction.reject,
    }
)


class ClinicianDecisionInput(BaseModel):
    """Inbound HITL payload. Validated before mutating state."""

    action: ClinicianAction
    actor: str = Field(..., min_length=1)
    actor_role: str = "clinician"
    reason: str = Field(default="", max_length=1000)
    active_priority: Optional[int] = Field(None, ge=1, le=5)
    decided_at_min: int = Field(0, ge=0)
    info_provided: list[str] = Field(default_factory=list)

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, value):  # noqa: ANN001
        if isinstance(value, ClinicianAction):
            return value
        raw = str(value).strip().lower().replace(" ", "_")
        aliases = {
            "request_info": ClinicianAction.request_more_information,
            "request_more_info": ClinicianAction.request_more_information,
            "request_information": ClinicianAction.request_more_information,
            "request_more_information": ClinicianAction.request_more_information,
        }
        if raw in aliases:
            return aliases[raw]
        return ClinicianAction(raw)


class HitlError(ValueError):
    """Raised when a clinician decision cannot be applied safely."""


def apply_clinician_decision(
    state: TriageAgentState,
    decision: ClinicianDecisionInput,
) -> ClinicianDecision:
    """Apply HITL. Preserves the original agent recommendation unchanged."""

    if state.current_recommendation is None and decision.action in (
        ClinicianAction.accept,
        ClinicianAction.modify,
        ClinicianAction.override,
    ):
        raise HitlError(
            "no agent recommendation to accept/modify/override; "
            "wait for RECOMMEND or use escalate / request_more_information"
        )

    action = decision.action
    # Normalize legacy request_info onto the Phase 5 name.
    if action == ClinicianAction.request_info:
        action = ClinicianAction.request_more_information

    reason = (decision.reason or "").strip()
    if action in _REASON_REQUIRED and not reason:
        raise HitlError(f"{action.value} requires a reason")

    if action in (ClinicianAction.modify, ClinicianAction.override):
        if decision.active_priority is None:
            raise HitlError(f"{action.value} requires active_priority")

    if action == ClinicianAction.accept:
        if not reason:
            reason = "Accepted agent recommendation."
        active = (
            decision.active_priority
            if decision.active_priority is not None
            else (
                state.current_recommendation.priority
                if state.current_recommendation
                else None
            )
        )
    elif action in (ClinicianAction.modify, ClinicianAction.override):
        active = decision.active_priority
    elif action == ClinicianAction.escalate:
        if not reason:
            raise HitlError("escalate requires a reason")
        active = decision.active_priority
    elif action == ClinicianAction.request_more_information:
        if not reason:
            raise HitlError("request_more_information requires a reason")
        active = None
    elif action == ClinicianAction.reject:
        active = decision.active_priority
    else:
        raise HitlError(f"unsupported clinician action: {action}")

    # Snapshot agent recommendation id for audit; never mutate the recommendation.
    rec = state.current_recommendation
    agent_priority_snapshot = rec.priority if rec else None
    rec_id = rec.recommendation_id if rec else None

    record = ClinicianDecision(
        patient_id=state.patient_id,
        action=action,
        actor=decision.actor,
        actor_role=decision.actor_role,
        reason=reason,
        active_priority=active,
        related_recommendation_id=rec_id,
        decided_at_min=decision.decided_at_min,
        timestamp_utc=_now_iso(),
        info_provided=list(decision.info_provided),
    )

    # Preserve original recommendation object identity / contents.
    preserved = rec.model_copy(deep=True) if rec is not None else None

    state.clinician_decision = record

    if action == ClinicianAction.accept:
        state.status = AgentStatus.COMPLETED
        state.human_review_required = False
        summary = (
            f"Clinician accepted agent priority {agent_priority_snapshot}."
        )
    elif action == ClinicianAction.modify:
        state.status = AgentStatus.COMPLETED
        state.human_review_required = False
        summary = (
            f"Clinician modified priority to {active} "
            f"(agent had {agent_priority_snapshot}). Reason: {reason}"
        )[:280]
    elif action == ClinicianAction.override:
        state.status = AgentStatus.COMPLETED
        state.human_review_required = False
        summary = (
            f"Clinician overrode to priority {active} "
            f"(agent had {agent_priority_snapshot}). Reason: {reason}"
        )[:280]
    elif action == ClinicianAction.request_more_information:
        state.status = AgentStatus.GATHERING_INFORMATION
        state.human_review_required = True
        for fact in decision.info_provided:
            gap_note = f"Clinician supplied: {fact}"
            if gap_note not in state.information_gaps:
                # info_provided are facts, not gaps; track as observations.
                pass
            state.current_observations.append(
                AgentObservation(
                    summary=gap_note[:280],
                    source=ObservationSource.clinician,
                )
            )
        state.information_gaps.append(f"Clinician requested more information: {reason}"[:280])
        summary = f"Clinician requested more information: {reason}"[:280]
    elif action == ClinicianAction.escalate:
        state.status = AgentStatus.ESCALATED
        state.human_review_required = True
        summary = f"Clinician escalated: {reason}"[:280]
    elif action == ClinicianAction.reject:
        state.status = AgentStatus.ESCALATED
        state.human_review_required = True
        summary = f"Clinician rejected recommendation: {reason}"[:280]
    else:
        summary = f"Clinician action {action.value}."

    state.current_observations.append(
        AgentObservation(
            summary=summary[:280],
            source=ObservationSource.clinician,
        )
    )
    state.events.append(
        PatientEvent(
            patient_id=state.patient_id,
            time_min=decision.decided_at_min,
            kind=EventKind.clinician_decision,
            summary=summary[:280],
            related_recommendation_id=rec_id,
            related_decision_id=record.decision_id,
        )
    )

    # Hard guarantee: agent recommendation bytes unchanged.
    if preserved is not None and state.current_recommendation is not None:
        if state.current_recommendation.model_dump() != preserved.model_dump():
            state.current_recommendation = preserved
            raise HitlError("internal error: agent recommendation was mutated")

    refresh_evaluation(state, clinician_priority=active)
    return record
