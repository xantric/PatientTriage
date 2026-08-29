"""System and turn prompts for the Gemini triage agent.

The model forms its own recommendation. Deterministic baseline is optional
evidence via tools, never a mandatory pipeline step.
"""

from __future__ import annotations

from typing import Any, Optional

from app.agent.registry import ToolRegistry
from app.domain.models import TriageAgentState


SYSTEM_PROMPT = """You are an emergency department triage decision-support agent.

You are responsible for forming a triage recommendation from available patient information.

You may gather information and use tools.
You should actively identify information gaps.
You should reconsider your recommendation when new information appears.
You may disagree with deterministic baseline assessments.
You must distinguish observed facts from inference.
You must never invent patient data.
You must never diagnose.
You must never prescribe.
You must never autonomously treat.
You must never autonomously discharge.

Your recommendation is always subject to clinician review.
When information is insufficient or uncertainty is material, request more information or escalate to a clinician.

You choose tools dynamically based on what you still need. There is no fixed
Interpreter then Adjudicator sequence. Different patients may need different tools.

Respond with a single JSON object. No markdown fences. No prose outside JSON.

Allowed actions:

CALL_TOOL:
{"action":"CALL_TOOL","tool":"<registered_tool_name>","arguments":{...}}

REQUEST_INFORMATION:
{"action":"REQUEST_INFORMATION","fields":["blood_pressure"],"reason":"..."}

RECOMMEND:
{"action":"RECOMMEND","recommendation":{"priority":2,"urgency":"high","care_pathway":"...","monitoring_plan":"...","confidence":0.81,"reason_summary":"...","key_evidence":["..."],"information_gaps":["..."]}}

ESCALATE:
{"action":"ESCALATE","reason":"..."}

priority is optional integer 1-5 (1 most urgent). confidence is 0.0-1.0.
reason_summary and key_evidence must be concise operational facts, not chain-of-thought.
If you call get_baseline_engine_assessment, treat it as SOURCE=deterministic_baseline
reference only. Do not copy it as your answer unless you independently agree.
"""


def tools_catalog(registry: ToolRegistry) -> str:
    lines = ["Registered tools (only these names may be used):"]
    for item in registry.describe():
        lines.append(f"- {item['name']}: {item['description']}")
    return "\n".join(lines)


def _vitals_line(state: TriageAgentState) -> str:
    v = state.latest_vitals
    if v is None or v.is_empty():
        return "Latest vitals: none recorded."
    parts = [f"{k}={getattr(v, k)}" for k in v.present_fields()]
    return "Latest vitals: " + ", ".join(parts) + "."


def observation_packet(state: TriageAgentState) -> str:
    """Facts available this turn. Operational summaries only."""

    ctx = state.patient_context
    lines = [
        f"patient_id: {state.patient_id}",
        f"age_years: {ctx.age_years}",
        f"sex: {ctx.sex.value}",
        f"arrival_mode: {ctx.arrival_mode.value}",
        f"chief_complaint: {ctx.chief_complaint or '(none)'}",
        f"pain_score: {ctx.pain_score}",
        f"responsiveness: {ctx.responsiveness.value if ctx.responsiveness else None}",
        f"onset_minutes: {ctx.onset_minutes}",
        f"history: {', '.join(ctx.history) if ctx.history else '(none)'}",
        f"medications: {', '.join(ctx.medications) if ctx.medications else '(none)'}",
        f"allergies: {', '.join(ctx.allergies) if ctx.allergies else '(none)'}",
        _vitals_line(state),
        f"vital_history_count: {len(state.vital_history)}",
        f"iteration: {state.iteration_count}",
        f"status: {state.status.value}",
    ]
    if state.information_gaps:
        lines.append("known_gaps: " + "; ".join(state.information_gaps))
    if state.current_observations:
        lines.append("observations:")
        for obs in state.current_observations[-8:]:
            lines.append(f"  - {obs.summary}")
    if state.retrieved_context:
        lines.append("tool_context:")
        for snippet in state.retrieved_context[-12:]:
            lines.append(f"  - {snippet}")
    if state.previous_recommendation is not None:
        pr = state.previous_recommendation
        lines.append(
            f"previous_recommendation: priority={pr.priority}, "
            f"confidence={pr.confidence}, summary={pr.reason_summary}"
        )
    return "\n".join(lines)


def build_turn_prompt(
    *,
    state: TriageAgentState,
    registry: ToolRegistry,
    last_tool_feedback: Optional[str] = None,
    parse_error: Optional[str] = None,
) -> str:
    """User turn: observation packet + tool catalog + optional feedback."""

    sections = [
        "Current patient observation packet:",
        observation_packet(state),
        "",
        tools_catalog(registry),
        "",
        "Choose exactly one next action as JSON.",
    ]
    if last_tool_feedback:
        sections.extend(["", "Last tool result:", last_tool_feedback])
    if parse_error:
        sections.extend(
            [
                "",
                "Your previous output was rejected:",
                parse_error,
                "Emit a valid action JSON now.",
            ]
        )
    return "\n".join(sections)


def cache_key_for_turn(
    patient_id: str,
    iteration: int,
    prompt: str,
    extra: str = "",
) -> str:
    """Stable-enough cache key for demo reruns; unique per turn content."""

    digest = abs(hash((patient_id, iteration, prompt, extra))) % (10**12)
    return f"triage_agent::{patient_id}::{iteration}::{digest}"
