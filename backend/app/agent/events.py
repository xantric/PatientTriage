"""Apply TriageEvents to agent state.

Watcher records physical observations. Agent recommendations never erase
historical deterioration events.
"""

from __future__ import annotations

from typing import Optional

from app.domain.enums import (
    AgentStatus,
    EventKind,
    ObservationSource,
    TriageEventType,
    WatcherQueueStatus,
)
from app.domain.models import (
    AgentObservation,
    PatientEvent,
    TriageAgentState,
    TriageEvent,
    VitalObservation,
    Vitals,
)


# Events that should wake the Gemini agent (not every sim tick).
MEANINGFUL_REASSESSMENT_TYPES = frozenset(
    {
        TriageEventType.PATIENT_ARRIVAL,
        TriageEventType.DETERIORATION_DETECTED,
        TriageEventType.VITAL_TREND_CHANGE,
        TriageEventType.WAIT_TIMEOUT,
        TriageEventType.NEW_INFORMATION,
    }
)


def raise_monitoring_floor(state: TriageAgentState, priority: Optional[int]) -> None:
    """Upward-only: adopt a more urgent (lower) monitoring floor, never softer."""

    if priority is None:
        return
    if state.monitoring_priority_floor is None:
        state.monitoring_priority_floor = priority
        return
    if priority < state.monitoring_priority_floor:
        state.monitoring_priority_floor = priority


def append_triage_event(state: TriageAgentState, event: TriageEvent) -> TriageEvent:
    """Append-only. Never removes or rewrites prior DETERIORATION_DETECTED rows."""

    state.triage_events.append(event)
    state.current_observations.append(
        AgentObservation(
            summary=event.summary[:280],
            source=(
                ObservationSource.watcher
                if event.source == "watcher"
                else ObservationSource.system
            ),
            time_min=event.time_min,
            evidence_refs=[event.event_id],
        )
    )
    kind_map = {
        TriageEventType.PATIENT_ARRIVAL: EventKind.arrival,
        TriageEventType.NEW_VITALS: EventKind.vitals_recorded,
        TriageEventType.VITAL_TREND_CHANGE: EventKind.recheck,
        TriageEventType.WAIT_TIMEOUT: EventKind.backstop,
        TriageEventType.DETERIORATION_DETECTED: EventKind.ratchet,
        TriageEventType.NEW_INFORMATION: EventKind.info_provided,
        TriageEventType.CLINICIAN_DECISION: EventKind.clinician_decision,
    }
    state.events.append(
        PatientEvent(
            patient_id=event.patient_id,
            time_min=event.time_min,
            kind=kind_map.get(event.event_type, EventKind.note),
            summary=event.summary[:280],
            related_recommendation_id=event.related_recommendation_id,
        )
    )
    return event


def apply_new_vitals(
    state: TriageAgentState,
    vitals: Vitals,
    *,
    time_min: int,
    source: ObservationSource = ObservationSource.watcher,
    note: str = "",
) -> VitalObservation:
    obs = VitalObservation(
        recorded_at_min=time_min,
        vitals=vitals,
        source=source,
        note=note[:280],
    )
    state.vital_history.append(obs)
    state.latest_vitals = vitals
    return obs


def apply_triage_event(state: TriageAgentState, event: TriageEvent) -> None:
    """Update agent/watcher state from an emitted event. Append-only history."""

    prior_deterioration_ids = {e.event_id for e in state.deterioration_events()}

    append_triage_event(state, event)

    ws = state.watcher_state
    if event.event_type == TriageEventType.PATIENT_ARRIVAL:
        ws.queue_status = WatcherQueueStatus.waiting
        ws.arrival_min = event.time_min
        ws.last_contact_min = event.time_min
        floor = event.payload.get("monitoring_priority")
        raise_monitoring_floor(state, floor)
        if floor is not None:
            from app.engine import thresholds as T

            ws.next_due_min = event.time_min + T.SAFE_WAIT_MINUTES.get(floor, 30)
        state.status = AgentStatus.OBSERVING

    elif event.event_type == TriageEventType.NEW_VITALS:
        raw = event.payload.get("vitals")
        if isinstance(raw, dict):
            apply_new_vitals(
                state,
                Vitals.model_validate(raw),
                time_min=event.time_min,
                note=event.summary[:280],
            )
        ws.last_contact_min = event.time_min
        ws.last_recheck_min = event.time_min
        ws.overdue = False
        floor = state.monitoring_priority_floor or 3
        from app.engine import thresholds as T

        ws.next_due_min = event.time_min + T.SAFE_WAIT_MINUTES.get(floor, 30)
        ws.last_event_summary = event.summary[:280]

    elif event.event_type == TriageEventType.VITAL_TREND_CHANGE:
        ws.last_event_summary = event.summary[:280]

    elif event.event_type == TriageEventType.DETERIORATION_DETECTED:
        floor = event.payload.get("monitoring_priority")
        raise_monitoring_floor(state, floor)
        ws.ratchet_count += 1
        ws.last_contact_min = event.time_min
        ws.last_recheck_min = event.time_min
        ws.overdue = False
        ws.last_event_summary = event.summary[:280]
        floor_now = state.monitoring_priority_floor or 3
        from app.engine import thresholds as T

        ws.next_due_min = event.time_min + T.SAFE_WAIT_MINUTES.get(floor_now, 30)
        state.human_review_required = True

    elif event.event_type == TriageEventType.WAIT_TIMEOUT:
        ws.overdue = True
        ws.backstop_fired = True
        ws.unsafe_wait_min = int(event.payload.get("unsafe_wait_min") or 0)
        ws.last_event_summary = event.summary[:280]
        state.human_review_required = True

    elif event.event_type == TriageEventType.NEW_INFORMATION:
        for fact in event.payload.get("facts") or []:
            state.current_observations.append(
                AgentObservation(
                    summary=str(fact)[:280],
                    source=ObservationSource.clinician,
                    time_min=event.time_min,
                )
            )
        state.human_review_required = True

    elif event.event_type == TriageEventType.CLINICIAN_DECISION:
        ws.last_event_summary = event.summary[:280]

    # Invariant: prior deterioration events still present.
    after_ids = {e.event_id for e in state.deterioration_events()}
    if not prior_deterioration_ids.issubset(after_ids):
        raise RuntimeError("deterioration events were erased; this is forbidden")


def should_trigger_reassessment(event: TriageEvent) -> bool:
    if event.triggers_reassessment:
        return True
    return event.event_type in MEANINGFUL_REASSESSMENT_TYPES
