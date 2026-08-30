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

    patient_name = None
    m = re.search(
        r"(?:patient\s*name|full\s*name|\bname)\s*[:\-]\s*([A-Za-z][A-Za-z .'\-]{1,60})",
        t,
        re.IGNORECASE,
    )
    if m:
        patient_name = re.sub(r"\s+", " ", m.group(1)).strip(" .,;:")

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
        m = re.search(r"\bage\s*[:\-]?\s*(\d{1,3})", t, re.IGNORECASE)
        if m:
            age = float(m.group(1))

    sex = None
    labeled_sex = re.search(
        r"\b(?:sex|gender)\s*[:\-]\s*(M|F|O|Male|Female|Other|Others)\b",
        t,
        re.IGNORECASE,
    )
    if labeled_sex:
        token = labeled_sex.group(1).upper()
        if token.startswith("M"):
            sex = "M"
        elif token.startswith("F"):
            sex = "F"
        else:
            sex = "O"
    elif compact:
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

    complaint = None
    note_field = re.search(
        r"(?:^|\n)\s*(?:note|description|chief\s*complaint|complaint|presenting)\s*[:\-]\s*([^\n]+)",
        t,
        re.IGNORECASE,
    )
    if note_field:
        complaint = re.sub(r"\s+", " ", note_field.group(1)).strip(" .,;")
    if not complaint:
        wellness = re.search(
            r"\b(no disease|no illness|no complaint|no complaints|well check|"
            r"checkup|check-up|healthy|asymptomatic|nothing wrong)\b",
            t,
            re.IGNORECASE,
        )
        if wellness:
            complaint = wellness.group(1).lower()
        else:
            # Drop labeled demographics so the complaint is not the whole form blob.
            stripped = t
            stripped = re.sub(
                r"(?:patient\s*name|full\s*name|\bname)\s*[:\-]\s*[^\n.]+[.\n]?",
                " ",
                stripped,
                flags=re.IGNORECASE,
            )
            stripped = re.sub(r"\bage\s*[:\-]?\s*\d{1,3}\b", " ", stripped, flags=re.IGNORECASE)
            stripped = re.sub(
                r"\b(?:sex|gender)\s*[:\-]\s*(?:M|F|O|Male|Female|Other|Others)\b",
                " ",
                stripped,
                flags=re.IGNORECASE,
            )
            stripped = re.sub(
                r"\bvitals?\s*[:\-]?\s*[^.\n]+",
                " ",
                stripped,
                flags=re.IGNORECASE,
            )
            stripped = re.sub(
                r"(?:^|\n)\s*prior\s*record\s*[:\-]?\s*(?:yes|no)\b",
                " ",
                stripped,
                flags=re.IGNORECASE,
            )
            stripped = re.sub(
                r"(?:^|\n)\s*(?:medical\s+)?history\s*[:\-]\s*[^\n]+",
                " ",
                stripped,
                flags=re.IGNORECASE,
            )
            stripped = re.sub(
                r"(?:^|\n)\s*medications?\s*[:\-]\s*[^\n]+",
                " ",
                stripped,
                flags=re.IGNORECASE,
            )
            stripped = re.sub(
                r"(?:^|\n)\s*allergies\s*[:\-]\s*[^\n]+",
                " ",
                stripped,
                flags=re.IGNORECASE,
            )
            stripped = re.sub(r"^\s*\d{1,3}\s*[MmFf]\s*[,:\-]?\s*", "", stripped).strip()
            stripped = re.sub(r"\s+", " ", stripped).strip(" .,;")
            if stripped and len(stripped) >= 3:
                complaint = stripped
            else:
                complaint = t

    return IntakeExtraction(
        patient_name=patient_name,
        age_years=age,
        sex=sex,
        arrival_mode=arrival,
        chief_complaint=complaint,
        pain_score=int(pain) if pain is not None and 0 <= pain <= 10 else None,
        responsiveness=responsiveness,
        heart_rate=hr,
        resp_rate=rr,
        sbp=sbp,
        dbp=dbp,
        spo2=spo2,
        temp_c=temp,
        onset_minutes=_onset_minutes(t),
        history=_list_after(
            [r"(?:medical\s+)?history", r"h/?x of", r"history of", r"pmh"],
            t,
        ),
        medications=_list_after([r"medications?", r"meds?", r"taking", r"on"], t),
        allergies=_list_after([r"allergies", r"allerg(?:y|ies) to", r"allergic to"], t),
    )


def template_explanation(result: TriageResult) -> str:
    """Two short clinical sentences justifying the ESI choice."""

    adj = result.adjudicator
    patient = result.patient
    name = (patient.display_name or "").strip()
    if not name or name.lower() in {"free-text intake", "walk-in"}:
        name = "This patient"

    complaint = (patient.chief_complaint or "").strip()
    if len(complaint) > 90:
        complaint = complaint[:87].rstrip() + "..."
    complaint_bit = f'"{complaint}"' if complaint else "this presentation"

    acuity = adj.acuity
    if acuity == 1:
        why = (
            f"{complaint_bit} shows immediate life-threat findings "
            "(airway, breathing, or circulation), so resuscitation comes first."
        )
    elif acuity == 2:
        why = (
            f"{complaint_bit} has high-risk or time-critical features, "
            "so this patient should not wait in a standard queue."
        )
    elif acuity == 3:
        why = (
            f"{complaint_bit} looks stable for the main ED track but will "
            "likely need a work-up rather than fast track alone."
        )
    elif acuity == 4:
        why = (
            f"{complaint_bit} is lower acuity and likely needs only limited "
            "evaluation before discharge or follow-up."
        )
    else:
        why = (
            f"{complaint_bit} looks minor, with no high-risk vitals or red flags "
            "on the information given, so fast track / waiting room is appropriate."
        )

    # Prefer concrete clinical points over internal labels when available.
    clinical = [
        d for d in adj.top_drivers
        if d and "expected resource" not in d.lower() and "decision " not in d.lower()
    ]
    if clinical and acuity in (1, 2):
        why = f"{complaint_bit}: {clinical[0]}."
        if len(clinical) > 1:
            why = f"{why} Also noted: {clinical[1]}."

    parts = [f"{name} is ESI {acuity} ({adj.placement}). {why}"]

    for c in adj.time_critical_clocks:
        if c.minutes_remaining is not None and c.minutes_remaining > 0:
            parts.append(
                f"A {c.name} clock is running with about {c.minutes_remaining} minutes left."
            )
            break

    if adj.confidence_band.value == "low":
        parts.append(
            "Information is incomplete, so a nurse should confirm before this patient waits unattended."
        )
    elif adj.routed_to_nurse:
        parts.append("Routed for nurse review.")

    return " ".join(parts).strip()
