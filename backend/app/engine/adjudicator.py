"""Agent 2: Adjudicator.

Assigns an ESI-style acuity (1 = most urgent, 5 = least), runs time-critical
clocks, sets a monitoring tier, and explains itself. The scoring is fully
deterministic so a clinician can audit every call. Two design choices enforce
the brief's safety requirement:

  * danger-zone vitals for the patient's age can only push acuity UP; and
  * genuine uncertainty (low confidence + red flags) escalates rather than
    settles on a comfortable middle score.
"""

from __future__ import annotations

import re

from app.engine import thresholds as T
from app.models import (
    AdjudicatorOutput,
    ConfidenceBand,
    InterpreterOutput,
    MonitoringTier,
    Patient,
    TimeCriticalClock,
)

# --- keyword cues (illustrative; a real system would use a clinical NLP layer) ---

_STROKE_CUES = ["facial droop", "slurred speech", "face droop", "weak arm",
                "weakness one side", "one-sided", "slurred", "droop", "fast positive"]
_STEMI_CUES = ["chest pain", "chest pressure", "crushing chest", "chest tightness",
               "radiating to left arm", "radiating to arm", "diaphoresis"]
_CARDIAC_CONTEXT = ["coronary", "cardiac", "angina", "previous mi", "stent", "cabg",
                    "artery disease"]
# Anginal-equivalent symptoms: how a silent/atypical MI shows up when there is
# no classic chest pain, especially in elderly or diabetic patients.
_ACS_EQUIVALENT = ["short of breath", "shortness of breath", "sweaty", "clammy",
                   "diaphoretic", "unwell", "nausea", "fatigue and"]
_SEPSIS_INFECTION = ["fever", "infection", "uti", "pneumonia", "cellulitis",
                     "sepsis", "urinary tract", "cough"]
_HIGH_RISK_CUES = ["difficulty breathing", "shortness of breath", "short of breath",
                   "severe bleeding", "overdose", "suicidal", "seizure",
                   "anaphylaxis", "wheezing", "lips swelling", "swelling",
                   "confused", "confusion", "not herself", "unwell and"]

# Complaints that typically need zero or one ED resource.
_NO_RESOURCE = ["refill", "prescription", "dressing change", "tetanus", "note",
                "suture removal", "rash", "sore throat"]
_ONE_RESOURCE = ["laceration", "cut", "sprain", "minor", "x-ray", "splinter"]

_TIME_WINDOWS = {"stroke": 270, "stemi": 90, "sepsis": 60}  # minutes


def _text(patient: Patient) -> str:
    return f"{patient.chief_complaint} {' '.join(patient.history)}".lower()


def _matches(text: str, cues: list[str]) -> bool:
    return any(c in text for c in cues)


def _life_threat(patient: Patient, interp: InterpreterOutput) -> list[str]:
    reasons: list[str] = []
    if patient.responsiveness is not None and patient.responsiveness.value in ("P", "U"):
        reasons.append("not alert (AVPU P/U): airway/consciousness threat")
    v = patient.vitals
    if v.spo2 is not None and v.spo2 < T.SPO2_CRITICAL:
        reasons.append(f"SpO2 {v.spo2}% below critical {T.SPO2_CRITICAL}%")
    if interp.shock_index_flag == "critical":
        reasons.append(f"shock index {interp.shock_index} critical")
    band = T.age_band(patient.age_years)
    if v.sbp is not None and v.sbp < T.SBP_HYPOTENSION[band] and interp.shock_index_flag in ("elevated", "critical"):
        reasons.append("hypotension with shock physiology")
    # A low respiratory rate is only an immediate life threat when the patient is
    # also failing: near-apnoeic, not fully alert, or severely hypoxic. An alert,
    # well-saturated patient with a merely low rate is still abnormal, but it is
    # handled below as a danger-zone vital (ESI 2) rather than a resus call.
    if v.resp_rate is not None and v.resp_rate <= T.RR_CRIT_LOW[band]:
        near_apnoeic = v.resp_rate <= T.RR_APNEIC[band]
        not_alert = patient.responsiveness is not None and patient.responsiveness.value != "A"
        severe_hypoxia = v.spo2 is not None and v.spo2 < T.SPO2_CRITICAL
        if near_apnoeic or not_alert or severe_hypoxia:
            reasons.append("critically low respiratory rate with failing airway or oxygenation")
    return reasons


def _high_risk(patient: Patient, interp: InterpreterOutput, text: str) -> list[str]:
    reasons: list[str] = []
    if _matches(text, _HIGH_RISK_CUES):
        reasons.append("high-risk complaint")
    if patient.pain_score is not None and patient.pain_score >= 7:
        reasons.append(f"severe pain ({patient.pain_score}/10)")
    danger_vitals = [f for f in interp.vital_flags if f.status == "critical"]
    if danger_vitals:
        reasons.append("critical danger-zone vital for age")
    if interp.shock_index_flag == "elevated":
        reasons.append("elevated shock index")
    if patient.responsiveness is not None and patient.responsiveness.value == "V":
        reasons.append("responds only to voice")
    return reasons


def _estimate_resources(text: str) -> int:
    if _matches(text, _NO_RESOURCE):
        return 0
    if _matches(text, _ONE_RESOURCE):
        return 1
    return 2  # default: assume a work-up (labs/imaging) -> multiple resources


def _time_critical(patient: Patient, text: str) -> list[TimeCriticalClock]:
    clocks: list[TimeCriticalClock] = []
    onset = patient.onset_minutes

    def build(name: str, active: bool, note: str) -> None:
        if not active:
            return
        window = _TIME_WINDOWS[name]
        remaining = None if onset is None else window - onset
        clocks.append(
            TimeCriticalClock(
                name=name,
                window_minutes=window,
                minutes_elapsed=onset,
                minutes_remaining=remaining,
                in_window=(onset is None) or (remaining is not None and remaining > 0),
                note=note,
            )
        )

    if _matches(text, _STROKE_CUES):
        build("stroke", True, "stroke signs; thrombolysis window ~4.5h")
    classic_acs = _matches(text, _STEMI_CUES)
    # Atypical/silent MI: cardiac history plus an anginal-equivalent symptom,
    # even without any chest pain. Diabetics and the elderly present this way.
    # Common abbreviations are matched on word boundaries so "CAD" reads as
    # coronary artery disease without "decade" or "cascade" tripping it.
    cardiac_history = _matches(text, _CARDIAC_CONTEXT) or bool(
        re.search(r"\b(cad|acs|ihd|nstemi|stemi|mi)\b", text)
    )
    diabetic_or_elderly = ("diabet" in text) or (patient.age_years >= 65)
    atypical_acs = cardiac_history and _matches(text, _ACS_EQUIVALENT) and diabetic_or_elderly
    if classic_acs or atypical_acs:
        note = ("possible ACS/STEMI; door-to-balloon target 90 min" if classic_acs
                else "atypical/silent MI risk (cardiac history + anginal equivalent); ACS work-up")
        build("stemi", True, note)
    # Sepsis: infection source plus systemic response (fever + tachy/tachypnea/hypotension).
    infection = _matches(text, _SEPSIS_INFECTION)
    v = patient.vitals
    systemic = (
        (v.temp_c is not None and v.temp_c >= T.FEVER)
        or (v.heart_rate is not None and v.heart_rate > 100)
        or (v.resp_rate is not None and v.resp_rate > 22)
    )
    if infection and systemic:
        build("sepsis", True, "infection with systemic response; 1h sepsis bundle")
    return clocks


def _monitoring_tier(acuity: int) -> MonitoringTier:
    return {
        1: MonitoringTier.continuous,
        2: MonitoringTier.every_15,
        3: MonitoringTier.every_30,
        4: MonitoringTier.every_60,
        5: MonitoringTier.every_120,
    }[acuity]


def _placement(acuity: int) -> str:
    return {
        1: "resuscitation bay now",
        2: "high-acuity area, immediate nurse",
        3: "main ED, timely work-up",
        4: "fast track / minors",
        5: "fast track / waiting room",
    }[acuity]


def adjudicate(patient: Patient, interp: InterpreterOutput) -> AdjudicatorOutput:
    text = _text(patient)
    rationale: list[str] = []
    drivers: list[str] = []

    clocks = _time_critical(patient, text)

    # ESI decision A: immediate life-saving intervention.
    life = _life_threat(patient, interp)
    if life:
        provisional = 1
        rationale.append("Decision A: immediate life-saving intervention required.")
        drivers.extend(life[:3])
    else:
        # Decision B: high-risk / can't-miss situations.
        high = _high_risk(patient, interp, text)
        can_miss_clock = any(c.in_window for c in clocks)
        if high or can_miss_clock:
            provisional = 2
            rationale.append("Decision B: high-risk or time-critical presentation.")
            if can_miss_clock:
                names = ", ".join(c.name for c in clocks if c.in_window)
                drivers.append(f"time-critical: {names}")
            drivers.extend(high[:2])
        else:
            # Decision C: resource-based separation of 3/4/5.
            resources = _estimate_resources(text)
            provisional = 3 if resources >= 2 else (4 if resources == 1 else 5)
            rationale.append(
                f"Decision C: estimated {resources} resource(s) -> provisional ESI {provisional}."
            )
            drivers.append(f"{resources} expected resource(s)")

    # Decision D: danger-zone vitals for age upgrade a 3/4/5 to a 2.
    has_danger_vital = any(f.status == "critical" for f in interp.vital_flags) or \
        interp.shock_index_flag in ("elevated", "critical")
    if provisional >= 3 and has_danger_vital:
        rationale.append("Decision D: danger-zone vitals for age -> upgrade to ESI 2.")
        provisional = 2
        drivers.insert(0, "danger-zone vitals for age")

    # Safety net: escalate under genuine uncertainty rather than guessing.
    acuity = provisional
    escalated = False
    routed = False
    if interp.confidence_band == ConfidenceBand.low and (interp.red_flags or provisional <= 3):
        if provisional > 2:
            acuity = provisional - 1
            escalated = True
            rationale.append(
                "Uncertainty rule: low confidence with red flags -> escalate one level and route to nurse."
            )
        routed = True
    # Any low-confidence read is surfaced to a nurse, never silently auto-cleared.
    if interp.confidence_band == ConfidenceBand.low:
        routed = True

    if not drivers:
        drivers = ["no high-risk features; low resource need"]

    what_if = _what_if_ignored(acuity, clocks)

    conf = interp.confidence
    conf_band = interp.confidence_band

    return AdjudicatorOutput(
        patient_id=patient.patient_id,
        acuity=acuity,
        provisional_acuity=provisional,
        escalated_for_uncertainty=escalated,
        placement=_placement(acuity),
        monitoring_tier=_monitoring_tier(acuity),
        top_drivers=drivers[:3],
        what_if_ignored=what_if,
        time_critical_clocks=clocks,
        confidence=conf,
        confidence_band=conf_band,
        routed_to_nurse=routed,
        rationale=rationale,
    )


def _what_if_ignored(acuity: int, clocks: list[TimeCriticalClock]) -> str:
    if clocks:
        names = ", ".join(c.name for c in clocks)
        return (
            f"A missed {names} case loses an irreversible treatment window; "
            "delay converts a treatable event into permanent harm."
        )
    if acuity <= 2:
        return "Delay risks rapid deterioration before anyone re-checks this patient."
    if acuity == 3:
        return "Delay is uncomfortable but unlikely to be immediately dangerous; still monitor."
    return "Low risk of harm from a reasonable wait."
