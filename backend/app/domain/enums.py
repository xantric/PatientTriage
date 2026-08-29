"""Enums for the agent-first domain."""

from __future__ import annotations

from enum import Enum


class Sex(str, Enum):
    male = "M"
    female = "F"
    other = "O"


class ArrivalMode(str, Enum):
    walk_in = "walk_in"
    ambulance = "ambulance"
    wheelchair = "wheelchair"
    carried = "carried"


class Responsiveness(str, Enum):
    """AVPU: Alert, Voice, Pain, Unresponsive."""

    alert = "A"
    voice = "V"
    pain = "P"
    unresponsive = "U"


class ConfidenceBand(str, Enum):
    high = "high"
    medium = "medium"
    low = "low"


class AgentStatus(str, Enum):
    """Lifecycle of one TriageAgentState session."""

    INITIALIZING = "INITIALIZING"
    OBSERVING = "OBSERVING"
    GATHERING_INFORMATION = "GATHERING_INFORMATION"
    REASONING = "REASONING"
    RECOMMENDING = "RECOMMENDING"
    AWAITING_HUMAN = "AWAITING_HUMAN"
    ESCALATED = "ESCALATED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class EventKind(str, Enum):
    arrival = "arrival"
    vitals_recorded = "vitals_recorded"
    info_provided = "info_provided"
    recheck = "recheck"
    ratchet = "ratchet"
    backstop = "backstop"
    recommendation = "recommendation"
    clinician_decision = "clinician_decision"
    note = "note"


class ObservationSource(str, Enum):
    intake = "intake"
    tool = "tool"
    watcher = "watcher"
    clinician = "clinician"
    system = "system"


class ToolCallStatus(str, Enum):
    ok = "ok"
    error = "error"
    skipped = "skipped"


class ClinicianAction(str, Enum):
    """HITL actions. Final authority sits with the clinician."""

    accept = "accept"
    modify = "modify"
    override = "override"
    request_more_information = "request_more_information"
    escalate = "escalate"
    # Legacy aliases kept for older fixtures / API callers.
    reject = "reject"
    request_info = "request_info"


class AuditAction(str, Enum):
    assessment_started = "assessment_started"
    tool_called = "tool_called"
    observation_added = "observation_added"
    recommendation_issued = "recommendation_issued"
    info_requested = "info_requested"
    clinician_decision = "clinician_decision"
    watcher_event = "watcher_event"
    status_changed = "status_changed"
    fallback = "fallback"
    reset = "reset"


class WatcherQueueStatus(str, Enum):
    waiting = "waiting"
    in_treatment = "in_treatment"
    done = "done"
    not_queued = "not_queued"


class TriageEventType(str, Enum):
    """Event-driven reassessment triggers. Watcher observes; agent recommends."""

    PATIENT_ARRIVAL = "PATIENT_ARRIVAL"
    NEW_VITALS = "NEW_VITALS"
    VITAL_TREND_CHANGE = "VITAL_TREND_CHANGE"
    WAIT_TIMEOUT = "WAIT_TIMEOUT"
    DETERIORATION_DETECTED = "DETERIORATION_DETECTED"
    NEW_INFORMATION = "NEW_INFORMATION"
    CLINICIAN_DECISION = "CLINICIAN_DECISION"


class DecisionSource(str, Enum):
    """Who produced the live recommendation on TriageAgentState."""

    agent = "agent"
    deterministic_fallback = "deterministic_fallback"
