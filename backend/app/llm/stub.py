"""Deterministic, offline fallback for the LLM layer.

`rule_parse` pulls structured fields out of a free-text note with regexes, and
`template_explanation` writes a plain sentence from a decision the engine made.
Neither needs a network or a key, so this is both the no-key demo path and the
safety net when a Gemini call fails or returns something we will not trust.
"""

from __future__ import annotations

import re

from app.models import IntakeExtraction, TriageResult


def _search_float(pattern: str, text: str) -> float | None:
    m = re.search(pattern, text, re.IGNORECASE)
    return float(m.group(1)) if m else None


def _normalise_temp(value: float | None) -> float | None:
    if value is None:
        return None
    # Anything clearly above a survivable Celsius reading is Fahrenheit.
    if value >= 45:
        return round((value - 32) * 5 / 9, 1)
    return round(value, 1)


def _onset_minutes(text: str) -> int | None:
    m = re.search(r"(\d+)\s*(min|minute|hour|hr|day)s?\b", text, re.IGNORECASE)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        if unit.startswith("min"):
            return n
        if unit in ("hour", "hr"):
            return n * 60
        if unit == "day":
            return n * 1440
    if re.search(r"since last night|overnight", text, re.IGNORECASE):
        return 600
    if re.search(r"this morning", text, re.IGNORECASE):
        return 240
    return None


def _list_after(labels: list[str], text: str) -> list[str]:
    for label in labels:
        m = re.search(label + r"\s*[:\-]?\s*([^.;\n]+)", text, re.IGNORECASE)
        if m:
            chunk = m.group(1)
            parts = re.split(r",|\band\b|/", chunk)
            return [p.strip() for p in parts if p.strip()][:6]
    return []


def rule_parse(text: str) -> IntakeExtraction:
    t = text.strip()

    # Age and sex, including the compact "78F" form.
    age = None
    compact = re.search(r"\b(\d{1,3})\s*([MmFf])\b", t)
    if compact:
        age = float(compact.group(1))
    if age is None:
        m = re.search(r"\b(\d{1,3})\s*(?:yo|y/o|y\.o\.|yrs?|years?)\b", t, re.IGNORECASE)
        if m:
            age = float(m.group(1))
    if age is None:
        m = re.search(r"\b(\d{1,2})\s*(?:mo|months?)\b", t, re.IGNORECASE)
        if m:
            age = round(int(m.group(1)) / 12, 2)
    if age is None:
        m = re.search(r"\bage\s*(\d{1,3})", t, re.IGNORECASE)
        if m:
            age = float(m.group(1))

    sex = None
    if compact:
        sex = compact.group(2).upper()
    elif re.search(r"\b(female|woman|girl)\b", t, re.IGNORECASE):
        sex = "F"
    elif re.search(r"\b(male|man|boy)\b", t, re.IGNORECASE):
        sex = "M"

    arrival = None
    if re.search(r"ambulance|\bems\b|paramedic|blue light", t, re.IGNORECASE):
        arrival = "ambulance"
    elif re.search(r"wheelchair", t, re.IGNORECASE):
        arrival = "wheelchair"
    elif re.search(r"carried|brought in unconscious", t, re.IGNORECASE):
        arrival = "carried"
    elif re.search(r"walk[- ]?in|walked in|self[- ]presented", t, re.IGNORECASE):
        arrival = "walk_in"

    responsiveness = None
    if re.search(r"unrespons|not respond|gcs\s*[3-8]\b", t, re.IGNORECASE):
        responsiveness = "unresponsive"
    elif re.search(r"responds? to pain|only to pain", t, re.IGNORECASE):
        responsiveness = "pain"
    elif re.search(r"responds? to voice|drowsy|lethargic", t, re.IGNORECASE):
        responsiveness = "voice"
    elif re.search(r"\balert\b|awake and", t, re.IGNORECASE):
        responsiveness = "alert"

    hr = _search_float(r"(?:hr|heart rate|pulse)\D{0,4}(\d{2,3})", t)
    rr = _search_float(r"(?:rr|resp(?:iratory)? rate|resps?|breathing)\D{0,4}(\d{1,2})", t)
    spo2 = _search_float(r"(?:spo2|sats?|o2 sat|oxygen sat|sat)\D{0,4}(\d{2,3})", t)
    temp = _normalise_temp(
        _search_float(r"(?:temp|temperature|t)\D{0,4}(\d{2,3}(?:\.\d)?)", t)
    )
    pain = _search_float(r"(\d{1,2})\s*/\s*10", t)

    sbp = dbp = None
    bp = re.search(r"(?:bp|blood pressure)\D{0,4}(\d{2,3})\s*/\s*(\d{2,3})", t, re.IGNORECASE)
    if not bp:
        bp = re.search(r"\b(\d{2,3})\s*/\s*(\d{2,3})\b", t)
    if bp:
        sbp, dbp = float(bp.group(1)), float(bp.group(2))

    return IntakeExtraction(
        age_years=age,
        sex=sex,
        arrival_mode=arrival,
        chief_complaint=t,
        pain_score=int(pain) if pain is not None and 0 <= pain <= 10 else None,
        responsiveness=responsiveness,
        heart_rate=hr,
        resp_rate=rr,
        sbp=sbp,
        dbp=dbp,
        spo2=spo2,
        temp_c=temp,
        onset_minutes=_onset_minutes(t),
        history=_list_after([r"h/?x of", r"history of", r"pmh"], t),
        medications=_list_after([r"meds?", r"taking", r"on"], t),
        allergies=_list_after([r"allerg(?:y|ies) to", r"allergic to"], t),
    )


def template_explanation(result: TriageResult) -> str:
    """A plain sentence built only from what the engine decided."""

    adj = result.adjudicator
    name = result.patient.display_name or "This patient"
    drivers = "; ".join(adj.top_drivers) if adj.top_drivers else "no high-risk features"
    clock = ""
    for c in adj.time_critical_clocks:
        if c.minutes_remaining is not None and c.minutes_remaining > 0:
            clock = f" A {c.name} clock is running with about {c.minutes_remaining} minutes left."
            break
    confidence = ""
    if adj.confidence_band.value == "low":
        confidence = " Confidence is low, so a nurse should confirm before anyone waits."
    return (
        f"{name} is triaged ESI {adj.acuity}: {adj.placement}. "
        f"The main reasons are {drivers}.{clock} {adj.what_if_ignored}{confidence}"
    ).strip()
