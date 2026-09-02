"""Data contracts shared across the Sentinel triage engine.

These schemas are deliberately small and explicit so every downstream
decision (acuity, confidence, monitoring) can be traced back to a field
that a clinician could inspect.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ArrivalMode(str, Enum):
    walk_in = "walk_in"
    ambulance = "ambulance"
    wheelchair = "wheelchair"
    carried = "carried"


class Responsiveness(str, Enum):
    """AVPU scale: Alert, responds to Voice, responds to Pain, Unresponsive."""

    alert = "A"
    voice = "V"
    pain = "P"
    unresponsive = "U"


class Sex(str, Enum):
    male = "M"
    female = "F"
    other = "O"


class Vitals(BaseModel):
    """Observed vitals. Every field is optional because real intake is sparse."""

    heart_rate: Optional[float] = Field(None, description="beats per minute")
    resp_rate: Optional[float] = Field(None, description="breaths per minute")
    sbp: Optional[float] = Field(None, description="systolic blood pressure mmHg")
    dbp: Optional[float] = Field(None, description="diastolic blood pressure mmHg")
    spo2: Optional[float] = Field(None, description="oxygen saturation percent")
    temp_c: Optional[float] = Field(None, description="core temperature Celsius")

    def present_fields(self) -> list[str]:
        return [k for k, v in self.model_dump().items() if v is not None]


class Patient(BaseModel):
    """A patient as they present at intake."""

    patient_id: str
    display_name: Optional[str] = None
    age_years: float
    sex: Sex = Sex.other
    arrival_mode: ArrivalMode = ArrivalMode.walk_in
    chief_complaint: str = ""
    pain_score: Optional[int] = Field(None, ge=0, le=10)
    responsiveness: Optional[Responsiveness] = None
    vitals: Vitals = Field(default_factory=Vitals)
    onset_minutes: Optional[int] = Field(
        None, description="minutes since symptom onset, if known"
    )
    history: list[str] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)
    has_prior_record: bool = False
    arrival_epoch_min: int = Field(
        0, description="arrival time in minutes on the simulation clock"
    )
    # Optional label used only for evaluating the engine on the synthetic set.
    expected_acuity: Optional[int] = Field(None, ge=1, le=5)
    scenario_note: Optional[str] = None
    # Simulation-only ground truth for the waiting-room demo. Names a
    # deterioration profile ("sepsis", "resp", "cardiac", "bleed") the patient
    # follows while waiting. The triage engine never reads this; the Watcher
    # only ever sees the re-recorded vitals, exactly like a nurse would.
    deteriorates: Optional[str] = None


class ConfidenceBand(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class VitalFlag(BaseModel):
    vital: str
    value: Optional[float]
    status: str  # normal | abnormal | critical | missing
    direction: str  # high | low | none
    note: str = ""


class InterpreterOutput(BaseModel):
    patient_id: str
    age_band: str
    findings: list[str]
    vital_flags: list[VitalFlag]
    shock_index: Optional[float] = None
    shock_index_flag: str = "unknown"  # normal | elevated | critical | unknown
    minutes_since_onset: Optional[int] = None
    red_flags: list[str]
    data_gaps: list[str]
    completeness: float
    confidence: float
    confidence_band: ConfidenceBand


class TimeCriticalClock(BaseModel):
    name: str  # stroke | stemi | sepsis
    window_minutes: int
    minutes_elapsed: Optional[int]
    minutes_remaining: Optional[int]
    in_window: bool
    note: str = ""


class MonitoringTier(str, Enum):
    continuous = "continuous"
    every_15 = "every_15_min"
    every_30 = "every_30_min"
    every_60 = "every_60_min"
    every_120 = "every_120_min"


class AdjudicatorOutput(BaseModel):
    patient_id: str
    acuity: int = Field(ge=1, le=5)
    provisional_acuity: int = Field(ge=1, le=5)
    escalated_for_uncertainty: bool = False
    placement: str
    monitoring_tier: MonitoringTier
    top_drivers: list[str]
    what_if_ignored: str
    time_critical_clocks: list[TimeCriticalClock]
    confidence: float
    confidence_band: ConfidenceBand
    routed_to_nurse: bool = False
    rationale: list[str]


class TriageResult(BaseModel):
    """Bundled agent output for a single patient at a point in time."""

    patient: Patient
    interpreter: InterpreterOutput
    adjudicator: AdjudicatorOutput


# --- Watcher (Agent 3) and waiting-room simulation ---


class WatcherEvent(BaseModel):
    """One entry in the Watcher's audit trail."""

    time_min: int
    patient_id: str
    kind: str  # arrival | treatment_start | recheck | ratchet | backstop
    detail: str = ""
    before_acuity: Optional[int] = None
    after_acuity: Optional[int] = None


class QueueMetric(BaseModel):
    """A snapshot of the board at one simulation tick."""

    time_min: int
    arrived: int
    waiting: int
    in_treatment: int
    done: int
    rechecks_due: int
    rechecks_done: int
    longest_unsafe_wait_min: int
    ratchets: int


class SimReport(BaseModel):
    """Result of one waiting-room simulation run."""

    label: str
    surge_factor: int
    ticks: int
    total_patients: int
    total_ratchets: int
    total_backstops: int
    # Safety invariant: the Watcher must never lower an acuity. Always 0.
    down_ratchets: int
    peak_waiting: int
    peak_unsafe_wait_min: int
    events: list[WatcherEvent]
    metrics: list[QueueMetric]


# --- Clinician override and audit trail ---


class AuditRecord(BaseModel):
    """One immutable line in the audit trail.

    Carries everything a reviewer needs to reconstruct a decision after the
    fact: what the agent/engine said, what the human changed it to, who did it,
    why, when, and which version produced the original score.
    """

    record_id: int
    timestamp_utc: str
    patient_id: str
    action: str  # override | reset | agent_assessment | hitl_*
    actor: str
    actor_role: str
    reason: str
    model_version: str
    before_acuity: Optional[int] = None
    after_acuity: Optional[int] = None
    direction: Optional[str] = None  # escalate | de-escalate | unchanged
    engine_confidence: Optional[float] = None
    engine_drivers: list[str] = Field(default_factory=list)
    # Phase 8 agent audit fields
    assessment_id: Optional[str] = None
    event_id: Optional[str] = None
    agent_model: Optional[str] = None
    agent_version: Optional[str] = None
    agent_action: Optional[str] = None
    tools_used: list[str] = Field(default_factory=list)
    iterations: Optional[int] = None
    agent_priority: Optional[int] = None
    agent_confidence: Optional[float] = None
    baseline_priority: Optional[int] = None
    agreement: Optional[bool] = None
    clinician_action: Optional[str] = None
    clinician_reason: Optional[str] = None
    final_priority: Optional[int] = None


class OverrideRequest(BaseModel):
    """A clinician changing an acuity. A reason is mandatory, not optional."""

    patient_id: str
    new_acuity: int = Field(ge=1, le=5)
    reason: str = Field(min_length=3, description="why the engine was wrong")
    actor: str = Field("unnamed clinician", min_length=1)
    actor_role: str = "triage nurse"


class HitlRequest(BaseModel):
    """Human-in-the-loop action on an agent recommendation."""

    patient_id: str
    action: str = Field(
        ...,
        description="accept | modify | override | request_more_information | escalate",
    )
    actor: str = Field("A. Nurse", min_length=1)
    actor_role: str = "triage nurse"
    reason: str = ""
    active_priority: Optional[int] = Field(None, ge=1, le=5)
    info_provided: list[str] = Field(default_factory=list)


class TimelineItem(BaseModel):
    time_min: int = 0
    kind: str
    label: str


class AgentPanel(BaseModel):
    """Agent Assessment panel payload for the patient detail UI."""

    status: str
    status_label: str
    decision_source: str
    baseline_used: bool = False
    priority: Optional[int] = None
    urgency: str = ""
    care_pathway: str = ""
    monitoring_plan: str = ""
    confidence: Optional[float] = None
    reason_summary: str = ""
    key_evidence: list[str] = Field(default_factory=list)
    information_gaps: list[str] = Field(default_factory=list)
    agent_priority: Optional[int] = None
    baseline_priority: Optional[int] = None
    agreement: Optional[bool] = None
    disagreement_reason: Optional[str] = None
    timeline: list[TimelineItem] = Field(default_factory=list)
    human_review_required: bool = True
    clinician_action: Optional[str] = None


class BoardRow(BaseModel):
    """One patient as shown on the live triage board."""

    patient_id: str
    display_name: Optional[str] = None
    age_years: float
    age_band: str
    sex: str = "O"
    chief_complaint: str
    arrival_epoch_min: int
    # Effective priority: clinician override if present, else agent (or fallback) rec.
    acuity: int
    # Deterministic baseline for evaluation / display. Never an auto-override.
    engine_acuity: int
    overridden: bool = False
    override_direction: Optional[str] = None
    provisional_acuity: int
    escalated_for_uncertainty: bool
    confidence: float
    confidence_band: ConfidenceBand
    routed_to_nurse: bool
    red_flag_count: int
    top_drivers: list[str]
    clocks: list[str]
    placement: str
    monitoring_tier: MonitoringTier
    expected_acuity: Optional[int] = None
    decision_source: str = "deterministic_fallback"  # agent | deterministic_fallback
    baseline_used: bool = False
    agent_priority: Optional[int] = None
    baseline_priority: Optional[int] = None
    clinician_priority: Optional[int] = None
    agent_baseline_agreement: Optional[bool] = None
    agent_clinician_agreement: Optional[bool] = None
    baseline_clinician_agreement: Optional[bool] = None
    agent_status: str = "COMPLETED"
    clinician_action: Optional[str] = None


class PatientUpdateRequest(BaseModel):
    """Payload for submitting requested information to a patient."""
    note: str = Field(..., min_length=1)
    confirm: bool = Field(False, description="If true, apply the reassessment to the board")


class PatientUpdateResponse(BaseModel):
    """Response from the update endpoint with the new prediction."""
    patient_id: str
    parsed: ParsedIntake
    result: TriageResult
    explanation: Explanation
    needs_more_information: bool = False
    information_gaps: list[str] = Field(default_factory=list)
    live_priority: Optional[int] = None
    confirmed: bool = False
    detail: Optional[PatientDetail] = None


class BoardSummary(BaseModel):
    total: int
    by_acuity: dict[int, int]
    routed_to_nurse: int
    low_confidence: int
    escalated_for_uncertainty: int
    active_clocks: int
    overrides: int
    agent_decisions: int = 0
    fallback_decisions: int = 0
    baseline_used_count: int = 0
    agent_baseline_disagreements: int = 0


class BoardResponse(BaseModel):
    surge_factor: int
    model_version: str
    summary: BoardSummary
    rows: list[BoardRow]


class PatientDetail(BaseModel):
    """Everything behind one board row, for the drill-down panel."""

    row: BoardRow
    result: TriageResult  # deterministic baseline / evaluation bundle
    audit: list[AuditRecord]
    decision_source: str = "deterministic_fallback"
    baseline_used: bool = False
    evaluation: Optional[dict] = None
    agent_reason_summary: Optional[str] = None
    agent: Optional[AgentPanel] = None



# --- LLM: free-text intake, explanations, telemetry ---
#
# The model does exactly two jobs, both at the edges of the system: it reads a
# messy free-text note into structured fields, and it rewrites a decision the
# engine already made into plain language. It never sets an acuity. Everything
# here is best-effort with a deterministic fallback, so the demo runs with no
# API key and survives a bad network.


class IntakeExtraction(BaseModel):
    """Structured fields pulled out of a free-text note. All optional."""

    patient_name: Optional[str] = None
    age_years: Optional[float] = None
    sex: Optional[str] = None
    arrival_mode: Optional[str] = None
    chief_complaint: Optional[str] = None
    pain_score: Optional[int] = None
    responsiveness: Optional[str] = None
    heart_rate: Optional[float] = None
    resp_rate: Optional[float] = None
    sbp: Optional[float] = None
    dbp: Optional[float] = None
    spo2: Optional[float] = None
    temp_c: Optional[float] = None
    onset_minutes: Optional[int] = None
    history: list[str] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)
    allergies: list[str] = Field(default_factory=list)


class ParsedIntake(BaseModel):
    """A free-text note turned into a scoreable patient."""

    patient: Patient
    fields_found: list[str]
    source: str  # gemini | rule-based
    note: Optional[str] = None
    raw_text: str


class Explanation(BaseModel):
    """A plain-language gloss on a decision the engine already made."""

    text: str
    source: str  # gemini | template
    verified: bool
    patient_id: Optional[str] = None


class LLMCall(BaseModel):
    """One telemetry record for a model call (or its deterministic fallback)."""

    task: str  # parse | explain
    provider: str  # gemini | rule-based
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0
    cached: bool = False
    ok: bool = True
    fell_back: bool = False
    est_cost_usd: float = 0.0
    error: Optional[str] = None
    timestamp_utc: str


class LLMStatus(BaseModel):
    provider_mode: str  # gemini | rule-based
    model: str
    configured_mode: str  # auto | gemini | stub
    key_present: bool
    package_available: bool
    live: bool  # calling a live model provider


class TelemetrySummary(BaseModel):
    status: LLMStatus
    total_calls: int
    cache_hits: int
    fallbacks: int
    errors: int
    avg_latency_ms: float
    total_input_tokens: int
    total_output_tokens: int
    est_cost_usd: float
    by_task: dict[str, int]
    recent: list[LLMCall]


class IntakeRequest(BaseModel):
    text: str = Field(min_length=1, description="a free-text triage note")
    add_to_board: bool = False
    preview_patient_id: Optional[str] = None


class IntakeResponse(BaseModel):
    parsed: ParsedIntake
    result: TriageResult
    explanation: Explanation
    calls: list[LLMCall]
    added_patient_id: Optional[str] = None
    preview_patient_id: Optional[str] = None
    # When true, UI must not present engine ESI as a firm triage decision.
    needs_more_information: bool = False
    information_gaps: list[str] = Field(default_factory=list)
    decision_source: str = "deterministic_fallback"
    live_priority: Optional[int] = None
    agent_status: Optional[str] = None
