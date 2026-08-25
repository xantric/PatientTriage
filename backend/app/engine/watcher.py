"""Agent 3: Watcher, plus the waiting-room simulation and surge harness.

The Watcher watches everyone who is still waiting. Each tick it spends a fixed
attention budget on the patients who are sickest or most overdue, re-records
their vitals, re-runs triage, and ratchets acuity UP if they have worsened. It
never lowers an acuity. A timer backstop raises an alert whenever a waiting
patient passes the maximum safe wait for their acuity, whether or not the
Watcher had budget to reach them, so a saturated queue can never hide a patient.

`simulate()` runs the whole ED for a run and returns a `SimReport`. Compare a
normal run against `surge_factor=3` to see the board load and the longest unsafe
wait grow while the Watcher reallocates its budget to the highest risk.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.data.generator import build_cohort
from app.engine import thresholds as T
from app.engine.deterioration import observe_vitals
from app.engine.pipeline import triage
from app.models import (
    Patient,
    QueueMetric,
    SimReport,
    TriageResult,
    Vitals,
    WatcherEvent,
)

TICK_MIN = 5              # simulation step
WATCHER_BUDGET = 2       # re-checks the Watcher can perform per tick
PROVIDER_SLOTS = 3       # patients that can be in treatment at once
HORIZON_CAP_MIN = 24 * 60

# How long a treatment ties up a provider slot, by acuity.
SERVICE_MIN: dict[int, int] = {1: 45, 2: 40, 3: 30, 4: 20, 5: 10}

# Maximum time a waiting patient of each acuity should go without being seen
# or re-assessed. Reused from the threshold table so it stays configurable.
SAFE_WAIT = T.SAFE_WAIT_MINUTES


@dataclass
class WaitEntry:
    """Mutable runtime state for one patient in the department."""

    patient: Patient
    baseline: Vitals
    result: TriageResult
    arrival_min: int
    last_contact_min: int
    status: str = "waiting"  # waiting | in_treatment | done
    treatment_end_min: int | None = None
    overdue_flagged: bool = False

    @property
    def acuity(self) -> int:
        return self.result.adjudicator.acuity

    @property
    def next_due_min(self) -> int:
        return self.last_contact_min + SAFE_WAIT[self.acuity]


def _recheck(entry: WaitEntry, now: int) -> TriageResult:
    """Re-record vitals for a waiting patient and re-run triage at time `now`."""

    elapsed = now - entry.arrival_min
    fresh = observe_vitals(entry.baseline, entry.patient.deteriorates, elapsed)
    entry.patient.vitals = fresh
    return triage(entry.patient)


def simulate(
    surge_factor: int = 1,
    budget: int = WATCHER_BUDGET,
    slots: int = PROVIDER_SLOTS,
    tick: int = TICK_MIN,
) -> SimReport:
    cohort = build_cohort(surge_factor=surge_factor)
    pending = sorted(cohort, key=lambda p: p.arrival_epoch_min)
    entries: dict[str, WaitEntry] = {}
    events: list[WatcherEvent] = []
    metrics: list[QueueMetric] = []

    total_ratchets = 0
    down_ratchets = 0
    total_backstops = 0
    peak_waiting = 0
    peak_unsafe = 0

    t = 0
    ticks = 0
    while True:
        # 1. Admissions: everyone whose arrival time has come.
        while pending and pending[0].arrival_epoch_min <= t:
            p = pending.pop(0)
            res = triage(p)
            entries[p.patient_id] = WaitEntry(
                patient=p,
                baseline=p.vitals.model_copy(deep=True),
                result=res,
                arrival_min=p.arrival_epoch_min,
                last_contact_min=p.arrival_epoch_min,
            )
            events.append(
                WatcherEvent(
                    time_min=t,
                    patient_id=p.patient_id,
                    kind="arrival",
                    detail=f"ESI {res.adjudicator.acuity} at intake: {p.chief_complaint[:48]}",
                    after_acuity=res.adjudicator.acuity,
                )
            )

        # 2. Discharge finished treatments.
        for e in entries.values():
            if e.status == "in_treatment" and e.treatment_end_min is not None and e.treatment_end_min <= t:
                e.status = "done"

        # 3. Assign free provider slots to the sickest waiting patients.
        in_treatment = sum(1 for e in entries.values() if e.status == "in_treatment")
        free = max(0, slots - in_treatment)
        waiting = sorted(
            (e for e in entries.values() if e.status == "waiting"),
            key=lambda e: (e.acuity, e.arrival_min),
        )
        for e in waiting[:free]:
            e.status = "in_treatment"
            e.treatment_end_min = t + SERVICE_MIN[e.acuity]
            events.append(
                WatcherEvent(
                    time_min=t,
                    patient_id=e.patient.patient_id,
                    kind="treatment_start",
                    detail=f"taken to treatment (ESI {e.acuity})",
                    after_acuity=e.acuity,
                )
            )

        # 4. Watcher re-check pass, prioritised by risk then overdue time.
        waiting = [e for e in entries.values() if e.status == "waiting"]
        due = [e for e in waiting if t >= e.next_due_min]
        due.sort(key=lambda e: (e.acuity, -(t - e.next_due_min)))
        ratchets_this_tick = 0
        checked = 0
        for e in due:
            if checked >= budget:
                break
            before = e.acuity
            new_res = _recheck(e, t)
            after_raw = new_res.adjudicator.acuity
            e.last_contact_min = t
            e.overdue_flagged = False
            checked += 1
            if after_raw < before:
                # Deteriorated: adopt the more urgent read and ratchet up.
                e.result = new_res
                total_ratchets += 1
                ratchets_this_tick += 1
                events.append(
                    WatcherEvent(
                        time_min=t,
                        patient_id=e.patient.patient_id,
                        kind="ratchet",
                        detail=(
                            f"deterioration on re-check -> {', '.join(new_res.adjudicator.top_drivers[:2])}"
                        ),
                        before_acuity=before,
                        after_acuity=after_raw,
                    )
                )
            elif after_raw == before:
                e.result = new_res  # same acuity, refresh the view
            else:
                # Engine would lower acuity; the Watcher refuses to de-escalate.
                down_ratchets += 1

        # 5. Timer backstop: alert on any waiting patient past their safe wait.
        unsafe_now = 0
        for e in waiting:
            over = t - e.next_due_min
            if over > 0:
                unsafe_now = max(unsafe_now, over)
                if not e.overdue_flagged:
                    e.overdue_flagged = True
                    total_backstops += 1
                    events.append(
                        WatcherEvent(
                            time_min=t,
                            patient_id=e.patient.patient_id,
                            kind="backstop",
                            detail=(
                                f"unsafe wait: ESI {e.acuity} not re-assessed for "
                                f"{t - e.last_contact_min} min (limit {SAFE_WAIT[e.acuity]})"
                            ),
                            after_acuity=e.acuity,
                        )
                    )

        # 6. Board metrics for this tick.
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

        # Stop once everyone has arrived and the department is clear.
        done_all = (
            not pending
            and all(e.status == "done" for e in entries.values())
            and entries
        )
        if done_all or t >= HORIZON_CAP_MIN:
            break
        t += tick

    return SimReport(
        label=f"surge x{surge_factor}" if surge_factor > 1 else "normal load",
        surge_factor=surge_factor,
        ticks=ticks,
        total_patients=len(entries),
        total_ratchets=total_ratchets,
        total_backstops=total_backstops,
        down_ratchets=down_ratchets,
        peak_waiting=peak_waiting,
        peak_unsafe_wait_min=peak_unsafe,
        events=events,
        metrics=metrics,
    )
