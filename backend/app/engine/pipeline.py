"""Compose the agents into a single triage pass for one patient."""

from __future__ import annotations

from app.engine.adjudicator import adjudicate
from app.engine.interpreter import interpret
from app.models import Patient, TriageResult


def triage(patient: Patient) -> TriageResult:
    interp = interpret(patient)
    adj = adjudicate(patient, interp)
    return TriageResult(patient=patient, interpreter=interp, adjudicator=adj)


def triage_cohort(patients: list[Patient]) -> list[TriageResult]:
    return [triage(p) for p in patients]
