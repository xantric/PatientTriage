"""The LLM facade the rest of the app talks to.

It owns provider selection, the parse and explain flows, ESI-flip verification,
and the telemetry log. The one rule it enforces above all: the model fills
intake fields and writes prose, and the deterministic engine sets the acuity.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from time import perf_counter

from app.engine.pipeline import triage
from app.llm import config
from app.llm.gemini import GeminiClient
from app.llm.stub import rule_parse, template_explanation
from app.llm.types import LLMResult
from app.models import (
    ArrivalMode,
    Explanation,
    IntakeExtraction,
    LLMCall,
    LLMStatus,
    ParsedIntake,
    Patient,
    Responsiveness,
    Sex,
    TelemetrySummary,
    TriageResult,
    Vitals,
)

_PARSE_SYSTEM = (
    "You convert a nurse's free-text triage note into structured JSON. Return "
    "only a JSON object. Use null for anything not stated. Never invent vital "
    "signs or numbers. Temperatures are Celsius; convert an obvious Fahrenheit "
    "reading. onset_minutes is minutes since symptoms began, if stated."
)

_PARSE_KEYS = (
    "age_years, sex (M/F/O), arrival_mode (walk_in/ambulance/wheelchair/carried), "
    "chief_complaint, pain_score (0-10), responsiveness (A/V/P/U), heart_rate, "
    "resp_rate, sbp, dbp, spo2, temp_c, onset_minutes, history (list), "
    "medications (list), allergies (list)"
)

_EXPLAIN_SYSTEM = (
    "You write a two-sentence, plain-language explanation of an emergency triage "
    "decision for a busy nurse. You are given the decision. Do not change the ESI "
    "level and do not state a different number. Do not add clinical findings that "
    "are not provided. No jargon and no dashes."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_sex(value: str | None) -> Sex:
    v = (value or "").strip().lower()
    if v in ("m", "male", "man", "boy"):
        return Sex.male
    if v in ("f", "female", "woman", "girl"):
        return Sex.female
    return Sex.other


def _to_arrival(value: str | None) -> ArrivalMode:
    v = (value or "").strip().lower()
    if "ambul" in v or v == "ems" or "paramedic" in v:
        return ArrivalMode.ambulance
    if "wheel" in v:
        return ArrivalMode.wheelchair
    if "carr" in v:
        return ArrivalMode.carried
    return ArrivalMode.walk_in


def _to_responsiveness(value: str | None) -> Responsiveness | None:
    v = (value or "").strip().lower()
    if not v:
        return None
    if v.startswith("u") or "unrespons" in v:
        return Responsiveness.unresponsive
    if v == "p" or "pain" in v:
        return Responsiveness.pain
    if v == "v" or "voice" in v or "drows" in v:
        return Responsiveness.voice
    if v == "a" or "alert" in v:
        return Responsiveness.alert
    return None


def _extraction_to_patient(x: IntakeExtraction, patient_id: str, raw_text: str) -> tuple[Patient, list[str]]:
    found: list[str] = []

    def note(name: str, value) -> None:
        if value is not None and value != [] and value != "":
            found.append(name)

    vitals = Vitals(
        heart_rate=x.heart_rate,
        resp_rate=x.resp_rate,
        sbp=x.sbp,
        dbp=x.dbp,
        spo2=x.spo2,
        temp_c=x.temp_c,
    )
    for key in ("heart_rate", "resp_rate", "sbp", "dbp", "spo2", "temp_c"):
        note(key, getattr(x, key))
    for key in ("age_years", "sex", "arrival_mode", "pain_score", "responsiveness",
                "onset_minutes", "history", "medications", "allergies"):
        note(key, getattr(x, key))
    complaint = (x.chief_complaint or raw_text).strip()
    if complaint:
        found.append("chief_complaint")

    patient = Patient(
        patient_id=patient_id,
        display_name="Free-text intake",
        age_years=float(x.age_years) if x.age_years is not None else 40.0,
        sex=_to_sex(x.sex),
        arrival_mode=_to_arrival(x.arrival_mode),
        chief_complaint=complaint,
        pain_score=x.pain_score if x.pain_score is not None and 0 <= x.pain_score <= 10 else None,
        responsiveness=_to_responsiveness(x.responsiveness),
        vitals=vitals,
        onset_minutes=x.onset_minutes,
        history=x.history,
        medications=x.medications,
        allergies=x.allergies,
        has_prior_record=False,
    )
    return patient, found


def _coerce_float(value) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+(?:\.\d+)?", value)
        return float(m.group()) if m else None
    return None


def _coerce_int(value) -> int | None:
    f = _coerce_float(value)
    return int(f) if f is not None else None


def _coerce_str(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _coerce_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _extraction_from_json(text: str) -> IntakeExtraction | None:
    """Read the model's JSON tolerantly: a single bad field is dropped, not fatal.

    Strict validation would discard a whole good extraction because the model
    wrote "about 40" instead of 40. We coerce field by field instead, so the
    live parse survives the small liberties a model takes with a schema.
    """

    try:
        raw = text.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", raw).strip()
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    return IntakeExtraction(
        age_years=_coerce_float(data.get("age_years")),
        sex=_coerce_str(data.get("sex")),
        arrival_mode=_coerce_str(data.get("arrival_mode")),
        chief_complaint=_coerce_str(data.get("chief_complaint")),
        pain_score=_coerce_int(data.get("pain_score")),
        responsiveness=_coerce_str(data.get("responsiveness")),
        heart_rate=_coerce_float(data.get("heart_rate")),
        resp_rate=_coerce_float(data.get("resp_rate")),
        sbp=_coerce_float(data.get("sbp")),
        dbp=_coerce_float(data.get("dbp")),
        spo2=_coerce_float(data.get("spo2")),
        temp_c=_coerce_float(data.get("temp_c")),
        onset_minutes=_coerce_int(data.get("onset_minutes")),
        history=_coerce_list(data.get("history")),
        medications=_coerce_list(data.get("medications")),
        allergies=_coerce_list(data.get("allergies")),
    )


def _mentions_wrong_acuity(text: str, acuity: int) -> bool:
    for m in re.finditer(r"(?:esi|level|category|acuity)\s*[:\-]?\s*([1-5])", text, re.IGNORECASE):
        if int(m.group(1)) != acuity:
            return True
    return False


class LLMService:
    def __init__(self, mode: str | None = None) -> None:
        self._telemetry: list[LLMCall] = []
        self.configure(mode)

    def configure(self, mode: str | None = None) -> None:
        """Pick the provider. `mode` overrides the environment when given."""

        self.configured = (mode or config.configured_mode()).strip().lower()
        self.key_present = config.api_key() is not None
        self.package_available = config.package_available()
        self.model = config.model_name()

        want_gemini = self.configured in ("auto", "gemini")
        self.live = want_gemini and self.key_present and self.package_available
        self.gemini = (
            GeminiClient(config.api_key(), self.model, config.timeout_ms())
            if self.live else None
        )

    # --- telemetry ---

    def _record(self, task: str, res: LLMResult, *, fell_back: bool) -> LLMCall:
        call = LLMCall(
            task=task,
            provider=res.provider,
            model=res.model,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            latency_ms=round(res.latency_ms, 1),
            cached=res.cached,
            ok=res.ok,
            fell_back=fell_back,
            est_cost_usd=config.estimate_cost(res.model, res.input_tokens, res.output_tokens)
            if res.provider == "gemini" else 0.0,
            error=res.error,
            timestamp_utc=_now_iso(),
        )
        self._telemetry.append(call)
        return call

    def status(self) -> LLMStatus:
        return LLMStatus(
            provider_mode="gemini" if self.live else "rule-based",
            model=self.model if self.live else "rule-based",
            configured_mode=self.configured,
            key_present=self.key_present,
            package_available=self.package_available,
            live=self.live,
        )

    def telemetry(self, recent: int = 20) -> TelemetrySummary:
        calls = self._telemetry
        n = len(calls)
        return TelemetrySummary(
            status=self.status(),
            total_calls=n,
            cache_hits=sum(1 for c in calls if c.cached),
            fallbacks=sum(1 for c in calls if c.fell_back),
            errors=sum(1 for c in calls if not c.ok),
            avg_latency_ms=round(sum(c.latency_ms for c in calls) / n, 1) if n else 0.0,
            total_input_tokens=sum(c.input_tokens for c in calls),
            total_output_tokens=sum(c.output_tokens for c in calls),
            est_cost_usd=round(sum(c.est_cost_usd for c in calls), 6),
            by_task={
                task: sum(1 for c in calls if c.task == task)
                for task in sorted({c.task for c in calls})
            },
            recent=list(reversed(calls[-recent:])),
        )

    # --- parse ---

    def parse_intake(self, text: str, patient_id: str) -> tuple[ParsedIntake, list[LLMCall]]:
        calls: list[LLMCall] = []
        extraction: IntakeExtraction | None = None
        source = "rule-based"
        note = None

        if self.gemini is not None:
            prompt = f"Fields to extract: {_PARSE_KEYS}.\n\nNote:\n{text}"
            res = self.gemini.generate(
                system=_PARSE_SYSTEM, prompt=prompt, json_mode=True,
                max_tokens=512, cache_key=f"parse::{text}",
            )
            calls.append(self._record("parse", res, fell_back=False))
            if res.ok:
                extraction = _extraction_from_json(res.text)
                if extraction is not None:
                    source = "gemini"
                else:
                    note = "model reply was not valid JSON; used rule-based parse"
            else:
                note = "model call failed; used rule-based parse"

        if extraction is None:
            start = perf_counter()
            extraction = rule_parse(text)
            fell_back = self.gemini is not None
            calls.append(self._record(
                "parse",
                LLMResult(text="", model="rule-based", provider="rule-based",
                          latency_ms=(perf_counter() - start) * 1000, ok=True),
                fell_back=fell_back,
            ))

        patient, found = _extraction_to_patient(extraction, patient_id, text)
        parsed = ParsedIntake(
            patient=patient, fields_found=found, source=source, note=note, raw_text=text,
        )
        return parsed, calls

    # --- explain ---

    def explain(self, result: TriageResult) -> tuple[Explanation, LLMCall]:
        acuity = result.adjudicator.acuity
        if self.gemini is not None:
            facts = (
                f"ESI level (do not change): {acuity}\n"
                f"Placement: {result.adjudicator.placement}\n"
                f"Top drivers: {', '.join(result.adjudicator.top_drivers)}\n"
                f"Time-critical: {', '.join(c.name for c in result.adjudicator.time_critical_clocks) or 'none'}\n"
                f"Confidence band: {result.adjudicator.confidence_band.value}\n"
                f"If ignored: {result.adjudicator.what_if_ignored}"
            )
            res = self.gemini.generate(
                system=_EXPLAIN_SYSTEM, prompt=facts, json_mode=False,
                max_tokens=200, cache_key=f"explain::{acuity}::{facts}",
            )
            call = self._record("explain", res, fell_back=False)
            verified = res.ok and 0 < len(res.text) <= 600 and not _mentions_wrong_acuity(res.text, acuity)
            if verified:
                return (
                    Explanation(text=res.text, source="gemini", verified=True,
                                patient_id=result.patient.patient_id),
                    call,
                )
            # Failed verification or call: fall through to the template.
            text = template_explanation(result)
            call = self._record(
                "explain",
                LLMResult(text=text, model="rule-based", provider="rule-based", ok=True),
                fell_back=True,
            )
            return (
                Explanation(text=text, source="template", verified=False,
                            patient_id=result.patient.patient_id),
                call,
            )

        start = perf_counter()
        text = template_explanation(result)
        call = self._record(
            "explain",
            LLMResult(text=text, model="rule-based", provider="rule-based",
                      latency_ms=(perf_counter() - start) * 1000, ok=True),
            fell_back=False,
        )
        return (
            Explanation(text=text, source="template", verified=True,
                        patient_id=result.patient.patient_id),
            call,
        )

    def triage_intake(self, text: str, patient_id: str) -> tuple[ParsedIntake, TriageResult, Explanation, list[LLMCall]]:
        parsed, calls = self.parse_intake(text, patient_id)
        result = triage(parsed.patient)
        explanation, explain_call = self.explain(result)
        calls.append(explain_call)
        return parsed, result, explanation, calls


LLM = LLMService()
