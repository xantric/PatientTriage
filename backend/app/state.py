"""In-memory department state behind the API.

Holds the current cohort, the engine's triage results, any clinician overrides,
and the audit trail. One process-wide instance backs the live board so an
override made in the UI is visible on the next refresh.

The engine's score is never overwritten. An override is stored alongside it, so
the board can always show both "what Sentinel said" and "what the clinician
decided", which is the honest version of a human-in-the-loop tool.
"""

from __future__ import annotations

from typing import Optional

from app import __version__ as ENGINE_VERSION
from app.audit import AuditLog, direction_for
from app.data.generator import build_cohort
from app.engine import thresholds as T
from app.engine.pipeline import triage
from app.models import (
    BoardResponse,
    BoardRow,
    BoardSummary,
    OverrideRequest,
    Patient,
    PatientDetail,
    TriageResult,
)


def _clock_labels(result: TriageResult) -> list[str]:
    labels: list[str] = []
    for c in result.adjudicator.time_critical_clocks:
        if c.minutes_remaining is None:
            labels.append(f"{c.name}: onset unknown")
        elif c.minutes_remaining > 0:
            labels.append(f"{c.name}: {c.minutes_remaining} min left")
        else:
            labels.append(f"{c.name}: window missed")
    return labels


class Department:
    def __init__(self, surge_factor: int = 1) -> None:
        self.audit = AuditLog()
        self.load(surge_factor)

    # --- lifecycle ---

    def load(self, surge_factor: int = 1) -> None:
        """Build (or rebuild) the cohort and score everyone. Clears overrides."""

        self.surge_factor = surge_factor
        self.patients: dict[str, Patient] = {}
        self.results: dict[str, TriageResult] = {}
        self.overrides: dict[str, int] = {}
        self._intake_seq = 0
        for p in build_cohort(surge_factor=surge_factor):
            self.patients[p.patient_id] = p
            self.results[p.patient_id] = triage(p)

    def add_patient(self, patient: Patient) -> str:
        """Register an already-built patient (from free-text intake) on the board."""

        self._intake_seq += 1
        pid = f"N-{self._intake_seq:03d}"
        latest = max((p.arrival_epoch_min for p in self.patients.values()), default=0)
        stamped = patient.model_copy(update={
            "patient_id": pid,
            "arrival_epoch_min": latest + 1,
        })
        self.patients[pid] = stamped
        self.results[pid] = triage(stamped)
        return pid

    def reset(self, surge_factor: int, actor: str = "system") -> None:
        """Reload the board. The audit trail survives, because it must."""

        self.load(surge_factor)
        self.audit.append(
            patient_id="-",
            action="reset",
            actor=actor,
            actor_role="system",
            reason=f"board reloaded at surge factor x{surge_factor}",
        )

    # --- reads ---

    def effective_acuity(self, patient_id: str) -> int:
        engine = self.results[patient_id].adjudicator.acuity
        return self.overrides.get(patient_id, engine)

    def row(self, patient_id: str) -> BoardRow:
        p = self.patients[patient_id]
        r = self.results[patient_id]
        adj, interp = r.adjudicator, r.interpreter
        override = self.overrides.get(patient_id)
        effective = override if override is not None else adj.acuity
        return BoardRow(
            patient_id=p.patient_id,
            display_name=p.display_name,
            age_years=p.age_years,
            age_band=interp.age_band,
            chief_complaint=p.chief_complaint,
            arrival_epoch_min=p.arrival_epoch_min,
            acuity=effective,
            engine_acuity=adj.acuity,
            overridden=override is not None,
            override_direction=direction_for(adj.acuity, override) if override is not None else None,
            provisional_acuity=adj.provisional_acuity,
            escalated_for_uncertainty=adj.escalated_for_uncertainty,
            confidence=interp.confidence,
            confidence_band=interp.confidence_band,
            routed_to_nurse=adj.routed_to_nurse,
            red_flag_count=len(interp.red_flags),
            top_drivers=adj.top_drivers,
            clocks=_clock_labels(r),
            placement=adj.placement,
            monitoring_tier=adj.monitoring_tier,
            expected_acuity=p.expected_acuity,
        )

    def board(self) -> BoardResponse:
        rows = [self.row(pid) for pid in self.patients]
        # Sickest first, then by arrival so the board is stable between refreshes.
        rows.sort(key=lambda r: (r.acuity, r.arrival_epoch_min))
        by_acuity = {level: 0 for level in range(1, 6)}
        for r in rows:
            by_acuity[r.acuity] += 1
        summary = BoardSummary(
            total=len(rows),
            by_acuity=by_acuity,
            routed_to_nurse=sum(1 for r in rows if r.routed_to_nurse),
            low_confidence=sum(1 for r in rows if r.confidence_band.value == "low"),
            escalated_for_uncertainty=sum(1 for r in rows if r.escalated_for_uncertainty),
            active_clocks=sum(len(r.clocks) for r in rows),
            overrides=len(self.overrides),
        )
        return BoardResponse(
            surge_factor=self.surge_factor,
            model_version=ENGINE_VERSION,
            summary=summary,
            rows=rows,
        )

    def detail(self, patient_id: str) -> PatientDetail:
        return PatientDetail(
            row=self.row(patient_id),
            result=self.results[patient_id],
            audit=self.audit.for_patient(patient_id),
        )

    # --- writes ---

    def apply_override(self, req: OverrideRequest) -> PatientDetail:
        """Record a clinician override and log it. Raises KeyError if unknown."""

        r = self.results[req.patient_id]
        before = self.effective_acuity(req.patient_id)
        self.overrides[req.patient_id] = req.new_acuity
        self.audit.append(
            patient_id=req.patient_id,
            action="override",
            actor=req.actor,
            actor_role=req.actor_role,
            reason=req.reason,
            before_acuity=before,
            after_acuity=req.new_acuity,
            engine_confidence=r.interpreter.confidence,
            engine_drivers=r.adjudicator.top_drivers,
        )
        return self.detail(req.patient_id)

    def safe_wait_minutes(self, patient_id: str) -> Optional[int]:
        return T.SAFE_WAIT_MINUTES.get(self.effective_acuity(patient_id))


DEPARTMENT = Department()
