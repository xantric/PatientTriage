"""Agent action schemas, parsing, and state updates for one loop step.

Validates model JSON. Rejects unknown tools at apply time via the registry.
Does not hardcode Interpreter → Adjudicator → Recommendation.
"""

from __future__ import annotations

import json
import re
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field, ValidationError, field_validator

from app.agent.context import ToolContext
from app.agent.registry import ToolRegistry, ToolResult
from app.domain.enums import AgentStatus, ObservationSource, ToolCallStatus
from app.domain.models import (
    AgentObservation,
    AgentRecommendation,
    AgentToolCall,
    BaselineAssessment,
    BaselineComparison,
    ConfidenceBand,
    TriageAgentState,
)

# Phase 5 bound on the OBSERVE → REASON → ACT loop.
MAX_ITERATIONS = 5

BUDGET_EXHAUSTED_MESSAGE = (
    "The agent could not safely complete the assessment within its reasoning budget."
)

ActionName = Literal["CALL_TOOL", "REQUEST_INFORMATION", "RECOMMEND", "ESCALATE"]


class CallToolAction(BaseModel):
    action: Literal["CALL_TOOL"] = "CALL_TOOL"
    tool: str = Field(..., min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class RequestInformationAction(BaseModel):
    action: Literal["REQUEST_INFORMATION"] = "REQUEST_INFORMATION"
    fields: list[str] = Field(..., min_length=1)
    reason: str = Field(..., min_length=1, max_length=500)

    @field_validator("fields")
    @classmethod
    def fields_nonempty(cls, value: list[str]) -> list[str]:
        cleaned = [f.strip() for f in value if f and str(f).strip()]
        if not cleaned:
            raise ValueError("fields must list at least one information need")
        return cleaned


class RecommendationPayload(BaseModel):
    priority: Optional[int] = Field(None, ge=1, le=5)
    urgency: str = Field(default="", max_length=120)
    care_pathway: str = Field(default="", max_length=200)
    monitoring_plan: str = Field(default="", max_length=200)
    confidence: float = Field(..., ge=0.0, le=1.0)
    reason_summary: str = Field(..., min_length=1, max_length=500)
    key_evidence: list[str] = Field(default_factory=list)
    information_gaps: list[str] = Field(default_factory=list)
    confidence_band: Optional[ConfidenceBand] = None
    human_review_required: bool = True


class RecommendAction(BaseModel):
    action: Literal["RECOMMEND"] = "RECOMMEND"
    recommendation: RecommendationPayload


class EscalateAction(BaseModel):
    action: Literal["ESCALATE"] = "ESCALATE"
    reason: str = Field(..., min_length=1, max_length=500)


AgentAction = Union[
    CallToolAction,
    RequestInformationAction,
    RecommendAction,
    EscalateAction,
]


class ParseFailure(BaseModel):
    ok: bool = False
    error: str
    raw: str = ""


class ParseSuccess(BaseModel):
    ok: bool = True
    action: AgentAction


def _strip_fences(text: str) -> str:
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def parse_model_action(text: str) -> ParseSuccess | ParseFailure:
    """Validate model output into a typed action. Rejects unknown shapes."""

    raw = _strip_fences(text)
    if not raw:
        return ParseFailure(error="empty model output", raw=text or "")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return ParseFailure(error=f"invalid JSON: {exc}", raw=raw)

    if not isinstance(data, dict):
        return ParseFailure(error="action must be a JSON object", raw=raw)

    name = data.get("action")
    try:
        if name == "CALL_TOOL":
            action: AgentAction = CallToolAction.model_validate(data)
        elif name == "REQUEST_INFORMATION":
            action = RequestInformationAction.model_validate(data)
        elif name == "RECOMMEND":
            action = RecommendAction.model_validate(data)
        elif name == "ESCALATE":
            action = EscalateAction.model_validate(data)
        else:
            return ParseFailure(
                error=f"unknown or missing action: {name!r}",
                raw=raw,
            )
    except ValidationError as exc:
        return ParseFailure(error=f"invalid action structure: {exc.errors()}", raw=raw)

    return ParseSuccess(action=action)


def normalize_tool_arguments(
    tool: str,
    arguments: dict[str, Any],
    patient_id: str,
) -> dict[str, Any]:
    args = dict(arguments or {})
    if "patient_id" not in args:
        args["patient_id"] = patient_id
    return args


def tool_call_signature(tool: str, arguments: dict[str, Any]) -> str:
    """Stable identity for duplicate detection (tool + sorted args)."""

    return json.dumps({"tool": tool, "arguments": arguments}, sort_keys=True, default=str)


def is_duplicate_tool_call(
    state: TriageAgentState,
    tool: str,
    arguments: dict[str, Any],
) -> bool:
    sig = tool_call_signature(tool, arguments)
    for prior in state.tool_history:
        if prior.status == ToolCallStatus.skipped:
            continue
        prior_args = dict(prior.arguments or {})
        if tool_call_signature(prior.tool_name, prior_args) == sig:
            return True
    return False


def sync_baseline_comparison(state: TriageAgentState) -> None:
    """Store agent vs baseline priorities. Never rewrites either side."""

    if state.baseline_assessment is None:
        return
    if state.current_recommendation is None:
        return

    bp = state.baseline_assessment.priority
    ap = state.current_recommendation.priority
    agrees: Optional[bool] = None
    delta: Optional[int] = None
    disagreement_reason: Optional[str] = None
    if bp is not None and ap is not None:
        agrees = bp == ap
        delta = ap - bp
        if not agrees:
            disagreement_reason = (
                f"Agent priority {ap} differs from deterministic baseline {bp}."
            )
    summary = (
        f"agent={ap}, baseline={bp}, agrees={agrees}"
        if agrees is not None
        else f"agent={ap}, baseline={bp}"
    )
    state.baseline_comparison = BaselineComparison(
        baseline_priority=bp,
        agent_priority=ap,
        agrees=agrees,
        delta=delta,
        disagreement_reason=disagreement_reason,
        summary=summary[:280],
    )


def seed_initial_observations(state: TriageAgentState) -> None:
    """OBSERVE: seed concise facts from intake without inventing data."""

    if state.current_observations:
        return
    ctx = state.patient_context
    facts: list[str] = []
    facts.append(f"Age {ctx.age_years} years.")
    if ctx.chief_complaint:
        facts.append(f"Chief complaint: {ctx.chief_complaint}.")
    if state.latest_vitals is not None and not state.latest_vitals.is_empty():
        present = state.latest_vitals.present_fields()
        dump = state.latest_vitals.model_dump()
        facts.append(
            "Observed vitals: "
            + ", ".join(f"{k}={dump[k]}" for k in present)
            + "."
        )
        missing_common = [
            label
            for key, label in (
                ("sbp", "blood pressure (SBP)"),
                ("heart_rate", "heart rate"),
                ("spo2", "SpO2"),
            )
            if key not in present
        ]
        for label in missing_common:
            facts.append(f"Missing: {label}.")
            if label not in state.information_gaps:
                state.information_gaps.append(f"Missing: {label}.")
    else:
        facts.append("No vitals recorded at intake.")
        state.information_gaps.append("No vitals recorded at intake.")

    for summary in facts:
        state.current_observations.append(
            AgentObservation(
                summary=summary,
                source=ObservationSource.system,
                time_min=0,
            )
        )
    state.status = AgentStatus.OBSERVING


def apply_tool_call(
    *,
    state: TriageAgentState,
    context: ToolContext,
    registry: ToolRegistry,
    action: CallToolAction,
    iteration: int,
) -> ToolResult:
    """CHOOSE TOOL → CALL TOOL → OBSERVE RESULT → UPDATE STATE."""

    state.status = AgentStatus.GATHERING_INFORMATION
    args = normalize_tool_arguments(action.tool, action.arguments, state.patient_id)

    result = registry.execute(action.tool, context, args)
    status = ToolCallStatus.ok if result.ok else ToolCallStatus.error
    call = AgentToolCall(
        tool_name=action.tool,
        arguments=args,
        status=status,
        result_summary=result.summary[:500],
        result_payload=result.evidence if result.ok else {"error": result.error},
        error=result.error,
        iteration=iteration,
    )
    state.tool_history.append(call)

    snippet = f"{action.tool}: {result.summary}"
    state.retrieved_context.append(snippet[:400])
    state.current_observations.append(
        AgentObservation(
            summary=snippet[:280],
            source=ObservationSource.tool,
            evidence_refs=[call.call_id],
            time_min=iteration,
        )
    )

    if (
        result.ok
        and action.tool == "get_baseline_engine_assessment"
        and result.evidence.get("source") == "deterministic_baseline"
    ):
        state.baseline_assessment = BaselineAssessment(
            source="deterministic_baseline",
            priority=result.evidence.get("priority"),
            placement=result.evidence.get("placement"),
            monitoring_tier=result.evidence.get("monitoring_tier"),
            confidence=result.evidence.get("confidence"),
            drivers=list(result.evidence.get("drivers") or []),
            summary=result.summary[:500],
        )
        state.baseline_used = True
        sync_baseline_comparison(state)

    if result.ok and action.tool == "calculate_shock_index":
        si = result.evidence.get("shock_index")
        state.derived_metrics.shock_index = si
        state.derived_metrics.shock_index_flag = result.evidence.get(
            "shock_index_flag", "unknown"
        )
        state.derived_metrics.age_band = result.evidence.get("age_band")

    if result.ok and action.tool == "assess_data_completeness":
        state.derived_metrics.completeness = result.evidence.get("completeness")
        for gap in result.evidence.get("missing_readable") or []:
            if gap not in state.information_gaps:
                state.information_gaps.append(gap)

    return result


def record_skipped_duplicate(
    state: TriageAgentState,
    tool: str,
    arguments: dict[str, Any],
    iteration: int,
) -> str:
    """Record a prevented duplicate tool call without re-executing it."""

    msg = (
        f"Duplicate tool call prevented: {tool} with the same arguments "
        "was already executed. Choose a different tool or conclude."
    )
    state.tool_history.append(
        AgentToolCall(
            tool_name=tool,
            arguments=arguments,
            status=ToolCallStatus.skipped,
            result_summary=msg[:500],
            error="duplicate_tool_call",
            iteration=iteration,
        )
    )
    state.current_observations.append(
        AgentObservation(
            summary=msg[:280],
            source=ObservationSource.system,
            time_min=iteration,
        )
    )
    return msg


def apply_request_information(
    state: TriageAgentState, action: RequestInformationAction
) -> None:
    for field in action.fields:
        gap = f"Requested: {field}"
        if gap not in state.information_gaps:
            state.information_gaps.append(gap)
    state.uncertainty_reasons.append(action.reason[:280])
    state.current_observations.append(
        AgentObservation(
            summary=(
                f"Requested information: {', '.join(action.fields)}. {action.reason}"
            )[:280],
            source=ObservationSource.system,
        )
    )
    state.human_review_required = True
    state.status = AgentStatus.AWAITING_HUMAN


def apply_recommend(state: TriageAgentState, action: RecommendAction) -> None:
    payload = action.recommendation
    if state.current_recommendation is not None:
        state.previous_recommendation = state.current_recommendation

    band = payload.confidence_band
    if band is None:
        if payload.confidence >= 0.75:
            band = ConfidenceBand.high
        elif payload.confidence >= 0.5:
            band = ConfidenceBand.medium
        else:
            band = ConfidenceBand.low

    rec = AgentRecommendation(
        priority=payload.priority,
        urgency=payload.urgency,
        care_pathway=payload.care_pathway,
        monitoring_plan=payload.monitoring_plan,
        confidence=payload.confidence,
        confidence_band=band,
        reason_summary=payload.reason_summary,
        key_evidence=list(payload.key_evidence),
        information_gaps=list(payload.information_gaps)
        or list(state.information_gaps),
        human_review_required=payload.human_review_required,
        revision=(
            state.current_recommendation.revision + 1
            if state.current_recommendation
            else 0
        ),
    )
    state.current_recommendation = rec
    state.agent_confidence = rec.confidence
    state.human_review_required = True
    state.status = AgentStatus.AWAITING_HUMAN
    sync_baseline_comparison(state)


def apply_escalate(state: TriageAgentState, action: EscalateAction) -> None:
    state.status = AgentStatus.ESCALATED
    state.human_review_required = True
    state.uncertainty_reasons.append(action.reason[:280])
    state.current_observations.append(
        AgentObservation(
            summary=f"Escalated to clinician: {action.reason}"[:280],
            source=ObservationSource.system,
        )
    )


def apply_budget_exhausted(state: TriageAgentState) -> None:
    """Iteration budget spent: hand off to human, do not invent a priority."""

    state.status = AgentStatus.AWAITING_HUMAN
    state.human_review_required = True
    if BUDGET_EXHAUSTED_MESSAGE not in state.uncertainty_reasons:
        state.uncertainty_reasons.append(BUDGET_EXHAUSTED_MESSAGE)
    state.current_observations.append(
        AgentObservation(
            summary=BUDGET_EXHAUSTED_MESSAGE,
            source=ObservationSource.system,
        )
    )


def is_terminal_after_action(action: AgentAction) -> bool:
    return isinstance(
        action, (RecommendAction, EscalateAction, RequestInformationAction)
    )
