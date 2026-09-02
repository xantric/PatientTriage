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
    "patient_name, age_years, sex (M/F/O), arrival_mode (walk_in/ambulance/wheelchair/carried), "
    "chief_complaint, pain_score (0-10), responsiveness (A/V/P/U), heart_rate, "
    "resp_rate, sbp, dbp, spo2, temp_c, onset_minutes, history (list), "
    "medications (list), allergies (list)"
)

_EXPLAIN_SYSTEM = (
    "You write a two-sentence clinical justification of an emergency triage ESI "
    "level for a busy nurse. You are given the decision and the patient facts. "
    "Do not change the ESI level and do not state a different number. Explain "
    "why this level fits using the complaint, age, vitals, and risk in plain "
    "language. Do not invent findings. Never use internal scoring jargon such as "
    "drivers, expected resources, Decision A, Decision B, Decision C, or "
    "resource count. No em dashes or en dashes."
)

_REASSESS_SYSTEM = (
    "You are an expert triage agent. You are provided with a patient's entire clinical "
    "record including initial vitals, history, and a recent clinician follow-up note. "
    "Your job is to read the full context, reason over the free-text updates, and "
    "determine the final ESI triage level (1-5).\n\n"
    "Crucially: you MUST interpret qualitative text like 'everything is fine', 'stable', "
    "or 'looks well' as clinical reassurance that overrides alarming initial numbers. "
    "For example, if initial HR was 140 (which usually triggers ESI 2) but the clinician "
    "note says 'patient resting comfortably, vitals stable', you should confidently "
    "downgrade to ESI 3 or 4 based on the holistic picture.\n\n"
    "Return a JSON object with exactly two keys:\n"
    "- 'priority': an integer 1 through 5\n"
    "- 'explanation': a 1-2 sentence clinical justification for this ESI level, written "
    "for a busy nurse, incorporating the latest update."
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _to_sex(value: str | None) -> Sex:
    v = (value or "").strip().lower()
    if v in ("m", "male", "man", "boy"):
        return Sex.male
    if v in ("f", "female", "woman", "girl"):
        return Sex.female
    if v in ("o", "other", "others", "non-binary", "nonbinary", "x"):
        return Sex.other
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
    if x.patient_name:
        found.append("patient_name")
    complaint = (x.chief_complaint or raw_text).strip()
    if complaint:
        found.append("chief_complaint")

    display = (x.patient_name or "").strip() or "Walk-in"
    prior_from_text = bool(
        re.search(r"prior\s*record\s*[:\-]?\s*yes\b", raw_text, re.IGNORECASE)
    )
    has_prior = prior_from_text or bool(x.history or x.medications or x.allergies)

    patient = Patient(
        patient_id=patient_id,
        display_name=display,
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
        has_prior_record=has_prior,
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
        # Local models often add prose around the object; take the outer braces.
        if not raw.startswith("{"):
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                raw = raw[start : end + 1]
        # llama/qwen sometimes inject # or // comments inside "JSON".
        raw = re.sub(r"//.*?$", "", raw, flags=re.MULTILINE)
        raw = re.sub(r"#.*?$", "", raw, flags=re.MULTILINE)
        raw = re.sub(r",\s*}", "}", raw)
        raw = re.sub(r",\s*]", "]", raw)
        data = json.loads(raw)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    return IntakeExtraction(
        patient_name=_coerce_str(data.get("patient_name")),
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


def _merge_rule_gaps(extraction: IntakeExtraction, text: str) -> IntakeExtraction:
    """Prefer model values; fill nulls from the deterministic regex parse."""

    rule = rule_parse(text)
    data = extraction.model_dump()
    for key, value in rule.model_dump().items():
        if data.get(key) in (None, [], ""):
            data[key] = value
    # Compact "21M" often lands in chief_complaint; prefer a real complaint.
    cc = (data.get("chief_complaint") or "").strip()
    if re.fullmatch(r"\d{1,3}\s*[MmFf].*", cc) or re.search(
        r"\bno disease\b|\bno illness\b|\bhealthy\b|\basymptomatic\b", cc, re.I
    ):
        if rule.chief_complaint and rule.chief_complaint != cc:
            data["chief_complaint"] = rule.chief_complaint
    return IntakeExtraction(**data)


_WELLNESS_CUES = re.compile(
    r"\b(no disease|no illness|no complaint|no complaints|well check|"
    r"checkup|check-up|healthy|asymptomatic|nothing wrong)\b",
    re.IGNORECASE,
)

_MILD_DERM_CUES = re.compile(
    r"\b(itch|itching|itchy|pruritus|rash|eczema|dry skin|"
    r"insect bite|mosquito bite)\b",
    re.IGNORECASE,
)

_DERM_DANGER_CUES = re.compile(
    r"\b(anaphylaxis|lips swelling|tongue swelling|throat swelling|"
    r"difficulty breathing|short of breath|wheezing|hives all over)\b",
    re.IGNORECASE,
)


def _intake_information_gaps(parsed: ParsedIntake) -> list[str]:
    """Fields a clinician would need before trusting an ESI number."""

    gaps: list[str] = []
    p = parsed.patient
    found = set(parsed.fields_found)
    cc = (p.chief_complaint or "").strip()
    if "age_years" not in found:
        gaps.append("age")
    if not cc or len(cc) < 4:
        gaps.append("chief complaint")
    elif _WELLNESS_CUES.search(cc) and not any(
        getattr(p.vitals, k) is not None
        for k in ("heart_rate", "sbp", "spo2", "resp_rate", "temp_c")
    ):
        gaps.append("reason for visit (wellness note has no vitals or acute complaint)")
    vital_ok = any(
        getattr(p.vitals, k) is not None
        for k in ("heart_rate", "sbp", "spo2", "resp_rate")
    )
    if not vital_ok:
        gaps.append("vitals (HR, BP, SpO2, or RR)")
    # Mild skin complaints without vitals or airway/allergy danger should not
    # present as a firm high-acuity score.
    blob = f"{cc} {' '.join(p.history)}".lower()
    if (
        _MILD_DERM_CUES.search(blob)
        and not _DERM_DANGER_CUES.search(blob)
        and not vital_ok
    ):
        gaps.append("exam detail for skin complaint (spread, breathing, allergy signs)")
    return gaps


def _needs_more_information(
    parsed: ParsedIntake,
    *,
    recommendation_priority: int | None,
    agent_terminal: str | None,
) -> bool:
    gaps = _intake_information_gaps(parsed)
    if len(gaps) >= 2:
        return True
    if agent_terminal in ("REQUEST_INFORMATION", "ESCALATE", "AWAITING_HUMAN"):
        return recommendation_priority is None
    if recommendation_priority is None and gaps:
        return True
    return False


def _insufficient_explanation(gaps: list[str], patient_id: str) -> Explanation:
    listed = "; ".join(gaps) if gaps else "more clinical detail"
    text = (
        "Not enough information to assign a trusted urgency level. "
        f"Please add: {listed}. "
        "A provisional engine score may exist for reference only and should not "
        "be treated as the triage decision."
    )
    return Explanation(
        text=text, source="template", verified=True, patient_id=patient_id
    )


def _mentions_wrong_acuity(text: str, acuity: int) -> bool:
    for m in re.finditer(r"(?:esi|level|category|acuity)\s*[:\-]?\s*([1-5])", text, re.IGNORECASE):
        if int(m.group(1)) != acuity:
            return True
    return False


def _scrub_explain_jargon(text: str) -> str:
    """Strip leftover internal scoring phrases from a model explanation."""

    cleaned = text.strip()
    cleaned = re.sub(
        r"\b\d+\s+expected resource\(s\)\b",
        "limited ED evaluation needs",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\bexpected resources?\b", "ED evaluation needs", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(top\s*)?drivers?\b[:\-]?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bDecision\s+[A-D]\b[:\-]?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    return cleaned.strip()


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
        self.client = None
        self.gemini = None  # kept for older tests / callers

        if self.configured in ("auto", "gemini"):
            self.live = self.key_present and self.package_available
            if self.live:
                self.client = GeminiClient(
                    config.api_key() or "", self.model, config.timeout_ms()
                )
                self.gemini = self.client
        else:
            self.live = False

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
        if self.live:
            provider_mode = "gemini"
        else:
            provider_mode = "rule-based"
        return LLMStatus(
            provider_mode=provider_mode,
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

        if self.client is not None:
            prompt = f"Fields to extract: {_PARSE_KEYS}.\n\nNote:\n{text}"
            res = self.client.generate(
                system=_PARSE_SYSTEM, prompt=prompt, json_mode=True,
                max_tokens=512, cache_key=f"parse::{text}",
            )
            calls.append(self._record("parse", res, fell_back=False))
            if res.ok:
                extraction = _extraction_from_json(res.text)
                if extraction is not None:
                    # Fill gaps the model left blank (e.g. missed "21M" age).
                    extraction = _merge_rule_gaps(extraction, text)
                    source = res.provider
                else:
                    note = "model reply was not valid JSON; used rule-based parse"
            else:
                note = "model call failed; used rule-based parse"

        if extraction is None:
            start = perf_counter()
            extraction = rule_parse(text)
            fell_back = self.client is not None
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
        patient = result.patient
        complaint = (patient.chief_complaint or "").strip()
        clinical_points = [
            d for d in result.adjudicator.top_drivers
            if d and "expected resource" not in d.lower()
        ]
        if self.client is not None:
            facts = (
                f"Patient name: {patient.display_name or 'unknown'}\n"
                f"Age years: {patient.age_years}\n"
                f"Chief complaint: {complaint or 'not stated'}\n"
                f"ESI level (do not change): {acuity}\n"
                f"Placement: {result.adjudicator.placement}\n"
                f"Clinical points: {'; '.join(clinical_points) or 'none stated'}\n"
                f"Time-critical clocks: "
                f"{', '.join(c.name for c in result.adjudicator.time_critical_clocks) or 'none'}\n"
                f"Confidence band: {result.adjudicator.confidence_band.value}\n"
                f"Nurse review flagged: {result.adjudicator.routed_to_nurse}\n"
                "Write exactly two short sentences justifying this ESI choice."
            )
            res = self.client.generate(
                system=_EXPLAIN_SYSTEM, prompt=facts, json_mode=False,
                max_tokens=200, cache_key=f"explain::{acuity}::{facts}",
            )
            call = self._record("explain", res, fell_back=False)
            verified = res.ok and 0 < len(res.text) <= 600 and not _mentions_wrong_acuity(res.text, acuity)
            if verified:
                cleaned = _scrub_explain_jargon(res.text)
                return (
                    Explanation(text=cleaned, source=res.provider, verified=True,
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

    def triage_intake(self, text: str, patient_id: str) -> tuple[
        ParsedIntake,
        "PrimaryAssessmentResult",
        Explanation,
        list[LLMCall],
        dict,
    ]:
        """Parse intake, then run agent-primary assessment (engine = baseline/fallback).

        Returns an extra meta dict used by the API so thin notes never present a
        firm ESI plus a dramatic model explanation.
        """

        from app.agent.primary import run_primary_assessment
        from app.domain.enums import DecisionSource

        parsed, calls = self.parse_intake(text, patient_id)
        outcome = run_primary_assessment(parsed.patient)
        result = outcome.baseline_result

        rec = outcome.state.current_recommendation
        live_priority = rec.priority if rec is not None else None
        terminal = outcome.agent_run.terminal_action if outcome.agent_run else None
        if terminal is None and outcome.decision_source == DecisionSource.deterministic_fallback:
            terminal = "FALLBACK"

        gaps = _intake_information_gaps(parsed)
        # Also surface agent-requested gaps when present.
        if outcome.state.information_gaps:
            for g in outcome.state.information_gaps:
                label = g if isinstance(g, str) else str(g)
                if label and label not in gaps:
                    gaps.append(label)

        needs_more = _needs_more_information(
            parsed,
            recommendation_priority=live_priority,
            agent_terminal=terminal,
        )

        if needs_more:
            explanation = _insufficient_explanation(gaps, patient_id)
            calls.append(
                self._record(
                    "explain",
                    LLMResult(
                        text=explanation.text,
                        model="rule-based",
                        provider="rule-based",
                        ok=True,
                    ),
                    fell_back=True,
                )
            )
        elif (
            rec is not None
            and live_priority is not None
            and (rec.reason_summary or "").strip()
            and "deterministic fallback" not in rec.reason_summary.lower()
            and not _mentions_wrong_acuity(rec.reason_summary, live_priority)
        ):
            # Prefer the agent's own clinical summary when it matches the level.
            summary = _scrub_explain_jargon(rec.reason_summary.strip())
            explanation = Explanation(
                text=summary,
                source="agent",
                verified=True,
                patient_id=patient_id,
            )
            calls.append(
                self._record(
                    "explain",
                    LLMResult(text=summary, model="agent", provider="agent", ok=True),
                    fell_back=False,
                )
            )
            if live_priority != result.adjudicator.acuity:
                result = result.model_copy(deep=True)
                result.adjudicator.acuity = live_priority
                result.adjudicator.top_drivers = list(
                    rec.key_evidence[:5] or result.adjudicator.top_drivers
                )
        elif (
            rec is not None
            and live_priority is not None
            and live_priority != result.adjudicator.acuity
        ):
            explained = result.model_copy(deep=True)
            explained.adjudicator.acuity = live_priority
            explained.adjudicator.top_drivers = list(
                rec.key_evidence[:5] or explained.adjudicator.top_drivers
            )
            explanation, explain_call = self.explain(explained)
            calls.append(explain_call)
            result = explained
        else:
            # Use template/LLM explain so fallback path still gets clinical prose.
            target = result
            if rec is not None and live_priority is not None:
                target = result.model_copy(deep=True)
                target.adjudicator.acuity = live_priority
                target.adjudicator.top_drivers = list(
                    rec.key_evidence[:5] or target.adjudicator.top_drivers
                )
                result = target
            explanation, explain_call = self.explain(target)
            calls.append(explain_call)

        meta = {
            "needs_more_information": needs_more,
            "information_gaps": gaps,
            "decision_source": outcome.decision_source.value,
            "live_priority": live_priority,
            "agent_status": (
                outcome.state.status.value
                if hasattr(outcome.state.status, "value")
                else str(outcome.state.status)
            ),
        }
        return parsed, result, explanation, calls, meta

    def reassess(self, text: str, patient_id: str) -> tuple[int | None, Explanation | None, list[LLMCall]]:
        """A direct LLM assessment bypassing the deterministic engine."""
        if self.client is None:
            return None, None, []
            
        calls = []
        res = self.client.generate(
            system=_REASSESS_SYSTEM, prompt=text, json_mode=True,
            max_tokens=250, cache_key=f"reassess::{text}",
        )
        calls.append(self._record("reassess", res, fell_back=False))
        if res.ok:
            try:
                # Clean and parse JSON
                raw = res.text.strip()
                if raw.startswith("```"):
                    raw = re.sub(r"^```[a-zA-Z]*\n?|\n?```$", "", raw).strip()
                if not raw.startswith("{"):
                    start = raw.find("{")
                    end = raw.rfind("}")
                    if start >= 0 and end > start:
                        raw = raw[start: end + 1]
                data = json.loads(raw)
                priority = int(data.get("priority", 0))
                expl_text = data.get("explanation", "")
                
                if 1 <= priority <= 5 and expl_text:
                    explanation = Explanation(
                        text=expl_text, source="gemini", verified=True,
                        patient_id=patient_id,
                    )
                    return priority, explanation, calls
            except Exception:
                pass
                
        return None, None, calls

LLM = LLMService()
