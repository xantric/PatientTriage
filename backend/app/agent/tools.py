"""Safe agent tools: evidence and calculations only.

No tool decides the patient's final ESI. The agent owns the recommendation.
get_baseline_engine_assessment exposes Interpreter+Adjudicator as a labeled
deterministic baseline (SOURCE=deterministic_baseline). It never writes into
TriageAgentState; the caller may inspect, agree, disagree, or ignore it.
"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from app.agent.bridge import to_legacy_patient
from app.agent.context import ToolContext
from app.agent.registry import ToolRegistry, ToolResult
from app.domain.models import (
    AgentRecommendation,
    Patient,
    TriageAgentState,
    VitalObservation,
    Vitals,
    WatcherState,
)
from app.engine import thresholds as T
from app.engine.interpreter import KEY_CONTEXT, KEY_VITALS
from app.engine.pipeline import triage as run_baseline_triage

# Explicit source tag for the old engine. Never present as the agent answer.
BASELINE_SOURCE = "deterministic_baseline"

# Forbidden: tools that decide ESI for the agent.
FORBIDDEN_TOOL_NAMES = frozenset(
    {
        "get_final_triage",
        "decide_esi",
        "set_priority",
        "get_esi",
        "final_acuity",
    }
)


# ---------------------------------------------------------------------------
# Argument schemas
# ---------------------------------------------------------------------------


class PatientIdArgs(BaseModel):
    patient_id: str = Field(..., min_length=1)


class VitalHistoryArgs(BaseModel):
    patient_id: str = Field(..., min_length=1)
    limit: int = Field(20, ge=1, le=100)


class ShockIndexArgs(BaseModel):
    patient_id: str = Field(..., min_length=1)
    # Optional overrides; if omitted, use latest vitals from agent state.
    heart_rate: Optional[float] = Field(None, gt=0)
    sbp: Optional[float] = Field(None, gt=0)


class VitalTrendsArgs(BaseModel):
    patient_id: str = Field(..., min_length=1)
    vitals: Optional[list[str]] = Field(
        None,
        description="Vital field names to trend; default all present fields.",
    )


class TimeSinceOnsetArgs(BaseModel):
    patient_id: str = Field(..., min_length=1)
    # Optional clock override (minutes from session origin).
    now_min: Optional[int] = Field(None, ge=0)


class CompletenessArgs(BaseModel):
    patient_id: str = Field(..., min_length=1)


class EmptyArgs(BaseModel):
    """Placeholder; prefer PatientIdArgs for patient-scoped tools."""


# ---------------------------------------------------------------------------
# Helpers (read-only; no agent state mutation)
# ---------------------------------------------------------------------------


def _require_patient(ctx: ToolContext, patient_id: str) -> Patient:
    return ctx.get_patient(patient_id)


def _require_state(ctx: ToolContext, patient_id: str) -> TriageAgentState:
    return ctx.get_agent_state(patient_id)


def _latest_vitals(state: TriageAgentState) -> Vitals:
    if state.latest_vitals is not None:
        return state.latest_vitals
    if state.vital_history:
        return state.vital_history[-1].vitals
    return Vitals()


def _format_series(values: list[float]) -> str:
    return " → ".join(str(int(v) if float(v).is_integer() else v) for v in values)


def _vital_label(name: str) -> str:
    labels = {
        "heart_rate": "HR",
        "resp_rate": "RR",
        "sbp": "SBP",
        "dbp": "DBP",
        "spo2": "SpO2",
        "temp_c": "Temp",
    }
    return labels.get(name, name)


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------


def get_patient(ctx: ToolContext, args: PatientIdArgs) -> ToolResult:
    patient = _require_patient(ctx, args.patient_id)
    c = patient.context
    evidence = {
        "patient_id": patient.patient_id,
        "display_name": c.display_name,
        "age_years": c.age_years,
        "sex": c.sex.value,
        "arrival_mode": c.arrival_mode.value,
        "chief_complaint": c.chief_complaint,
        "pain_score": c.pain_score,
        "responsiveness": c.responsiveness.value if c.responsiveness else None,
        "onset_minutes": c.onset_minutes,
        "has_prior_record": c.has_prior_record,
        "arrival_epoch_min": patient.arrival_epoch_min,
    }
    name = c.display_name or patient.patient_id
    summary = (
        f"{name}, age {c.age_years}, {c.arrival_mode.value}; "
        f"complaint: {c.chief_complaint or 'none recorded'}."
    )
    return ToolResult(
        ok=True, tool_name="get_patient", evidence=evidence, summary=summary
    )


def get_latest_vitals(ctx: ToolContext, args: PatientIdArgs) -> ToolResult:
    state = _require_state(ctx, args.patient_id)
    vitals = _latest_vitals(state)
    dump = vitals.model_dump()
    present = vitals.present_fields()
    evidence = {
        "patient_id": args.patient_id,
        "vitals": dump,
        "present_fields": present,
        "recorded_at_min": (
            state.vital_history[-1].recorded_at_min if state.vital_history else None
        ),
    }
    if not present:
        summary = "No vitals recorded yet."
    else:
        parts = [f"{_vital_label(k)}={dump[k]}" for k in present]
        summary = "Latest vitals: " + ", ".join(parts) + "."
    return ToolResult(
        ok=True, tool_name="get_latest_vitals", evidence=evidence, summary=summary
    )


def get_vital_history(ctx: ToolContext, args: VitalHistoryArgs) -> ToolResult:
    state = _require_state(ctx, args.patient_id)
    history = state.vital_history[-args.limit :]
    rows: list[dict[str, Any]] = []
    for obs in history:
        rows.append(
            {
                "observation_id": obs.observation_id,
                "recorded_at_min": obs.recorded_at_min,
                "source": obs.source.value,
                "vitals": obs.vitals.model_dump(),
                "note": obs.note,
            }
        )
    evidence = {
        "patient_id": args.patient_id,
        "count": len(rows),
        "observations": rows,
    }
    summary = f"{len(rows)} vital observation(s) on record."
    return ToolResult(
        ok=True, tool_name="get_vital_history", evidence=evidence, summary=summary
    )


def get_patient_history(ctx: ToolContext, args: PatientIdArgs) -> ToolResult:
    patient = _require_patient(ctx, args.patient_id)
    c = patient.context
    evidence = {
        "patient_id": args.patient_id,
        "history": list(c.history),
        "medications": list(c.medications),
        "allergies": list(c.allergies),
        "has_prior_record": c.has_prior_record,
    }
    if c.history:
        summary = ", ".join(c.history)
    else:
        summary = "No past medical history on file."
    return ToolResult(
        ok=True, tool_name="get_patient_history", evidence=evidence, summary=summary
    )


def calculate_shock_index(ctx: ToolContext, args: ShockIndexArgs) -> ToolResult:
    patient = _require_patient(ctx, args.patient_id)
    state = _require_state(ctx, args.patient_id)
    vitals = _latest_vitals(state)
    hr = args.heart_rate if args.heart_rate is not None else vitals.heart_rate
    sbp = args.sbp if args.sbp is not None else vitals.sbp

    band = T.age_band(patient.context.age_years)
    if hr is None or sbp is None or sbp <= 0:
        return ToolResult(
            ok=True,
            tool_name="calculate_shock_index",
            evidence={
                "patient_id": args.patient_id,
                "shock_index": None,
                "shock_index_flag": "unknown",
                "heart_rate": hr,
                "sbp": sbp,
                "age_band": band.value,
                "reason": "heart_rate and positive sbp required",
            },
            summary="Shock index unknown: need HR and SBP.",
        )

    si = round(float(hr) / float(sbp), 2)
    if si >= T.shock_index_critical(band):
        flag = "critical"
    elif si >= T.shock_index_cutoff(band):
        flag = "elevated"
    else:
        flag = "normal"

    evidence = {
        "patient_id": args.patient_id,
        "shock_index": si,
        "shock_index_flag": flag,
        "heart_rate": hr,
        "sbp": sbp,
        "age_band": band.value,
        "cutoff_elevated": T.shock_index_cutoff(band),
        "cutoff_critical": T.shock_index_critical(band),
    }
    summary = f"Shock index {si} ({flag}) for age band {band.value}."
    return ToolResult(
        ok=True,
        tool_name="calculate_shock_index",
        evidence=evidence,
        summary=summary,
    )


def calculate_vital_trends(ctx: ToolContext, args: VitalTrendsArgs) -> ToolResult:
    state = _require_state(ctx, args.patient_id)
    history: list[VitalObservation] = list(state.vital_history)
    if not history and state.latest_vitals is not None:
        # Single snapshot: still report as a one-point series.
        history = [
            VitalObservation(recorded_at_min=0, vitals=state.latest_vitals)
        ]

    field_names = args.vitals or [
        "heart_rate",
        "resp_rate",
        "sbp",
        "dbp",
        "spo2",
        "temp_c",
    ]
    trends: dict[str, Any] = {}
    lines: list[str] = []
    for name in field_names:
        series: list[float] = []
        times: list[int] = []
        for obs in history:
            val = getattr(obs.vitals, name, None)
            if val is not None:
                series.append(float(val))
                times.append(obs.recorded_at_min)
        if not series:
            continue
        formatted = _format_series(series)
        direction = "flat"
        if len(series) >= 2:
            if series[-1] > series[0]:
                direction = "rising"
            elif series[-1] < series[0]:
                direction = "falling"
        trends[name] = {
            "values": series,
            "times_min": times,
            "series_text": formatted,
            "direction": direction,
        }
        lines.append(f"{_vital_label(name)} {formatted}")

    evidence = {
        "patient_id": args.patient_id,
        "trends": trends,
        "observation_count": len(history),
    }
    summary = "; ".join(lines) if lines else "No trendable vitals."
    return ToolResult(
        ok=True,
        tool_name="calculate_vital_trends",
        evidence=evidence,
        summary=summary,
    )


def calculate_time_since_onset(ctx: ToolContext, args: TimeSinceOnsetArgs) -> ToolResult:
    patient = _require_patient(ctx, args.patient_id)
    onset = patient.context.onset_minutes
    # onset_minutes on intake is already "minutes since onset" at arrival.
    # If now_min is provided relative to arrival, add elapsed wait.
    minutes: Optional[int] = None
    method = "unknown"
    if onset is not None:
        if args.now_min is not None:
            elapsed_wait = max(0, args.now_min - patient.arrival_epoch_min)
            minutes = onset + elapsed_wait
            method = "onset_at_intake_plus_wait"
        else:
            minutes = onset
            method = "onset_at_intake"

    evidence = {
        "patient_id": args.patient_id,
        "minutes_since_onset": minutes,
        "onset_minutes_at_intake": onset,
        "now_min": args.now_min,
        "arrival_epoch_min": patient.arrival_epoch_min,
        "method": method,
    }
    if minutes is None:
        summary = "Time since onset unknown."
    else:
        summary = f"{minutes} minutes since symptom onset ({method})."
    return ToolResult(
        ok=True,
        tool_name="calculate_time_since_onset",
        evidence=evidence,
        summary=summary,
    )


def assess_data_completeness(ctx: ToolContext, args: CompletenessArgs) -> ToolResult:
    patient = _require_patient(ctx, args.patient_id)
    state = _require_state(ctx, args.patient_id)
    vitals = _latest_vitals(state)
    present = set(vitals.present_fields())
    missing_vitals = [k for k in KEY_VITALS if k not in present]
    missing_context: list[str] = []
    if not patient.context.chief_complaint:
        missing_context.append("chief_complaint")
    if patient.context.responsiveness is None:
        missing_context.append("responsiveness")
    if patient.context.onset_minutes is None:
        missing_context.append("onset_minutes")

    key_present = len(present & set(KEY_VITALS))
    context_present = sum(
        1
        for c in KEY_CONTEXT
        if (
            (c == "chief_complaint" and patient.context.chief_complaint)
            or (
                c == "responsiveness"
                and patient.context.responsiveness is not None
            )
        )
    )
    completeness = round(
        (key_present / len(KEY_VITALS)) * 0.75
        + (context_present / len(KEY_CONTEXT)) * 0.25,
        2,
    )

    # Human-readable gaps, e.g. "missing SBP"
    gap_labels = {
        "heart_rate": "HR",
        "resp_rate": "RR",
        "sbp": "SBP",
        "spo2": "SpO2",
        "temp_c": "Temp",
        "chief_complaint": "chief complaint",
        "responsiveness": "responsiveness",
        "onset_minutes": "time since onset",
    }
    missing_readable = [
        f"missing {gap_labels.get(m, m)}" for m in missing_vitals + missing_context
    ]

    evidence = {
        "patient_id": args.patient_id,
        "completeness": completeness,
        "missing_vitals": missing_vitals,
        "missing_context": missing_context,
        "missing_readable": missing_readable,
        "present_vitals": sorted(present),
        "has_prior_record": patient.context.has_prior_record,
    }
    summary = (
        ", ".join(missing_readable)
        if missing_readable
        else f"Key data complete (score {completeness})."
    )
    return ToolResult(
        ok=True,
        tool_name="assess_data_completeness",
        evidence=evidence,
        summary=summary,
    )


def inspect_watcher_state(ctx: ToolContext, args: PatientIdArgs) -> ToolResult:
    state = _require_state(ctx, args.patient_id)
    w: WatcherState = state.watcher_state
    evidence = {
        "patient_id": args.patient_id,
        "queue_status": w.queue_status.value,
        "arrival_min": w.arrival_min,
        "last_contact_min": w.last_contact_min,
        "next_due_min": w.next_due_min,
        "overdue": w.overdue,
        "unsafe_wait_min": w.unsafe_wait_min,
        "last_recheck_min": w.last_recheck_min,
        "ratchet_count": w.ratchet_count,
        "backstop_fired": w.backstop_fired,
        "last_event_summary": w.last_event_summary,
    }
    summary = (
        f"Watcher {w.queue_status.value}; overdue={w.overdue}; "
        f"unsafe_wait={w.unsafe_wait_min} min; ratchets={w.ratchet_count}."
    )
    return ToolResult(
        ok=True,
        tool_name="inspect_watcher_state",
        evidence=evidence,
        summary=summary,
    )


def get_previous_agent_assessment(ctx: ToolContext, args: PatientIdArgs) -> ToolResult:
    """Prior agent recommendation evidence. Not a final ESI decision tool."""

    priors = ctx.prior_assessments(args.patient_id)
    state: TriageAgentState | None = None
    if priors:
        state = priors[-1]
    elif args.patient_id in ctx.agent_states:
        current = ctx.agent_states[args.patient_id]
        if current.previous_recommendation is not None:
            state = current

    if state is None:
        return ToolResult(
            ok=True,
            tool_name="get_previous_agent_assessment",
            evidence={"patient_id": args.patient_id, "available": False},
            summary="No previous agent assessment on file.",
        )

    rec: AgentRecommendation | None = (
        state.previous_recommendation or state.current_recommendation
    )
    if rec is None:
        return ToolResult(
            ok=True,
            tool_name="get_previous_agent_assessment",
            evidence={"patient_id": args.patient_id, "available": False},
            summary="Previous assessment exists but has no recommendation yet.",
        )

    evidence = {
        "patient_id": args.patient_id,
        "available": True,
        "assessment_id": state.assessment_id,
        "status": state.status.value,
        "recommendation": {
            "recommendation_id": rec.recommendation_id,
            "priority": rec.priority,
            "urgency": rec.urgency,
            "care_pathway": rec.care_pathway,
            "monitoring_plan": rec.monitoring_plan,
            "confidence": rec.confidence,
            "reason_summary": rec.reason_summary,
            "key_evidence": list(rec.key_evidence),
            "information_gaps": list(rec.information_gaps),
            "revision": rec.revision,
            "human_review_required": rec.human_review_required,
        },
        "note": (
            "Prior agent recommendation for inspection only. "
            "Not an automatic override of the current session."
        ),
    }
    pri = rec.priority if rec.priority is not None else "unset"
    summary = (
        f"Previous agent recommendation priority {pri}; "
        f"confidence {rec.confidence}."
    )
    return ToolResult(
        ok=True,
        tool_name="get_previous_agent_assessment",
        evidence=evidence,
        summary=summary,
    )


def get_baseline_engine_assessment(ctx: ToolContext, args: PatientIdArgs) -> ToolResult:
    """Run Interpreter+Adjudicator. Labeled deterministic baseline only.

    Does not mutate TriageAgentState. Agent may inspect / agree / disagree / ignore.
    """

    patient = _require_patient(ctx, args.patient_id)
    state = _require_state(ctx, args.patient_id)
    vitals = _latest_vitals(state)
    legacy = to_legacy_patient(patient, vitals)
    result = run_baseline_triage(legacy)
    adj = result.adjudicator
    interp = result.interpreter

    evidence = {
        "source": BASELINE_SOURCE,
        "patient_id": args.patient_id,
        "priority": adj.acuity,
        "provisional_priority": adj.provisional_acuity,
        "placement": adj.placement,
        "monitoring_tier": adj.monitoring_tier.value,
        "confidence": adj.confidence,
        "confidence_band": adj.confidence_band.value,
        "drivers": list(adj.top_drivers),
        "rationale": list(adj.rationale),
        "escalated_for_uncertainty": adj.escalated_for_uncertainty,
        "routed_to_nurse": adj.routed_to_nurse,
        "interpreter": {
            "age_band": interp.age_band,
            "shock_index": interp.shock_index,
            "shock_index_flag": interp.shock_index_flag,
            "completeness": interp.completeness,
            "data_gaps": list(interp.data_gaps),
            "red_flags": list(interp.red_flags),
            "findings": list(interp.findings),
        },
        "disclaimer": (
            "Deterministic baseline for reference, fallback, and evaluation only. "
            "Not the agent's final recommendation."
        ),
    }
    summary = (
        f"Deterministic baseline P{adj.acuity} "
        f"(SOURCE={BASELINE_SOURCE}); placement {adj.placement}."
    )
    return ToolResult(
        ok=True,
        tool_name="get_baseline_engine_assessment",
        evidence=evidence,
        summary=summary,
    )


# ---------------------------------------------------------------------------
# Registry builder
# ---------------------------------------------------------------------------


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()

    specs: list[tuple[str, str, type[BaseModel], Any]] = [
        (
            "get_patient",
            "Return demographics and intake context for a patient (evidence only).",
            PatientIdArgs,
            get_patient,
        ),
        (
            "get_latest_vitals",
            "Return the most recent vital signs snapshot.",
            PatientIdArgs,
            get_latest_vitals,
        ),
        (
            "get_vital_history",
            "Return chronologic vital observations for trend inspection.",
            VitalHistoryArgs,
            get_vital_history,
        ),
        (
            "get_patient_history",
            "Return past medical history, medications, and allergies.",
            PatientIdArgs,
            get_patient_history,
        ),
        (
            "calculate_shock_index",
            "Compute shock index (HR/SBP) and age-banded flag. Numeric evidence only.",
            ShockIndexArgs,
            calculate_shock_index,
        ),
        (
            "calculate_vital_trends",
            "Format vital series (e.g. HR 96 → 110 → 122). Evidence only.",
            VitalTrendsArgs,
            calculate_vital_trends,
        ),
        (
            "calculate_time_since_onset",
            "Compute minutes since symptom onset when known.",
            TimeSinceOnsetArgs,
            calculate_time_since_onset,
        ),
        (
            "assess_data_completeness",
            "List missing key vitals/context (e.g. missing SBP). Evidence only.",
            CompletenessArgs,
            assess_data_completeness,
        ),
        (
            "inspect_watcher_state",
            "Return waiting-room Watcher queue and timer state.",
            PatientIdArgs,
            inspect_watcher_state,
        ),
        (
            "get_previous_agent_assessment",
            "Return a prior agent recommendation if present. Not a final ESI tool.",
            PatientIdArgs,
            get_previous_agent_assessment,
        ),
        (
            "get_baseline_engine_assessment",
            "Run Interpreter+Adjudicator as SOURCE=deterministic_baseline. Reference only.",
            PatientIdArgs,
            get_baseline_engine_assessment,
        ),
    ]

    for name, description, args_model, handler in specs:
        if name in FORBIDDEN_TOOL_NAMES:
            raise RuntimeError(f"refusing to register forbidden tool: {name}")
        registry.register(
            name=name,
            description=description,
            args_model=args_model,
            handler=handler,
        )

    return registry
