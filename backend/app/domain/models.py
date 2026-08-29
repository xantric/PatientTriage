"""Pydantic domain models for agent-first triage state.

Operational summaries only. No chain-of-thought fields.
The agent may recommend a priority; nothing here overrides that recommendation
with a deterministic acuity winner.
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from app.domain.enums import (
    AgentStatus,
    ArrivalMode,
    AuditAction,
    ClinicianAction,
    ConfidenceBand,
    DecisionSource,
    EventKind,
    ObservationSource,
    Responsiveness,
    Sex,
    ToolCallStatus,
    TriageEventType,
    WatcherQueueStatus,
)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Vitals and observations
# ---------------------------------------------------------------------------


class Vitals(BaseModel):
    """Point-in-time vital signs. All fields optional; intake is often sparse."""

    heart_rate: Optional[float] = Field(None, description="beats per minute")
    resp_rate: Optional[float] = Field(None, description="breaths per minute")
    sbp: Optional[float] = Field(None, description="systolic blood pressure mmHg")
    dbp: Optional[float] = Field(None, description="diastolic blood pressure mmHg")
    spo2: Optional[float] = Field(None, description="oxygen saturation percent")
    temp_c: Optional[float] = Field(None, description="core temperature Celsius")

    def present_fields(self) -> list[str]:
        return [k for k, v in self.model_dump().items() if v is not None]

    def is_empty(self) -> bool:
        return not self.present_fields()


class VitalObservation(BaseModel):
    """One recorded vitals snapshot with provenance."""

    observation_id: str = Field(default_factory=lambda: _new_id("vo"))
    recorded_at_min: int = Field(
        ..., ge=0, description="simulation or wall-clock minutes from session origin"
    )
    recorded_at_utc: Optional[str] = None
    vitals: Vitals
    source: ObservationSource = ObservationSource.intake
    note: str = Field(
        default="",
        description="Concise operational note, e.g. 'SpO2 declined from 96 to 91 over 15 minutes.'",
    )

    @field_validator("note")
    @classmethod
    def note_must_be_concise(cls, value: str) -> str:
        text = (value or "").strip()
        if len(text) > 280:
            raise ValueError("vital observation note must be at most 280 characters")
        return text


# ---------------------------------------------------------------------------
# Patient
# ---------------------------------------------------------------------------


class PatientContext(BaseModel):
    """Stable demographics and intake context for the agent."""

    display_name: Optional[str] = None
    age_years: float = Field(..., ge=0)
    sex: Sex = Sex.other
    arrival_mode: ArrivalMode = ArrivalMode.walk_in
    chief_complaint: str = ""
    pain_score: Optional[int] = Field(None, ge=0, le=10)
    responsiveness: Optional[Responsiveness] = None
    onset_minutes: Optional[int] = Field(
        None, ge=0, description="minutes since symptom onset, if known"
    )
    history: list[str] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    has_prior_record: bool = False


class Patient(BaseModel):
    """Patient identity plus context. Vitals live in observations / agent state."""

    patient_id: str
    context: PatientContext
    arrival_epoch_min: int = Field(0, ge=0)
    # Simulation / evaluation labels only. Never used as an overriding priority.
    expected_priority: Optional[int] = Field(None, ge=1, le=5)
    scenario_note: Optional[str] = None
    deteriorates: Optional[str] = Field(
        None,
        description="Sim-only deterioration profile name; triage agents must not read this.",
    )


class PatientEvent(BaseModel):
    """Timeline event for a patient. Summary is operational, not chain-of-thought."""

    event_id: str = Field(default_factory=lambda: _new_id("pe"))
    patient_id: str
    time_min: int = Field(..., ge=0)
    timestamp_utc: Optional[str] = None
    kind: EventKind
    summary: str = Field(
        ...,
        min_length=1,
        max_length=280,
        description="Concise fact, e.g. 'Blood pressure is missing.'",
    )
    related_recommendation_id: Optional[str] = None
    related_decision_id: Optional[str] = None


class TriageEvent(BaseModel):
    """Immutable operational event for event-driven reassessment.

    Watcher emits what physically happened. Agent recommendations must never
    erase these records, especially DETERIORATION_DETECTED.
    """

    event_id: str = Field(default_factory=lambda: _new_id("te"))
    event_type: TriageEventType
    patient_id: str
    time_min: int = Field(..., ge=0)
    summary: str = Field(..., min_length=1, max_length=500)
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(
        default="watcher",
        description="watcher | agent | clinician | system",
    )
    triggers_reassessment: bool = False
    related_recommendation_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Agent observations, tools, recommendation
# ---------------------------------------------------------------------------


class AgentObservation(BaseModel):
    """Concise operational finding the agent may reason over."""

    observation_id: str = Field(default_factory=lambda: _new_id("ao"))
    time_min: int = Field(0, ge=0)
    source: ObservationSource = ObservationSource.system
    summary: str = Field(
        ...,
        min_length=1,
        max_length=280,
        description="Operational summary only. No hidden chain-of-thought.",
    )
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="Ids of vitals, tools, or events that support this summary.",
    )


class AgentToolCall(BaseModel):
    """Record of one tool invocation during an assessment."""

    call_id: str = Field(default_factory=lambda: _new_id("tc"))
    tool_name: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: ToolCallStatus = ToolCallStatus.ok
    result_summary: str = Field(
        default="",
        max_length=500,
        description="Short operational digest of the tool result.",
    )
    result_payload: Optional[dict[str, Any]] = Field(
        None, description="Optional structured tool output for later tools."
    )
    latency_ms: float = Field(0.0, ge=0)
    error: Optional[str] = None
    iteration: int = Field(0, ge=0)


class AgentRecommendation(BaseModel):
    """Agent-produced triage recommendation.

    Priority is optional: the agent MAY recommend one. There is no sibling
    deterministic priority on TriageAgentState that overrides this field.
    """

    recommendation_id: str = Field(default_factory=lambda: _new_id("rec"))
    issued_at_min: int = Field(0, ge=0)
    priority: Optional[int] = Field(
        None,
        ge=1,
        le=5,
        description="Agent-recommended priority (ESI-style 1 most urgent). Optional.",
    )
    urgency: str = Field(
        default="",
        max_length=120,
        description="Short urgency label, e.g. 'immediate' or 'semi-urgent'.",
    )
    care_pathway: str = Field(
        default="",
        max_length=200,
        description="Suggested care area or pathway, not a diagnosis.",
    )
    monitoring_plan: str = Field(
        default="",
        max_length=200,
        description="Suggested re-check / observation cadence.",
    )
    confidence: float = Field(..., ge=0.0, le=1.0)
    confidence_band: Optional[ConfidenceBand] = None
    reason_summary: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Concise operational rationale. No chain-of-thought.",
    )
    key_evidence: list[str] = Field(default_factory=list)
    information_gaps: list[str] = Field(default_factory=list)
    human_review_required: bool = True
    revision: int = Field(0, ge=0)


class DerivedMetrics(BaseModel):
    """Tool-derived scalars and flags. Reference data, not a priority override."""

    shock_index: Optional[float] = None
    shock_index_flag: str = "unknown"  # normal | elevated | critical | unknown
    age_band: Optional[str] = None
    minutes_since_onset: Optional[int] = None
    completeness: Optional[float] = Field(None, ge=0.0, le=1.0)
    extra: dict[str, Any] = Field(default_factory=dict)


class BaselineAssessment(BaseModel):
    """Deterministic reference assessment for fallback and evaluation only.

    Stored for comparison. Must not be treated as the live priority override.
    """

    source: str = "deterministic_baseline"
    priority: Optional[int] = Field(None, ge=1, le=5)
    placement: Optional[str] = None
    monitoring_tier: Optional[str] = None
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    drivers: list[str] = Field(default_factory=list)
    summary: str = Field(default="", max_length=500)


class BaselineComparison(BaseModel):
    """How the agent recommendation relates to the baseline reference.

    Neither side is auto-rewritten to match the other. Clinician sees both.
    """

    baseline_priority: Optional[int] = Field(None, ge=1, le=5)
    agent_priority: Optional[int] = Field(None, ge=1, le=5)
    agrees: Optional[bool] = None
    delta: Optional[int] = Field(
        None,
        description="agent_priority - baseline_priority; negative means agent more urgent",
    )
    disagreement_reason: Optional[str] = Field(
        None,
        max_length=500,
        description="Set when priorities differ; never forces either side to change.",
    )
    summary: str = Field(default="", max_length=280)


class EvaluationSnapshot(BaseModel):
    """Disagreement analytics. Never used to force the agent to mimic baseline."""

    agent_priority: Optional[int] = Field(None, ge=1, le=5)
    baseline_priority: Optional[int] = Field(None, ge=1, le=5)
    clinician_priority: Optional[int] = Field(None, ge=1, le=5)
    agent_baseline_agreement: Optional[bool] = None
    agent_clinician_agreement: Optional[bool] = None
    baseline_clinician_agreement: Optional[bool] = None


# ---------------------------------------------------------------------------
# HITL, Watcher, audit, assessment
# ---------------------------------------------------------------------------


class ClinicianDecision(BaseModel):
    """Human-in-the-loop decision. Final authority over activation."""

    decision_id: str = Field(default_factory=lambda: _new_id("cd"))
    patient_id: str
    action: ClinicianAction
    actor: str = Field(..., min_length=1)
    actor_role: str = "clinician"
    reason: str = Field(..., min_length=1, max_length=1000)
    # Clinician may set an active priority on accept/modify/escalate.
    active_priority: Optional[int] = Field(None, ge=1, le=5)
    related_recommendation_id: Optional[str] = None
    decided_at_min: int = Field(0, ge=0)
    timestamp_utc: Optional[str] = None
    info_provided: list[str] = Field(
        default_factory=list,
        description="Facts supplied when action is request_info.",
    )


class WatcherState(BaseModel):
    """Continuous waiting-room monitoring state for one patient."""

    queue_status: WatcherQueueStatus = WatcherQueueStatus.not_queued
    arrival_min: Optional[int] = None
    last_contact_min: Optional[int] = None
    next_due_min: Optional[int] = None
    overdue: bool = False
    unsafe_wait_min: int = Field(0, ge=0)
    last_recheck_min: Optional[int] = None
    ratchet_count: int = Field(0, ge=0)
    backstop_fired: bool = False
    last_event_summary: str = Field(default="", max_length=280)


class AuditEvent(BaseModel):
    """Append-only audit line for the agent-era system."""

    event_id: str = Field(default_factory=lambda: _new_id("ae"))
    sequence: Optional[int] = Field(None, ge=1)
    timestamp_utc: str = Field(..., min_length=1)
    patient_id: str
    assessment_id: Optional[str] = None
    action: AuditAction
    actor: str = "system"
    actor_role: str = "system"
    summary: str = Field(..., min_length=1, max_length=500)
    payload: dict[str, Any] = Field(default_factory=dict)
    model_version: Optional[str] = None


# ---------------------------------------------------------------------------
# TriageAgentState (core iterative reasoning container)
# ---------------------------------------------------------------------------


class TriageAgentState(BaseModel):
    """Enough state for iterative agent reasoning without chain-of-thought.

    Live priority, when present, lives only on AgentRecommendation.priority
    (and later on ClinicianDecision.active_priority). Baseline values are
    reference/fallback/evaluation only.
    """

    patient_id: str

    patient_context: PatientContext
    latest_vitals: Optional[Vitals] = None
    vital_history: list[VitalObservation] = Field(default_factory=list)

    current_observations: list[AgentObservation] = Field(default_factory=list)
    information_gaps: list[str] = Field(default_factory=list)

    derived_metrics: DerivedMetrics = Field(default_factory=DerivedMetrics)

    agent_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    uncertainty_reasons: list[str] = Field(default_factory=list)

    tool_history: list[AgentToolCall] = Field(default_factory=list)
    retrieved_context: list[str] = Field(
        default_factory=list,
        description="Concise tool-derived context snippets. Not RAG passages.",
    )

    previous_recommendation: Optional[AgentRecommendation] = None
    current_recommendation: Optional[AgentRecommendation] = None

    baseline_assessment: Optional[BaselineAssessment] = None
    baseline_comparison: Optional[BaselineComparison] = None

    # Primary recommendation provenance (Phase 7).
    decision_source: Optional[DecisionSource] = None
    baseline_used: bool = False
    evaluation: EvaluationSnapshot = Field(default_factory=EvaluationSnapshot)

    watcher_state: WatcherState = Field(default_factory=WatcherState)

    # Append-only triage events. Deterioration history must never be erased.
    triage_events: list[TriageEvent] = Field(default_factory=list)
    # Watcher monitoring floor (ESI-style 1 most urgent). Upward-only (never
    # de-escalated by a softer agent recommendation).
    monitoring_priority_floor: Optional[int] = Field(None, ge=1, le=5)

    iteration_count: int = Field(0, ge=0)
    status: AgentStatus = AgentStatus.INITIALIZING

    human_review_required: bool = True

    clinician_decision: Optional[ClinicianDecision] = None

    # Session bookkeeping (not CoT)
    assessment_id: Optional[str] = None
    events: list[PatientEvent] = Field(default_factory=list)
    last_error: Optional[str] = None

    def deterioration_events(self) -> list[TriageEvent]:
        return [
            e
            for e in self.triage_events
            if e.event_type == TriageEventType.DETERIORATION_DETECTED
        ]

    def operational_summaries(self) -> list[str]:
        """Flatten concise facts suitable for prompts or UI traces."""

        items: list[str] = []
        items.extend(o.summary for o in self.current_observations)
        items.extend(self.information_gaps)
        items.extend(self.uncertainty_reasons)
        if self.current_recommendation is not None:
            items.append(self.current_recommendation.reason_summary)
        return items


class AgentAssessment(BaseModel):
    """One assessment session bound to a TriageAgentState snapshot."""

    assessment_id: str = Field(default_factory=lambda: _new_id("as"))
    patient_id: str
    started_at_min: int = Field(0, ge=0)
    completed_at_min: Optional[int] = None
    status: AgentStatus = AgentStatus.INITIALIZING
    state: TriageAgentState
    audit_event_ids: list[str] = Field(default_factory=list)
