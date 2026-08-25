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
from app.engine.watcher import simulate
from app.llm.service import LLM
from app.models import (
    AuditRecord,
    BoardResponse,
    Explanation,
    IntakeRequest,
    IntakeResponse,
    LLMStatus,
    OverrideRequest,
    PatientDetail,
    SimReport,
    TelemetrySummary,
)
from app.state import DEPARTMENT

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(
    title="Sentinel triage assistant",
    version=ENGINE_VERSION,
    description=(
        "Decision support for emergency department triage. Deterministic "
        "scoring, confidence on every call, and a full override audit trail. "
        "Prototype only, not clinical advice."
    ),
)

api = APIRouter(prefix="/api")


@api.get("/health")
def health() -> dict:
    return {"status": "ok", "model_version": ENGINE_VERSION, "patients": len(DEPARTMENT.patients)}


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
    """Record a clinician override.

    The clinician has final say, so an override may raise or lower acuity. What
    it may not do is happen silently: the reason is required by the schema and
    every change is written to the audit trail.
    """

    if req.patient_id not in DEPARTMENT.patients:
        raise HTTPException(status_code=404, detail=f"unknown patient {req.patient_id}")
    return DEPARTMENT.apply_override(req)


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
    """Read a free-text note into a patient, score it, and explain the result.

    The model only fills intake fields and writes the prose. The acuity comes
    from the same deterministic engine every other patient runs through.
    """

    preview_id = "INTAKE-PREVIEW"
    parsed, result, explanation, calls = LLM.triage_intake(req.text, preview_id)

    added_id = None
    if req.add_to_board:
        added_id = DEPARTMENT.add_patient(parsed.patient)
        result = DEPARTMENT.results[added_id]
        parsed = parsed.model_copy(update={"patient": DEPARTMENT.patients[added_id]})
        explanation = explanation.model_copy(update={"patient_id": added_id})

    return IntakeResponse(
        parsed=parsed,
        result=result,
        explanation=explanation,
        calls=calls,
        added_patient_id=added_id,
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
