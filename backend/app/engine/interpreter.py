"""Agent 1: Interpreter.

Turns sparse, messy intake into a clean, structured snapshot: age-aware
vital flags, derived indices (shock index, time since onset), a red-flag
list, an explicit list of what is missing, and a data-completeness driven
confidence score. It makes no acuity decision; that is the Adjudicator's job.
"""

from __future__ import annotations

from app.engine import thresholds as T
from app.models import (
    ConfidenceBand,
    InterpreterOutput,
    Patient,
    VitalFlag,
)

# Fields that matter most for a safe triage decision. Missing items here
# cost more confidence than missing peripheral data.
KEY_VITALS = ["heart_rate", "resp_rate", "sbp", "spo2", "temp_c"]
KEY_CONTEXT = ["chief_complaint", "responsiveness"]


def _flag_heart_rate(hr: float, band: T.AgeBand) -> VitalFlag:
    if hr >= T.HR_CRIT_HIGH[band]:
        return VitalFlag(vital="heart_rate", value=hr, status="critical", direction="high",
                         note=f"tachycardia for {band.value}")
    if hr <= T.HR_CRIT_LOW[band]:
        return VitalFlag(vital="heart_rate", value=hr, status="critical", direction="low",
                         note=f"bradycardia for {band.value}")
    if not T.HR_NORMAL[band].contains(hr):
        direction = "high" if hr > T.HR_NORMAL[band].high else "low"
        return VitalFlag(vital="heart_rate", value=hr, status="abnormal", direction=direction,
                         note=f"outside normal range for {band.value}")
    return VitalFlag(vital="heart_rate", value=hr, status="normal", direction="none")


def _flag_resp_rate(rr: float, band: T.AgeBand) -> VitalFlag:
    if rr >= T.RR_CRIT_HIGH[band]:
        return VitalFlag(vital="resp_rate", value=rr, status="critical", direction="high",
                         note=f"tachypnea for {band.value}")
    if rr <= T.RR_CRIT_LOW[band]:
        return VitalFlag(vital="resp_rate", value=rr, status="critical", direction="low",
                         note=f"bradypnea for {band.value}")
    if not T.RR_NORMAL[band].contains(rr):
        direction = "high" if rr > T.RR_NORMAL[band].high else "low"
        return VitalFlag(vital="resp_rate", value=rr, status="abnormal", direction=direction)
    return VitalFlag(vital="resp_rate", value=rr, status="normal", direction="none")


def _flag_sbp(sbp: float, band: T.AgeBand) -> VitalFlag:
    if sbp < T.SBP_HYPOTENSION[band]:
        return VitalFlag(vital="sbp", value=sbp, status="critical", direction="low",
                         note=f"hypotension for {band.value}")
    return VitalFlag(vital="sbp", value=sbp, status="normal", direction="none")


def _flag_spo2(spo2: float) -> VitalFlag:
    if spo2 < T.SPO2_CRITICAL:
        return VitalFlag(vital="spo2", value=spo2, status="critical", direction="low",
                         note="severe hypoxia")
    if spo2 < T.SPO2_ABNORMAL:
        return VitalFlag(vital="spo2", value=spo2, status="abnormal", direction="low",
                         note="hypoxia")
    return VitalFlag(vital="spo2", value=spo2, status="normal", direction="none")


def _flag_temp(temp: float) -> VitalFlag:
    if temp >= T.HIGH_FEVER:
        return VitalFlag(vital="temp_c", value=temp, status="abnormal", direction="high",
                         note="high fever")
    if temp >= T.FEVER:
        return VitalFlag(vital="temp_c", value=temp, status="abnormal", direction="high",
                         note="fever")
    if temp < T.HYPOTHERMIA:
        return VitalFlag(vital="temp_c", value=temp, status="critical", direction="low",
                         note="hypothermia")
    return VitalFlag(vital="temp_c", value=temp, status="normal", direction="none")


def interpret(patient: Patient) -> InterpreterOutput:
    band = T.age_band(patient.age_years)
    v = patient.vitals

    flags: list[VitalFlag] = []
    if v.heart_rate is not None:
        flags.append(_flag_heart_rate(v.heart_rate, band))
    if v.resp_rate is not None:
        flags.append(_flag_resp_rate(v.resp_rate, band))
    if v.sbp is not None:
        flags.append(_flag_sbp(v.sbp, band))
    if v.spo2 is not None:
        flags.append(_flag_spo2(v.spo2))
    if v.temp_c is not None:
        flags.append(_flag_temp(v.temp_c))

    # Derived index: shock index = HR / SBP.
    shock_index = None
    si_flag = "unknown"
    if v.heart_rate is not None and v.sbp is not None and v.sbp > 0:
        shock_index = round(v.heart_rate / v.sbp, 2)
        if shock_index >= T.shock_index_critical(band):
            si_flag = "critical"
        elif shock_index >= T.shock_index_cutoff(band):
            si_flag = "elevated"
        else:
            si_flag = "normal"

    findings: list[str] = [f"age band: {band.value}"]
    if patient.responsiveness is not None and patient.responsiveness.value != "A":
        findings.append(f"reduced responsiveness (AVPU={patient.responsiveness.value})")
    if shock_index is not None:
        findings.append(f"shock index {shock_index} ({si_flag})")
    if patient.onset_minutes is not None:
        findings.append(f"onset {patient.onset_minutes} min ago")

    red_flags: list[str] = []
    for f in flags:
        if f.status in ("critical", "abnormal") and f.direction != "none":
            red_flags.append(f"{f.vital} {f.status} ({f.direction}){': ' + f.note if f.note else ''}")
    if si_flag in ("elevated", "critical"):
        red_flags.append(f"shock index {si_flag}")
    if patient.responsiveness is not None and patient.responsiveness.value in ("P", "U"):
        red_flags.append("not alert (responds to pain or unresponsive)")

    # Data gaps and completeness.
    present_vitals = set(v.present_fields())
    missing_vitals = [k for k in KEY_VITALS if k not in present_vitals]
    data_gaps = [f"missing {k}" for k in missing_vitals]
    if patient.onset_minutes is None:
        data_gaps.append("unknown time since onset")
    if not patient.has_prior_record:
        data_gaps.append("no prior record on file")

    key_present = len(present_vitals & set(KEY_VITALS))
    context_present = sum(
        1 for c in KEY_CONTEXT
        if (getattr(patient, c) not in (None, ""))
    )
    completeness = round(
        (key_present / len(KEY_VITALS)) * 0.75
        + (context_present / len(KEY_CONTEXT)) * 0.25,
        2,
    )

    # Confidence starts from completeness and is penalised for ambiguity:
    # a first-time patient with a vague complaint and missing vitals should
    # not read as confident.
    confidence = completeness
    if not patient.has_prior_record:
        confidence -= 0.1
    if patient.onset_minutes is None:
        confidence -= 0.05
    confidence = max(0.0, min(1.0, round(confidence, 2)))

    band_conf = (
        ConfidenceBand.high if confidence >= 0.75
        else ConfidenceBand.medium if confidence >= 0.5
        else ConfidenceBand.low
    )

    return InterpreterOutput(
        patient_id=patient.patient_id,
        age_band=band.value,
        findings=findings,
        vital_flags=flags,
        shock_index=shock_index,
        shock_index_flag=si_flag,
        minutes_since_onset=patient.onset_minutes,
        red_flags=red_flags,
        data_gaps=data_gaps,
        completeness=completeness,
        confidence=confidence,
        confidence_band=band_conf,
    )
