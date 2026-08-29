"""Event-driven waiting-room Watcher.

Preserves: attention budget, deterioration simulation, unsafe-wait detection,
3x surge load, upward-only monitoring floor.

Does NOT produce the final clinical recommendation. Responsibilities:

    OBSERVE → DETECT CHANGE → EMIT TriageEvent

Meaningful events may wake the Gemini agent via ReassessmentBus. Stable ticks
do not invoke the LLM.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.agent.bridge import from_legacy_patient, from_legacy_vitals, to_legacy_vitals
from app.agent.context import ToolContext
from app.agent.events import apply_triage_event
from app.agent.reassessment import ReassessmentBus
from app.data.generator import build_cohort
from app.domain.enums import TriageEventType, WatcherQueueStatus
from app.domain.models import (
    Patient,
    TriageAgentState,
    TriageEvent,
    Vitals,
)
from app.engine import thresholds as T
from app.engine.deterioration import observe_vitals
from app.engine.pipeline import triage
from app.models import QueueMetric, SimReport, WatcherEvent

TICK_MIN = 5
WATCHER_BUDGET = 2
PROVIDER_SLOTS = 3
HORIZON_CAP_MIN = 24 * 60
SERVICE_MIN: dict[int, int] = {1: 45, 2: 40, 3: 30, 4: 20, 5: 10}
SAFE_WAIT = T.SAFE_WAIT_MINUTES


def _vitals_worsened(before: Vitals, after: Vitals) -> tuple[bool, list[str]]:
    """Detect clinically meaningful deterioration from vitals alone."""

    notes: list[str] = []
    if before.spo2 is not None and after.spo2 is not None and after.spo2 <= before.spo2 - 3:
        notes.append(f"SpO2 {before.spo2} → {after.spo2}")
    if (
        before.heart_rate is not None
        and after.heart_rate is not None
        and after.heart_rate >= before.heart_rate + 15
    ):
        notes.append(f"HR {before.heart_rate} → {after.heart_rate}")
    if before.sbp is not None and after.sbp is not None and after.sbp <= before.sbp - 15:
        notes.append(f"SBP {before.sbp} → {after.sbp}")
    if (
        before.resp_rate is not None
        and after.resp_rate is not None
        and after.resp_rate >= before.resp_rate + 6
    ):
        notes.append(f"RR {before.resp_rate} → {after.resp_rate}")
    return (bool(notes), notes)


def _trend_changed(before: Vitals, after: Vitals) -> bool:
    worsened, _ = _vitals_worsened(before, after)
    if worsened:
        return True
    # Milder movement still counts as a trend signal for logging.
    if before.spo2 is not None and after.spo2 is not None and after.spo2 != before.spo2:
        return abs(after.spo2 - before.spo2) >= 2
    if (
        before.heart_rate is not None
        and after.heart_rate is not None
        and abs(after.heart_rate - before.heart_rate) >= 10
    ):
        return True
    return False


@dataclass
class WatchedPatient:
    """Runtime waiting-room entry. Monitoring floor is upward-only."""

    patient: Patient
    baseline_vitals: Vitals
    state: TriageAgentState
    # Sim-only profile name; never passed to agent tools / Gemini as ground truth.
    deteriorates: Optional[str] = None
    # Reference acuity from deterministic engine for queue priority / safe wait only.
    reference_acuity: int = 3
    monitoring_floor: int = 3
    arrival_min: int = 0
    last_contact_min: int = 0
    status: str = "waiting"
    treatment_end_min: Optional[int] = None
    overdue_flagged: bool = False
    last_observed: Optional[Vitals] = None

    @property
    def acuity(self) -> int:
        """Queue priority: most urgent of monitoring floor and reference."""

        return min(self.monitoring_floor, self.reference_acuity)

    @property
    def next_due_min(self) -> int:
        return self.last_contact_min + SAFE_WAIT[self.acuity]


@dataclass
class EventWatcherReport:
    """Agent-era simulation report plus legacy SimReport compatibility fields."""

    label: str
    surge_factor: int
    ticks: int
    total_patients: int
    total_deteriorations: int
    total_backstops: int
    down_ratchets: int  # must stay 0 (upward-only invariant)
    peak_waiting: int
    peak_unsafe_wait_min: int
    triage_events: list[TriageEvent] = field(default_factory=list)
    legacy_events: list[WatcherEvent] = field(default_factory=list)
    metrics: list[QueueMetric] = field(default_factory=list)
    llm_invocations: int = 0
    reassessment_count: int = 0
    states: dict[str, TriageAgentState] = field(default_factory=dict)

    def to_sim_report(self) -> SimReport:
        return SimReport(
            label=self.label,
            surge_factor=self.surge_factor,
            ticks=self.ticks,
            total_patients=self.total_patients,
            total_ratchets=self.total_deteriorations,
            total_backstops=self.total_backstops,
            down_ratchets=self.down_ratchets,
            peak_waiting=self.peak_waiting,
            peak_unsafe_wait_min=self.peak_unsafe_wait_min,
            events=self.legacy_events,
            metrics=self.metrics,
        )


class EventDrivenWatcher:
    """Waiting-room Watcher that emits TriageEvents and optionally wakes the agent."""

    def __init__(
        self,
        *,
        bus: Optional[ReassessmentBus] = None,
        budget: int = WATCHER_BUDGET,
        slots: int = PROVIDER_SLOTS,
        tick: int = TICK_MIN,
    ) -> None:
        self.bus = bus
        self.budget = budget
        self.slots = slots
        self.tick = tick

    def _emit(
        self,
        *,
        entry: WatchedPatient,
        event: TriageEvent,
        collected: list[TriageEvent],
        context: ToolContext,
    ) -> None:
        collected.append(event)
        if self.bus is not None:
            self.bus.handle(
                event=event,
                patient=entry.patient,
                state=entry.state,
                context=context,
            )
        else:
            apply_triage_event(entry.state, event)

        # Sync monitoring floor: always the more urgent of watcher vs state.
        if entry.state.monitoring_priority_floor is None:
            entry.state.monitoring_priority_floor = entry.monitoring_floor
        else:
            entry.monitoring_floor = min(
                entry.monitoring_floor, entry.state.monitoring_priority_floor
            )
            entry.state.monitoring_priority_floor = entry.monitoring_floor

    def simulate(self, surge_factor: int = 1) -> EventWatcherReport:
        cohort = build_cohort(surge_factor=surge_factor)
        pending = sorted(cohort, key=lambda p: p.arrival_epoch_min)
        entries: dict[str, WatchedPatient] = {}
        triage_events: list[TriageEvent] = []
        legacy_events: list[WatcherEvent] = []
        metrics: list[QueueMetric] = []
        context = ToolContext()

        total_deteriorations = 0
        down_ratchets = 0
        total_backstops = 0
        peak_waiting = 0
        peak_unsafe = 0
        ticks = 0
        t = 0

        while True:
            # 1. Admissions
            while pending and pending[0].arrival_epoch_min <= t:
                legacy_p = pending.pop(0)
                domain_p = from_legacy_patient(legacy_p)
                intake_vitals = from_legacy_vitals(legacy_p.vitals)
                # Reference acuity for safe-wait / queue only (not agent recommendation).
                ref = triage(legacy_p).adjudicator.acuity
                state = TriageAgentState(
                    patient_id=domain_p.patient_id,
                    patient_context=domain_p.context,
                    latest_vitals=intake_vitals,
                    monitoring_priority_floor=ref,
                )
                entry = WatchedPatient(
                    patient=domain_p,
                    baseline_vitals=intake_vitals.model_copy(deep=True),
                    state=state,
                    deteriorates=legacy_p.deteriorates,
                    reference_acuity=ref,
                    monitoring_floor=ref,
                    arrival_min=legacy_p.arrival_epoch_min,
                    last_contact_min=legacy_p.arrival_epoch_min,
                    last_observed=intake_vitals.model_copy(deep=True),
                )
                entries[domain_p.patient_id] = entry
                context.patients[domain_p.patient_id] = domain_p
                context.agent_states[domain_p.patient_id] = state

                arrival = TriageEvent(
                    event_type=TriageEventType.PATIENT_ARRIVAL,
                    patient_id=domain_p.patient_id,
                    time_min=t,
                    summary=(
                        f"Patient arrived; monitoring floor P{ref}; "
                        f"{domain_p.context.chief_complaint[:80]}"
                    ),
                    payload={
                        "monitoring_priority": ref,
                        "vitals": intake_vitals.model_dump(),
                    },
                    source="watcher",
                    triggers_reassessment=True,
                )
                self._emit(
                    entry=entry, event=arrival, collected=triage_events, context=context
                )
                legacy_events.append(
                    WatcherEvent(
                        time_min=t,
                        patient_id=domain_p.patient_id,
                        kind="arrival",
                        detail=arrival.summary[:120],
                        after_acuity=ref,
                    )
                )

            # 2. Discharge finished treatments
            for e in entries.values():
                if (
                    e.status == "in_treatment"
                    and e.treatment_end_min is not None
                    and e.treatment_end_min <= t
                ):
                    e.status = "done"
                    e.state.watcher_state.queue_status = WatcherQueueStatus.done

            # 3. Assign provider slots (sickest first)
            in_treatment = sum(1 for e in entries.values() if e.status == "in_treatment")
            free = max(0, self.slots - in_treatment)
            waiting = sorted(
                (e for e in entries.values() if e.status == "waiting"),
                key=lambda e: (e.acuity, e.arrival_min),
            )
            for e in waiting[:free]:
                e.status = "in_treatment"
                e.treatment_end_min = t + SERVICE_MIN[e.acuity]
                e.state.watcher_state.queue_status = WatcherQueueStatus.in_treatment
                legacy_events.append(
                    WatcherEvent(
                        time_min=t,
                        patient_id=e.patient.patient_id,
                        kind="treatment_start",
                        detail=f"taken to treatment (monitor P{e.acuity})",
                        after_acuity=e.acuity,
                    )
                )

            # 4. Watcher re-check: prioritize highest-risk, overdue, deteriorating
            waiting = [e for e in entries.values() if e.status == "waiting"]
            due = [e for e in waiting if t >= e.next_due_min]

            def _priority_key(e: WatchedPatient) -> tuple:
                overdue = max(0, t - e.next_due_min)
                deteriorating = 1 if e.deteriorates else 0
                # acuity asc (1 first), deteriorating first, most overdue first
                return (e.acuity, -deteriorating, -overdue)

            due.sort(key=_priority_key)
            ratchets_this_tick = 0
            checked = 0
            for e in due:
                if checked >= self.budget:
                    break
                before_floor = e.monitoring_floor
                before_vitals = e.last_observed or e.baseline_vitals
                elapsed = t - e.arrival_min
                fresh_legacy = observe_vitals(
                    to_legacy_vitals(e.baseline_vitals),
                    e.deteriorates,
                    elapsed,
                )
                fresh = from_legacy_vitals(fresh_legacy)
                checked += 1
                e.last_contact_min = t
                e.overdue_flagged = False

                # NEW_VITALS always recorded (physical observation).
                new_vitals_event = TriageEvent(
                    event_type=TriageEventType.NEW_VITALS,
                    patient_id=e.patient.patient_id,
                    time_min=t,
                    summary=f"Re-check vitals recorded at t={t}.",
                    payload={"vitals": fresh.model_dump()},
                    source="watcher",
                    triggers_reassessment=False,
                )
                self._emit(
                    entry=e,
                    event=new_vitals_event,
                    collected=triage_events,
                    context=context,
                )

                worsened, notes = _vitals_worsened(before_vitals, fresh)
                # Reference acuity for monitoring floor only (never agent recommendation).
                from app.agent.bridge import to_legacy_patient

                ref_patient = to_legacy_patient(e.patient, fresh)
                ref_patient.deteriorates = None
                ref_acuity = triage(ref_patient).adjudicator.acuity

                proposed_floor = before_floor
                if ref_acuity < proposed_floor:
                    proposed_floor = ref_acuity
                if worsened and proposed_floor >= before_floor:
                    proposed_floor = max(1, before_floor - 1)

                if proposed_floor < before_floor:
                    e.monitoring_floor = proposed_floor
                    e.reference_acuity = min(e.reference_acuity, ref_acuity)
                    total_deteriorations += 1
                    ratchets_this_tick += 1
                    detail = (
                        "; ".join(notes) if notes else f"monitor floor → P{proposed_floor}"
                    )
                    det = TriageEvent(
                        event_type=TriageEventType.DETERIORATION_DETECTED,
                        patient_id=e.patient.patient_id,
                        time_min=t,
                        summary=f"Deterioration detected: {detail}",
                        payload={
                            "monitoring_priority": e.monitoring_floor,
                            "before_floor": before_floor,
                            "after_floor": e.monitoring_floor,
                            "vitals_before": before_vitals.model_dump(),
                            "vitals_after": fresh.model_dump(),
                            "changes": notes,
                        },
                        source="watcher",
                        triggers_reassessment=True,
                    )
                    self._emit(
                        entry=e, event=det, collected=triage_events, context=context
                    )
                    legacy_events.append(
                        WatcherEvent(
                            time_min=t,
                            patient_id=e.patient.patient_id,
                            kind="ratchet",
                            detail=det.summary[:160],
                            before_acuity=before_floor,
                            after_acuity=e.monitoring_floor,
                        )
                    )
                elif ref_acuity > before_floor:
                    # Deterministic engine would soften acuity; Watcher refuses.
                    down_ratchets += 1
                elif _trend_changed(before_vitals, fresh):
                    trend = TriageEvent(
                        event_type=TriageEventType.VITAL_TREND_CHANGE,
                        patient_id=e.patient.patient_id,
                        time_min=t,
                        summary="Vital trend change on re-check.",
                        payload={
                            "vitals_before": before_vitals.model_dump(),
                            "vitals_after": fresh.model_dump(),
                        },
                        source="watcher",
                        triggers_reassessment=True,
                    )
                    self._emit(
                        entry=e, event=trend, collected=triage_events, context=context
                    )

                e.last_observed = fresh

            # 5. Timer backstop
            unsafe_now = 0
            for e in waiting:
                over = t - e.next_due_min
                if over > 0:
                    unsafe_now = max(unsafe_now, over)
                    if not e.overdue_flagged:
                        e.overdue_flagged = True
                        total_backstops += 1
                        timeout = TriageEvent(
                            event_type=TriageEventType.WAIT_TIMEOUT,
                            patient_id=e.patient.patient_id,
                            time_min=t,
                            summary=(
                                f"Unsafe wait: monitor P{e.acuity} not re-assessed for "
                                f"{t - e.last_contact_min} min "
                                f"(limit {SAFE_WAIT[e.acuity]})"
                            ),
                            payload={
                                "unsafe_wait_min": over,
                                "monitoring_priority": e.acuity,
                            },
                            source="watcher",
                            triggers_reassessment=True,
                        )
                        self._emit(
                            entry=e,
                            event=timeout,
                            collected=triage_events,
                            context=context,
                        )
                        legacy_events.append(
                            WatcherEvent(
                                time_min=t,
                                patient_id=e.patient.patient_id,
                                kind="backstop",
                                detail=timeout.summary[:160],
                                after_acuity=e.acuity,
                            )
                        )

            # 6. Metrics
            n_waiting = len(waiting)
            n_treat = sum(1 for e in entries.values() if e.status == "in_treatment")
            n_done = sum(1 for e in entries.values() if e.status == "done")
            peak_waiting = max(peak_waiting, n_waiting)
            peak_unsafe = max(peak_unsafe, unsafe_now)
            metrics.append(
                QueueMetric(
                    time_min=t,
                    arrived=len(entries),
                    waiting=n_waiting,
                    in_treatment=n_treat,
                    done=n_done,
                    rechecks_due=len(due),
                    rechecks_done=checked,
                    longest_unsafe_wait_min=unsafe_now,
                    ratchets=ratchets_this_tick,
                )
            )
            ticks += 1

            done_all = (
                not pending
                and all(e.status == "done" for e in entries.values())
                and entries
            )
            if done_all or t >= HORIZON_CAP_MIN:
                break
            t += self.tick

        llm_n = self.bus.llm_invocations if self.bus else 0
        reassess_n = (
            sum(1 for r in self.bus.records if r.llm_invoked) if self.bus else 0
        )
        return EventWatcherReport(
            label=f"surge x{surge_factor}" if surge_factor > 1 else "normal load",
            surge_factor=surge_factor,
            ticks=ticks,
            total_patients=len(entries),
            total_deteriorations=total_deteriorations,
            total_backstops=total_backstops,
            down_ratchets=down_ratchets,
            peak_waiting=peak_waiting,
            peak_unsafe_wait_min=peak_unsafe,
            triage_events=triage_events,
            legacy_events=legacy_events,
            metrics=metrics,
            llm_invocations=llm_n,
            reassessment_count=reassess_n,
            states={pid: e.state for pid, e in entries.items()},
        )


def simulate_event_driven(
    surge_factor: int = 1,
    *,
    bus: Optional[ReassessmentBus] = None,
    budget: int = WATCHER_BUDGET,
) -> EventWatcherReport:
    return EventDrivenWatcher(bus=bus, budget=budget).simulate(surge_factor=surge_factor)
