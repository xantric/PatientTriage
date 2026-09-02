"""FastAPI service behind the Sentinel triage board.

Endpoints are deliberately thin: all the clinical logic lives in the engine,
and all the state lives in `app.state.DEPARTMENT`. The UI is a single static
page served from `/`, so the whole prototype runs from one process.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__ as ENGINE_VERSION
from app.cache.assessment_cache import assessment_cache
from app.data.generator import build_cohort
from app.engine.watcher import simulate
from app.llm.service import LLM
from app.models import (
    AuditRecord,
    BoardResponse,
    Explanation,
    HitlRequest,
    IntakeRequest,
    IntakeResponse,
    LLMStatus,
    OverrideRequest,
    PatientDetail,
    PatientUpdateRequest,
    PatientUpdateResponse,
    SimReport,
    TelemetrySummary,
)
from app.state import DEPARTMENT, PendingIntake

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="Sentinel triage assistant",
    version=ENGINE_VERSION,
    description=(
        "Agent-primary emergency triage decision support. Gemini forms the "
        "recommendation with tools; the deterministic engine is baseline, "
        "fallback, and evaluation only. Clinician HITL is final authority. "
        "Prototype only, not clinical advice."
    ),
)

api = APIRouter(prefix="/api")


@api.get("/health")
def health() -> dict:
    expected = {
        1: len(build_cohort(surge_factor=1)),
        3: len(build_cohort(surge_factor=3)),
    }
    by_surge = assessment_cache.count_by_surge()
    return {
        "status": "ok",
        "model_version": ENGINE_VERSION,
        "patients": len(DEPARTMENT.patients),
        "assessment_cache": {
            "enabled": assessment_cache.enabled(),
            "entries": assessment_cache.count(),
            "by_surge": {
                str(sf): {"cached": by_surge.get(sf, 0), "expected": expected.get(sf, 0)}
                for sf in (1, 3)
            },
        },
    }


@api.get("/board", response_model=BoardResponse)
def board() -> BoardResponse:
    return DEPARTMENT.board()


@api.post("/board/reset", response_model=BoardResponse)
def reset_board(
    surge_factor: int = Query(1, ge=1, le=5, description="1 = normal load, 3 = surge"),
    actor: str = Query("demo operator"),
) -> BoardResponse:
    DEPARTMENT.reset(surge_factor=surge_factor, actor=actor)
    return DEPARTMENT.board()


@api.get("/patients/{patient_id}", response_model=PatientDetail)
def patient_detail(patient_id: str) -> PatientDetail:
    if patient_id not in DEPARTMENT.patients:
        raise HTTPException(status_code=404, detail=f"unknown patient {patient_id}")
    return DEPARTMENT.detail(patient_id)


@api.post("/overrides", response_model=PatientDetail)
def create_override(req: OverrideRequest) -> PatientDetail:
    """Record a clinician override (legacy board control). Prefer /hitl."""

    if req.patient_id not in DEPARTMENT.patients:
        raise HTTPException(status_code=404, detail=f"unknown patient {req.patient_id}")
    return DEPARTMENT.apply_override(req)


@api.post("/hitl", response_model=PatientDetail)
def clinician_hitl(req: HitlRequest) -> PatientDetail:
    """Human-in-the-loop: accept, modify, override, request info, or escalate."""

    if req.patient_id not in DEPARTMENT.patients:
        raise HTTPException(status_code=404, detail=f"unknown patient {req.patient_id}")
    try:
        return DEPARTMENT.apply_hitl(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@api.post("/patients/{patient_id}/update", response_model=PatientUpdateResponse)
def update_patient(patient_id: str, req: PatientUpdateRequest) -> PatientUpdateResponse:
    """Provide requested information and get a new Gemini prediction.

    Two-step flow:
      1. confirm=false (default): runs the new info through Gemini, returns
         the prediction without changing the board.
      2. confirm=true: applies the reassessment to the live board.
    """
    if patient_id not in DEPARTMENT.patients:
        raise HTTPException(status_code=404, detail=f"unknown patient {patient_id}")

    p = DEPARTMENT.patients[patient_id]
    agent_state = DEPARTMENT.agent_states.get(patient_id)

    # Build a combined text from ALL existing patient data + new info.
    # The new info is framed as the LATEST clinical update so the LLM
    # gives it proper weight over initial presentation values.
    parts = []
    parts.append("=== PATIENT RECORD ===")
    if p.display_name:
        parts.append(f"Patient Name: {p.display_name}")
    parts.append(f"Age: {p.age_years}")
    parts.append(f"Sex: {p.sex.value if hasattr(p.sex, 'value') else p.sex}")
    if p.arrival_mode:
        mode = p.arrival_mode.value if hasattr(p.arrival_mode, 'value') else p.arrival_mode
        parts.append(f"Arrival mode: {mode}")
    if p.chief_complaint:
        parts.append(f"Chief complaint: {p.chief_complaint}")
    if p.pain_score is not None:
        parts.append(f"Pain score: {p.pain_score}/10")
    if p.responsiveness:
        resp = p.responsiveness.value if hasattr(p.responsiveness, 'value') else p.responsiveness
        parts.append(f"Responsiveness (AVPU): {resp}")
    if p.onset_minutes is not None:
        parts.append(f"Onset: {p.onset_minutes} minutes ago")
    v = p.vitals
    vitals_parts = []
    if v.heart_rate is not None:
        vitals_parts.append(f"HR {v.heart_rate}")
    if v.sbp is not None:
        bp = f"BP {v.sbp}"
        if v.dbp is not None:
            bp += f"/{v.dbp}"
        vitals_parts.append(bp)
    if v.spo2 is not None:
        vitals_parts.append(f"SpO2 {v.spo2}%")
    if v.resp_rate is not None:
        vitals_parts.append(f"RR {v.resp_rate}")
    if v.temp_c is not None:
        vitals_parts.append(f"Temp {v.temp_c}C")
    if vitals_parts:
        parts.append(f"Initial vitals on arrival: {', '.join(vitals_parts)}")
    if p.history:
        parts.append(f"History: {', '.join(p.history)}")
    if p.medications:
        parts.append(f"Medications: {', '.join(p.medications)}")
    if p.allergies:
        parts.append(f"Allergies: {', '.join(p.allergies)}")
    if p.scenario_note:
        parts.append(f"Clinical note: {p.scenario_note}")
    # Include what the system asked for so Gemini knows the context
    if agent_state and agent_state.information_gaps:
        parts.append(f"Previously requested information: {'; '.join(agent_state.information_gaps)}")
    parts.append("")
    parts.append("=== LATEST CLINICAL UPDATE (use this to revise the assessment) ===")
    parts.append(f"Clinician follow-up note: {req.note}")
    parts.append("If the follow-up indicates findings are normal or stable, adjust the triage level downward accordingly. "
                 "If new vitals are provided, use those instead of the initial vitals.")
    combined_text = "\n".join(parts)

    # First parse the combined text the standard way to get structured data
    parsed, outcome, old_explanation, calls, meta = LLM.triage_intake(combined_text, patient_id)
    result = outcome.baseline_result

    # Now use the pure-Gemini free-text reasoner to get the actual ESI
    new_priority, new_explanation, reassess_calls = LLM.reassess(combined_text, patient_id)
    calls.extend(reassess_calls)

    if new_priority is not None and new_explanation is not None:
        result = result.model_copy(deep=True)
        result.adjudicator.acuity = new_priority
        explanation = new_explanation
        meta["live_priority"] = new_priority
    else:
        explanation = old_explanation

    if req.confirm:
        # Apply: update patient history and reassess on the board
        new_p = p.model_copy()
        new_p.history = list(new_p.history) + [req.note]
        DEPARTMENT._register_assessed(new_p)

        # Apply the LLM-predicted priority as a confirmed override so the
        # board ESI actually reflects the new assessment.
        new_priority = meta["live_priority"] or result.adjudicator.acuity
        DEPARTMENT.overrides[patient_id] = new_priority
        DEPARTMENT.audit.append(
            patient_id=patient_id,
            action="update_confirmed",
            actor="clinician",
            actor_role="triage nurse",
            reason=f"Confirmed reassessment after new information: {req.note}",
            before_acuity=DEPARTMENT.results[patient_id].adjudicator.acuity,
            after_acuity=new_priority,
        )

        detail = DEPARTMENT.detail(patient_id)
        return PatientUpdateResponse(
            patient_id=patient_id,
            parsed=parsed,
            result=result,
            explanation=explanation,
            needs_more_information=meta["needs_more_information"],
            information_gaps=meta["information_gaps"],
            live_priority=meta["live_priority"],
            confirmed=True,
            detail=detail,
        )

    # Preview only
    return PatientUpdateResponse(
        patient_id=patient_id,
        parsed=parsed,
        result=result,
        explanation=explanation,
        needs_more_information=meta["needs_more_information"],
        information_gaps=meta["information_gaps"],
        live_priority=meta["live_priority"],
        confirmed=False,
        detail=None,
    )


@api.get("/audit", response_model=list[AuditRecord])
def audit_trail() -> list[AuditRecord]:
    return DEPARTMENT.audit.all()


@api.get("/simulation", response_model=SimReport)
def simulation(
    surge_factor: int = Query(1, ge=1, le=5),
    notable_only: bool = Query(True, description="return only ratchets and backstop alerts"),
) -> SimReport:
    """Run the Watcher over the waiting room and return the run report."""

    report = simulate(surge_factor=surge_factor)
    if notable_only:
        report = report.model_copy(
            update={"events": [e for e in report.events if e.kind in ("ratchet", "backstop")]}
        )
    return report


@api.get("/llm/status", response_model=LLMStatus)
def llm_status() -> LLMStatus:
    return LLM.status()


@api.post("/intake", response_model=IntakeResponse)
def intake(req: IntakeRequest) -> IntakeResponse:
    """Read a free-text note into a patient and run primary agent assessment.

    Intake parsing may use Gemini. The triage recommendation is agent-primary
    with deterministic baseline/fallback/evaluation only.
    """

    import time
    
    # 1. Look for a rigorous cache hit first
    if req.add_to_board and req.preview_patient_id:
        pending = DEPARTMENT.pending_intakes.get(req.preview_patient_id)
        if pending and pending.text == req.text:
            # Inject directly into data layer without hitting LLM service
            added_id = DEPARTMENT.inject_assessed_patient(pending.parsed.patient, pending.outcome)
            del DEPARTMENT.pending_intakes[req.preview_patient_id]
            
            return IntakeResponse(
                parsed=pending.parsed,
                result=pending.outcome.baseline_result,
                explanation=pending.explanation.model_copy(update={"patient_id": added_id}),
                calls=[],  # No new API calls
                added_patient_id=added_id,
                preview_patient_id=req.preview_patient_id,
                needs_more_information=pending.meta.get("needs_more_information", False),
                information_gaps=pending.meta.get("information_gaps", []),
                live_priority=pending.meta.get("live_priority"),
            )

    # 2. Process normally if not in cache or if text was modified
    patient_id = req.preview_patient_id if req.preview_patient_id else DEPARTMENT.reserve_next_patient_id()
    parsed, outcome, explanation, calls, meta = LLM.triage_intake(req.text, patient_id)

    added_id = None
    if req.add_to_board:
        added_id = DEPARTMENT.inject_assessed_patient(parsed.patient, outcome)
        result = outcome.baseline_result
        explanation = explanation.model_copy(update={"patient_id": added_id})
    else:
        result = outcome.baseline_result
        # Cache for subsequent 'Add to board' action
        DEPARTMENT.pending_intakes[patient_id] = PendingIntake(
            text=req.text,
            parsed=parsed,
            outcome=outcome,
            explanation=explanation,
            calls=calls,
            meta=meta,
            expires_at=time.time() + 3600
        )

    return IntakeResponse(
        parsed=parsed,
        result=result,
        explanation=explanation,
        calls=calls,
        added_patient_id=added_id,
        preview_patient_id=patient_id,
        needs_more_information=meta.get("needs_more_information", False),
        information_gaps=meta["information_gaps"],
        decision_source=meta["decision_source"],
        live_priority=meta["live_priority"],
        agent_status=meta["agent_status"],
    )


@api.get("/patients/{patient_id}/explanation", response_model=Explanation)
def patient_explanation(patient_id: str) -> Explanation:
    if patient_id not in DEPARTMENT.results:
        raise HTTPException(status_code=404, detail=f"unknown patient {patient_id}")
    explanation, _ = LLM.explain(DEPARTMENT.results[patient_id])
    return explanation


@api.get("/telemetry", response_model=TelemetrySummary)
def telemetry() -> TelemetrySummary:
    return LLM.telemetry()


app.include_router(api)

if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))
