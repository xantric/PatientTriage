"""Agent package: tools, Gemini primary loop, HITL, Watcher, evaluation."""

from app.agent.context import ToolContext
from app.agent.event_watcher import EventDrivenWatcher, simulate_event_driven
from app.agent.hitl import ClinicianDecisionInput, HitlError, apply_clinician_decision
from app.agent.loop import BUDGET_EXHAUSTED_MESSAGE, MAX_ITERATIONS
from app.agent.orchestrator import (
    AgentRunResult,
    GeminiAgentLLM,
    MockAgentLLM,
    TriageAgentOrchestrator,
    build_default_agent_llm,
)
from app.agent.primary import PrimaryAssessmentResult, run_primary_assessment
from app.agent.prompt import SYSTEM_PROMPT
from app.agent.reassessment import ReassessmentBus
from app.agent.registry import ToolRegistry, ToolResult, get_default_registry
from app.agent import tools as tool_impls

__all__ = [
    "AgentRunResult",
    "BUDGET_EXHAUSTED_MESSAGE",
    "ClinicianDecisionInput",
    "EventDrivenWatcher",
    "GeminiAgentLLM",
    "HitlError",
    "MAX_ITERATIONS",
    "MockAgentLLM",
    "PrimaryAssessmentResult",
    "ReassessmentBus",
    "SYSTEM_PROMPT",
    "ToolContext",
    "ToolRegistry",
    "ToolResult",
    "TriageAgentOrchestrator",
    "apply_clinician_decision",
    "build_default_agent_llm",
    "get_default_registry",
    "run_primary_assessment",
    "simulate_event_driven",
    "tool_impls",
]
