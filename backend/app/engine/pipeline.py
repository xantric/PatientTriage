"""Deterministic triage pipeline: BASELINE / FALLBACK / EVALUATION only.

Phase 7: Gemini agent owns the primary recommendation. Callers must not treat
`triage()` as an automatic override of the agent. Use it for:

- baseline reference (`get_baseline_engine_assessment` / evaluation storage)
- explicit deterministic_fallback when the agent cannot complete
- offline evaluation of agreement rates

Do not wire Interpreter → Adjudicator → force agent recommendation.
"""

from __future__ import annotations

from app.engine.adjudicator import adjudicate
from app.engine.interpreter import interpret
from app.models import Patient, TriageResult


def triage(patient: Patient) -> TriageResult:
    """Run Interpreter + Adjudicator for baseline / fallback / evaluation."""

    interp = interpret(patient)
    adj = adjudicate(patient, interp)
    return TriageResult(patient=patient, interpreter=interp, adjudicator=adj)


def triage_cohort(patients: list[Patient]) -> list[TriageResult]:
    return [triage(p) for p in patients]
