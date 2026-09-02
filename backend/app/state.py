"""In-memory department state behind the API.

Primary flow (Phase 7):

    PATIENT → AGENT STATE → GEMINI AGENT → TOOLS → RECOMMENDATION → HITL

The deterministic engine is stored as baseline / fallback / evaluation only.
`results` holds the baseline TriageResult for drill-down and evaluation.
Live board acuity comes from the agent recommendation (or explicit fallback),
then clinician override.

Board cohort load uses the SQLite assessment cache when enabled: cached Gemini
outcomes reload instantly; uncached patients are assessed once and stored.
Without cache, board defaults to deterministic_fallback unless an AgentLLM is
injected or SENTINEL_LIVE_AGENT_BOARD=1.
"""

from __future__ import annotations

import os
from typing import Optional

from app import __version__ as ENGINE_VERSION
from app.agent.evaluation import refresh_evaluation
from app.agent.hitl import ClinicianDecisionInput, HitlError, apply_clinician_decision
from app.agent.orchestrator import AgentLLM
from app.agent.primary import PrimaryAssessmentResult, run_primary_assessment
from app.agent.timeline import build_operational_timeline, status_label
from app.audit import AuditLog, direction_for
from app.cache.assessment_cache import assessment_cache
from app.data.generator import build_cohort
from app.domain.enums import DecisionSource
from app.domain.models import TriageAgentState
from app.engine import thresholds as T
from app.models import (
    AgentPanel,
    BoardResponse,
    BoardRow,
    BoardSummary,
    ConfidenceBand,
    HitlRequest,
    OverrideRequest,
    Patient,
    PatientDetail,
    TimelineItem,
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


def _live_agent_board_enabled() -> bool:
    return (os.getenv("SENTINEL_LIVE_AGENT_BOARD") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


from dataclasses import dataclass
import time

@dataclass
class PendingIntake:
    text: str
    parsed: "ParsedIntake"
    outcome: "PrimaryAssessmentResult"
    explanation: "Explanation"
    calls: list["LLMCall"]
    meta: dict
    expires_at: float


@dataclass
class _CohortSnapshot:
    """In-memory board for one surge level (instant 1x <-> 3x switching)."""

    surge_factor: int
    patients: dict[str, Patient]
    results: dict[str, TriageResult]
    agent_states: dict[str, TriageAgentState]
    decision_sources: dict[str, DecisionSource]
    overrides: dict[str, int]


class Department:
    def __init__(
        self,
        surge_factor: int = 1,
        *,
        agent_llm: Optional[AgentLLM] = None,
        force_fallback: bool = False,
    ) -> None:
        self.audit = AuditLog()
        self.agent_llm = agent_llm
        self.force_fallback = force_fallback
        self._snapshots: dict[int, _CohortSnapshot] = {}
        self.pending_intakes: dict[str, PendingIntake] = {}
        self._intake_seq = 0
        self.load(surge_factor, allow_gemini_warm=False)

    def _snapshot(self) -> _CohortSnapshot:
        return _CohortSnapshot(
            surge_factor=self.surge_factor,
            patients=dict(self.patients),
            results=dict(self.results),
            agent_states=dict(self.agent_states),
            decision_sources=dict(self.decision_sources),
            overrides=dict(self.overrides),
        )

    def _restore(self, snap: _CohortSnapshot) -> None:
        self.surge_factor = snap.surge_factor
        self.patients = dict(snap.patients)
        self.results = dict(snap.results)
        self.agent_states = dict(snap.agent_states)
        self.decision_sources = dict(snap.decision_sources)
        self.overrides = dict(snap.overrides)

    def _save_snapshot(self) -> None:
        self._snapshots[self.surge_factor] = self._snapshot()

    def load(self, surge_factor: int = 1, *, allow_gemini_warm: bool | None = None) -> None:
        """Build the cohort and run primary assessment for everyone."""

        if surge_factor in self._snapshots:
            self._restore(self._snapshots[surge_factor])
            return

        self.surge_factor = surge_factor
        self.patients = {}
        self.results = {}
        self.agent_states = {}
        self.decision_sources = {}
        self.overrides = {}
        if allow_gemini_warm is None:
            allow_gemini_warm = assessment_cache.enabled()
        for p in build_cohort(surge_factor=surge_factor):
            self._register_assessed(p, allow_gemini_warm=allow_gemini_warm)
        self._save_snapshot()

    def _assess(
        self,
        patient: Patient,
        force_fallback: bool = False,
        *,
        allow_gemini_warm: bool = False,
    ) -> PrimaryAssessmentResult:
        use_fallback = force_fallback or self.force_fallback

        if self.agent_llm is not None:
            return run_primary_assessment(
                patient,
                llm=self.agent_llm,
                force_fallback=use_fallback,
            )

        cache_key = assessment_cache.make_key(patient.patient_id, self.surge_factor)
        if assessment_cache.enabled() and not use_fallback:
            cached = assessment_cache.get(cache_key)
            if cached is None and self.surge_factor != 1:
                # Named cases and the first filler block are identical at 1x and 3x.
                base_key = assessment_cache.make_key(patient.patient_id, 1)
                cached = assessment_cache.get(base_key)
                if cached is not None:
                    assessment_cache.put(cache_key, cached)
            if cached is not None:
                return cached

        if allow_gemini_warm and assessment_cache.enabled() and not use_fallback:
            effective_fallback = False
        else:
            effective_fallback = use_fallback or not _live_agent_board_enabled()

        outcome = run_primary_assessment(patient, force_fallback=effective_fallback)

        if (
            assessment_cache.enabled()
            and not use_fallback
            and outcome.decision_source == DecisionSource.agent
        ):
            assessment_cache.put(cache_key, outcome)

        return outcome

    def _register_assessed(
        self,
        patient: Patient,
        force_fallback: bool = False,
        *,
        allow_gemini_warm: bool = False,
    ) -> PrimaryAssessmentResult:
        outcome = self._assess(
            patient,
            force_fallback=force_fallback,
            allow_gemini_warm=allow_gemini_warm,
        )
        pid = patient.patient_id
        self.patients[pid] = patient
        self.results[pid] = outcome.baseline_result
        self.agent_states[pid] = outcome.state
        self.decision_sources[pid] = outcome.decision_source
        self._audit_assessment(outcome)
        return outcome

    def _audit_assessment(self, outcome: PrimaryAssessmentResult) -> None:
        state = outcome.state
        rec = state.current_recommendation
        self.audit.append(
            patient_id=state.patient_id,
            action="agent_assessment",
            actor="sentinel-agent",
            actor_role="agent",
            reason=(
                rec.reason_summary
                if rec is not None
                else f"Assessment via {outcome.decision_source.value}"
            ),
            after_acuity=rec.priority if rec else None,
            engine_confidence=(
                state.baseline_assessment.confidence
                if state.baseline_assessment
                else None
            ),
            engine_drivers=(
                list(state.baseline_assessment.drivers)
                if state.baseline_assessment
                else []
            ),
            assessment_id=state.assessment_id,
            agent_model=(
                "gemini"
                if outcome.decision_source == DecisionSource.agent
                else "deterministic_fallback"
            ),
            agent_version=ENGINE_VERSION,
            agent_action=outcome.decision_source.value,
            tools_used=[c.tool_name for c in state.tool_history],
            iterations=state.iteration_count,
            agent_priority=rec.priority if rec else None,
            agent_confidence=rec.confidence if rec else None,
            baseline_priority=(
                state.baseline_assessment.priority if state.baseline_assessment else None
            ),
            agreement=state.evaluation.agent_baseline_agreement,
            final_priority=rec.priority if rec else None,
        )

    def agent_panel(self, patient_id: str) -> AgentPanel:
        state = self.agent_states[patient_id]
        source = self.decision_sources.get(
            patient_id, DecisionSource.deterministic_fallback
        )
        rec = state.current_recommendation
        cmp_ = state.baseline_comparison
        return AgentPanel(
            status=state.status.value,
            status_label=status_label(state.status),
            decision_source=source.value,
            baseline_used=state.baseline_used,
            priority=rec.priority if rec else None,
            urgency=rec.urgency if rec else "",
            care_pathway=rec.care_pathway if rec else "",
            monitoring_plan=rec.monitoring_plan if rec else "",
            confidence=rec.confidence if rec else None,
            reason_summary=rec.reason_summary if rec else "",
            key_evidence=list(rec.key_evidence) if rec else [],
            information_gaps=list(state.information_gaps),
            agent_priority=state.evaluation.agent_priority,
            baseline_priority=state.evaluation.baseline_priority
            or (
                state.baseline_assessment.priority
                if state.baseline_assessment
                else None
            ),
            agreement=state.evaluation.agent_baseline_agreement,
            disagreement_reason=(
                cmp_.disagreement_reason if cmp_ is not None else None
            ),
            timeline=[
                TimelineItem(**item) for item in build_operational_timeline(state)
            ],
            human_review_required=state.human_review_required,
            clinician_action=self._resolved_clinician_action(patient_id),
        )

    def reserve_next_patient_id(self) -> str:
        self._intake_seq += 1
        return f"N-{self._intake_seq:03d}"

    def inject_assessed_patient(self, patient: Patient, outcome: "PrimaryAssessmentResult") -> str:
        """Inject a pre-assessed patient directly into the board."""
        pid = patient.patient_id
        latest = max((p.arrival_epoch_min for p in self.patients.values()), default=0)
        
        patient.arrival_epoch_min = latest + 1
        
        self.patients[pid] = patient
        self.results[pid] = outcome.baseline_result
        self.agent_states[pid] = outcome.state
        self.decision_sources[pid] = outcome.decision_source
        self._audit_assessment(outcome)
        return pid

    def add_patient(self, patient: Patient) -> str:
        """Register an intake patient and run primary assessment."""
        pid = self.reserve_next_patient_id()
        latest = max((p.arrival_epoch_min for p in self.patients.values()), default=0)
        stamped = patient.model_copy(
            update={
                "patient_id": pid,
                "arrival_epoch_min": latest + 1,
            }
        )
        self._register_assessed(stamped)
        self._save_snapshot()
        return pid

    def reset(self, surge_factor: int, actor: str = "system") -> None:
        # Surge toggles restore an in-memory snapshot; never re-call Gemini here.
        self.load(surge_factor, allow_gemini_warm=False)
        self.audit.append(
            patient_id="-",
            action="reset",
            actor=actor,
            actor_role="system",
            reason=f"board reloaded at surge factor x{surge_factor}",
        )

    def primary_priority(self, patient_id: str) -> int:
        state = self.agent_states.get(patient_id)
        if (
            state is not None
            and state.current_recommendation is not None
            and state.current_recommendation.priority is not None
        ):
            return state.current_recommendation.priority
        return self.results[patient_id].adjudicator.acuity

    def effective_acuity(self, patient_id: str) -> int:
        if patient_id in self.overrides:
            return self.overrides[patient_id]
        return self.primary_priority(patient_id)

    def _resolved_clinician_action(self, patient_id: str) -> Optional[str]:
        """Latest clinician HITL action from agent state or audit trail."""

        state = self.agent_states[patient_id]
        if state.clinician_decision is not None:
            return state.clinician_decision.action.value
        for rec in reversed(self.audit.for_patient(patient_id)):
            if rec.clinician_action:
                return rec.clinician_action
            if rec.action.startswith("hitl_"):
                return rec.action[5:]
        return None

    def row(self, patient_id: str) -> BoardRow:
        p = self.patients[patient_id]
        baseline = self.results[patient_id]
        adj, interp = baseline.adjudicator, baseline.interpreter
        state = self.agent_states[patient_id]
        source = self.decision_sources.get(
            patient_id, DecisionSource.deterministic_fallback
        )
        override = self.overrides.get(patient_id)
        primary = self.primary_priority(patient_id)
        clinician_action = self._resolved_clinician_action(patient_id)

        if clinician_action == "accept":
            self.overrides.pop(patient_id, None)
            override = None
        elif (
            clinician_action in ("modify", "override")
            and override is None
            and state.clinician_decision
            and state.clinician_decision.active_priority is not None
        ):
            override = state.clinician_decision.active_priority
            self.overrides[patient_id] = override

        effective = override if override is not None else primary

        clin_dec = state.clinician_decision
        clin_priority = override
        if clin_priority is None and clin_dec and clin_dec.active_priority is not None:
            clin_priority = clin_dec.active_priority
        refresh_evaluation(state, clinician_priority=clin_priority)
        ev = state.evaluation

        rec = state.current_recommendation
        confidence = rec.confidence if rec is not None else interp.confidence
        if rec is not None and rec.confidence_band is not None:
            band = ConfidenceBand(rec.confidence_band.value)
        else:
            band = interp.confidence_band

        drivers = (
            list(rec.key_evidence[:5])
            if rec is not None and rec.key_evidence
            else adj.top_drivers
        )
        placement = (
            rec.care_pathway
            if rec is not None and rec.care_pathway
            else adj.placement
        )

        return BoardRow(
            patient_id=p.patient_id,
            display_name=p.display_name,
            age_years=p.age_years,
            age_band=interp.age_band,
            sex=p.sex.value if hasattr(p.sex, "value") else str(p.sex),
            chief_complaint=p.chief_complaint,
            arrival_epoch_min=p.arrival_epoch_min,
            acuity=effective,
            engine_acuity=adj.acuity,
            overridden=override is not None and clinician_action != "accept",
            override_direction=(
                direction_for(primary, override) if override is not None else None
            ),
            provisional_acuity=adj.provisional_acuity,
            escalated_for_uncertainty=(
                adj.escalated_for_uncertainty
                and clinician_action
                not in ("accept", "modify", "override")
            ),
            confidence=confidence,
            confidence_band=band,
            routed_to_nurse=(
                (adj.routed_to_nurse or state.human_review_required)
                and clinician_action not in ("accept", "modify", "override")
            ),
            red_flag_count=len(interp.red_flags),
            top_drivers=drivers,
            clocks=_clock_labels(baseline),
            placement=placement or adj.placement,
            monitoring_tier=adj.monitoring_tier,
            expected_acuity=p.expected_acuity,
            decision_source=source.value,
            baseline_used=state.baseline_used,
            agent_priority=ev.agent_priority,
            baseline_priority=(
                ev.baseline_priority if ev.baseline_priority is not None else adj.acuity
            ),
            clinician_priority=ev.clinician_priority,
            agent_baseline_agreement=ev.agent_baseline_agreement,
            agent_clinician_agreement=ev.agent_clinician_agreement,
            baseline_clinician_agreement=ev.baseline_clinician_agreement,
            agent_status=state.status.value,
            clinician_action=clinician_action,
        )

    def board(self) -> BoardResponse:
        rows = [self.row(pid) for pid in self.patients]
        rows.sort(key=lambda r: (r.acuity, r.arrival_epoch_min))
        by_acuity = {level: 0 for level in range(1, 6)}
        for r in rows:
            by_acuity[r.acuity] += 1
        summary = BoardSummary(
            total=len(rows),
            by_acuity=by_acuity,
            routed_to_nurse=sum(1 for r in rows if r.routed_to_nurse),
            low_confidence=sum(1 for r in rows if r.confidence_band.value == "low"),
            escalated_for_uncertainty=sum(
                1 for r in rows if r.escalated_for_uncertainty
            ),
            active_clocks=sum(len(r.clocks) for r in rows),
            overrides=len(self.overrides),
            agent_decisions=sum(
                1 for r in rows if r.decision_source == DecisionSource.agent.value
            ),
            fallback_decisions=sum(
                1
                for r in rows
                if r.decision_source == DecisionSource.deterministic_fallback.value
            ),
            baseline_used_count=sum(1 for r in rows if r.baseline_used),
            agent_baseline_disagreements=sum(
                1 for r in rows if r.agent_baseline_agreement is False
            ),
        )
        return BoardResponse(
            surge_factor=self.surge_factor,
            model_version=ENGINE_VERSION,
            summary=summary,
            rows=rows,
        )

    def detail(self, patient_id: str) -> PatientDetail:
        state = self.agent_states[patient_id]
        rec = state.current_recommendation
        return PatientDetail(
            row=self.row(patient_id),
            result=self.results[patient_id],
            audit=self.audit.for_patient(patient_id),
            decision_source=self.decision_sources[patient_id].value,
            baseline_used=state.baseline_used,
            evaluation=state.evaluation.model_dump(),
            agent_reason_summary=rec.reason_summary if rec else None,
            agent=self.agent_panel(patient_id),
        )

    def apply_override(self, req: OverrideRequest) -> PatientDetail:
        """Record a clinician override. Does not erase agent or baseline scores."""

        r = self.results[req.patient_id]
        state = self.agent_states[req.patient_id]
        before = self.effective_acuity(req.patient_id)
        self.overrides[req.patient_id] = req.new_acuity
        refresh_evaluation(state, clinician_priority=req.new_acuity)
        agent_p = (
            state.current_recommendation.priority
            if state.current_recommendation
            else None
        )
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
            assessment_id=state.assessment_id,
            agent_priority=agent_p,
            agent_confidence=(
                state.current_recommendation.confidence
                if state.current_recommendation
                else None
            ),
            baseline_priority=(
                state.baseline_assessment.priority if state.baseline_assessment else None
            ),
            agreement=state.evaluation.agent_baseline_agreement,
            clinician_action="override",
            clinician_reason=req.reason,
            final_priority=req.new_acuity,
        )
        self._save_snapshot()
        return self.detail(req.patient_id)

    def apply_hitl(self, req: HitlRequest) -> PatientDetail:
        """Apply ACCEPT / MODIFY / OVERRIDE / REQUEST MORE INFORMATION / ESCALATE."""

        if req.patient_id not in self.agent_states:
            raise KeyError(req.patient_id)
        state = self.agent_states[req.patient_id]
        before = self.effective_acuity(req.patient_id)
        try:
            decision = apply_clinician_decision(
                state,
                ClinicianDecisionInput(
                    action=req.action,
                    actor=req.actor,
                    actor_role=req.actor_role,
                    reason=req.reason,
                    active_priority=req.active_priority,
                    info_provided=list(req.info_provided),
                ),
            )
        except HitlError as exc:
            raise ValueError(str(exc)) from exc

        if decision.action.value in ("modify", "override"):
            if decision.active_priority is not None:
                self.overrides[req.patient_id] = decision.active_priority
        elif decision.action.value == "accept":
            self.overrides.pop(req.patient_id, None)
        elif (
            decision.action.value == "escalate"
            and decision.active_priority is not None
        ):
            self.overrides[req.patient_id] = decision.active_priority

        clin_priority = self.overrides.get(req.patient_id)
        if clin_priority is None and decision.active_priority is not None:
            clin_priority = decision.active_priority
        refresh_evaluation(state, clinician_priority=clin_priority)
        agent_p = (
            state.current_recommendation.priority
            if state.current_recommendation
            else None
        )
        final = self.overrides.get(req.patient_id, agent_p)
        self.audit.append(
            patient_id=req.patient_id,
            action=f"hitl_{decision.action.value}",
            actor=req.actor,
            actor_role=req.actor_role,
            reason=decision.reason,
            before_acuity=before,
            after_acuity=final,
            assessment_id=state.assessment_id,
            agent_priority=agent_p,
            agent_confidence=(
                state.current_recommendation.confidence
                if state.current_recommendation
                else None
            ),
            baseline_priority=(
                state.baseline_assessment.priority if state.baseline_assessment else None
            ),
            agreement=state.evaluation.agent_baseline_agreement,
            clinician_action=decision.action.value,
            clinician_reason=decision.reason,
            final_priority=final,
            tools_used=[c.tool_name for c in state.tool_history],
            iterations=state.iteration_count,
            agent_model=(
                "gemini"
                if self.decision_sources.get(req.patient_id) == DecisionSource.agent
                else "deterministic_fallback"
            ),
            agent_version=ENGINE_VERSION,
            agent_action=self.decision_sources.get(
                req.patient_id, DecisionSource.deterministic_fallback
            ).value,
        )
        self._save_snapshot()
        return self.detail(req.patient_id)

    def safe_wait_minutes(self, patient_id: str) -> Optional[int]:
        return T.SAFE_WAIT_MINUTES.get(self.effective_acuity(patient_id))


DEPARTMENT = Department()
