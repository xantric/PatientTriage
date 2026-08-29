"""Operational timeline for the patient detail UI.

No chain-of-thought. Only observable actions and facts.
"""

from __future__ import annotations

from app.domain.enums import AgentStatus, ToolCallStatus
from app.domain.models import TriageAgentState


_STATUS_LABELS = {
    AgentStatus.INITIALIZING: "Observing",
    AgentStatus.OBSERVING: "Observing",
    AgentStatus.GATHERING_INFORMATION: "Gathering information",
    AgentStatus.REASONING: "Reasoning",
    AgentStatus.RECOMMENDING: "Recommendation ready",
    AgentStatus.AWAITING_HUMAN: "Awaiting clinician",
    AgentStatus.ESCALATED: "Escalated",
    AgentStatus.COMPLETED: "Completed",
    AgentStatus.FAILED: "Awaiting clinician",
}


def status_label(status: AgentStatus) -> str:
    return _STATUS_LABELS.get(status, status.value.replace("_", " ").title())


def build_operational_timeline(state: TriageAgentState) -> list[dict]:
    """Build chronological operational events for the UI."""

    items: list[dict] = []

    items.append(
        {
            "time_min": 0,
            "kind": "arrival",
            "label": "Patient event received",
        }
    )
    items.append(
        {
            "time_min": 0,
            "kind": "observe",
            "label": "Agent observed patient data",
        }
    )

    for gap in state.information_gaps:
        if "missing" in gap.lower() or "requested" in gap.lower() or "bp" in gap.lower():
            items.append(
                {
                    "time_min": 0,
                    "kind": "gap",
                    "label": f"Agent identified {gap}",
                }
            )

    for call in state.tool_history:
        if call.status == ToolCallStatus.skipped:
            items.append(
                {
                    "time_min": call.iteration,
                    "kind": "tool_skip",
                    "label": f"Skipped duplicate {call.tool_name}",
                }
            )
            continue
        if call.status == ToolCallStatus.error:
            items.append(
                {
                    "time_min": call.iteration,
                    "kind": "tool_error",
                    "label": f"Tool {call.tool_name} unavailable or rejected",
                }
            )
            continue
        label = f"Called {call.tool_name}"
        if call.tool_name == "get_baseline_engine_assessment":
            label = "Checked deterministic baseline"
        elif call.tool_name == "calculate_shock_index":
            label = "Calculated shock index"
        elif call.tool_name == "get_vital_history":
            label = "Called get_vital_history"
        elif call.tool_name == "get_latest_vitals":
            label = "Called get_latest_vitals"
        items.append(
            {
                "time_min": call.iteration,
                "kind": "tool",
                "label": label,
            }
        )

    if state.baseline_assessment is not None:
        items.append(
            {
                "time_min": state.iteration_count,
                "kind": "baseline",
                "label": (
                    f"Baseline was P{state.baseline_assessment.priority}"
                    if state.baseline_assessment.priority is not None
                    else "Deterministic baseline recorded"
                ),
            }
        )

    if state.current_recommendation is not None:
        pri = state.current_recommendation.priority
        items.append(
            {
                "time_min": state.iteration_count,
                "kind": "recommend",
                "label": (
                    f"Generated P{pri} recommendation"
                    if pri is not None
                    else "Generated recommendation"
                ),
            }
        )
        items.append(
            {
                "time_min": state.iteration_count,
                "kind": "hitl_queue",
                "label": "Sent recommendation to clinician",
            }
        )

    if state.clinician_decision is not None:
        d = state.clinician_decision
        action = d.action.value.replace("_", " ")
        if d.active_priority is not None:
            items.append(
                {
                    "time_min": d.decided_at_min,
                    "kind": "clinician",
                    "label": f"Clinician {action} P{d.active_priority}",
                }
            )
        else:
            items.append(
                {
                    "time_min": d.decided_at_min,
                    "kind": "clinician",
                    "label": f"Clinician {action}",
                }
            )

    for ev in state.triage_events:
        if ev.event_type.value == "DETERIORATION_DETECTED":
            items.append(
                {
                    "time_min": ev.time_min,
                    "kind": "deterioration",
                    "label": "Watcher detected deterioration",
                }
            )

    # Stable order: time then insertion order already roughly chronological.
    return items
