"""Bridge domain patient + vitals into the legacy deterministic engine models."""

from __future__ import annotations

from app.domain.enums import ArrivalMode, Responsiveness, Sex
from app.domain.models import Patient as DomainPatient
from app.domain.models import PatientContext
from app.domain.models import Vitals as DomainVitals
from app.models import (
    ArrivalMode as LegacyArrival,
    Patient as LegacyPatient,
    Responsiveness as LegacyResponsiveness,
    Sex as LegacySex,
    Vitals as LegacyVitals,
)


def _sex(value: Sex) -> LegacySex:
    return LegacySex(value.value)


def _arrival(value: ArrivalMode) -> LegacyArrival:
    return LegacyArrival(value.value)


def _responsiveness(value: Responsiveness | None) -> LegacyResponsiveness | None:
    if value is None:
        return None
    return LegacyResponsiveness(value.value)


def to_legacy_vitals(vitals: DomainVitals | None) -> LegacyVitals:
    if vitals is None:
        return LegacyVitals()
    return LegacyVitals(
        heart_rate=vitals.heart_rate,
        resp_rate=vitals.resp_rate,
        sbp=vitals.sbp,
        dbp=vitals.dbp,
        spo2=vitals.spo2,
        temp_c=vitals.temp_c,
    )


def from_legacy_vitals(vitals: LegacyVitals | None) -> DomainVitals:
    if vitals is None:
        return DomainVitals()
    return DomainVitals(
        heart_rate=vitals.heart_rate,
        resp_rate=vitals.resp_rate,
        sbp=vitals.sbp,
        dbp=vitals.dbp,
        spo2=vitals.spo2,
        temp_c=vitals.temp_c,
    )


def to_legacy_patient(
    patient: DomainPatient,
    vitals: DomainVitals | None = None,
) -> LegacyPatient:
    """Build a legacy Patient for Interpreter/Adjudicator. Ignores deteriorates."""

    ctx = patient.context
    return LegacyPatient(
        patient_id=patient.patient_id,
        display_name=ctx.display_name,
        age_years=ctx.age_years,
        sex=_sex(ctx.sex),
        arrival_mode=_arrival(ctx.arrival_mode),
        chief_complaint=ctx.chief_complaint,
        pain_score=ctx.pain_score,
        responsiveness=_responsiveness(ctx.responsiveness),
        vitals=to_legacy_vitals(vitals),
        onset_minutes=ctx.onset_minutes,
        history=list(ctx.history),
        medications=list(ctx.medications),
        allergies=list(ctx.allergies),
        has_prior_record=ctx.has_prior_record,
        arrival_epoch_min=patient.arrival_epoch_min,
        expected_acuity=patient.expected_priority,
        scenario_note=patient.scenario_note,
        deteriorates=None,  # engine must never see sim ground truth
    )


def from_legacy_patient(patient: LegacyPatient) -> DomainPatient:
    """Convert a cohort / engine patient into the agent-first domain model.

    Preserves deteriorates for simulation only. Triage tools must not read it.
    """

    sex = Sex(patient.sex.value)
    arrival = ArrivalMode(patient.arrival_mode.value)
    resp = (
        Responsiveness(patient.responsiveness.value)
        if patient.responsiveness is not None
        else None
    )
    return DomainPatient(
        patient_id=patient.patient_id,
        context=PatientContext(
            display_name=patient.display_name,
            age_years=patient.age_years,
            sex=sex,
            arrival_mode=arrival,
            chief_complaint=patient.chief_complaint,
            pain_score=patient.pain_score,
            responsiveness=resp,
            onset_minutes=patient.onset_minutes,
            history=list(patient.history),
            medications=list(patient.medications),
            allergies=list(patient.allergies),
            has_prior_record=patient.has_prior_record,
        ),
        arrival_epoch_min=patient.arrival_epoch_min,
        expected_priority=patient.expected_acuity,
        scenario_note=patient.scenario_note,
        deteriorates=patient.deteriorates,
    )
