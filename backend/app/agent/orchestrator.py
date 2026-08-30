"""Gemini-primary triage agent orchestrator.

Bounded loop (MAX_ITERATIONS=5):

OBSERVE → REASON → SELECT ACTION →
CALL TOOL / REQUEST INFORMATION / RECOMMEND / ESCALATE →
OBSERVE RESULT → UPDATE STATE → REASON AGAIN

Duplicate identical tool calls are blocked. Budget exhaustion parks the
session in AWAITING_HUMAN. HITL is applied separately via app.agent.hitl.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import perf_counter
from typing import Any, Optional, Protocol

from app.agent.context import ToolContext
from app.agent.loop import (
    MAX_ITERATIONS,
    CallToolAction,
    EscalateAction,
    RecommendAction,
    RequestInformationAction,
    apply_budget_exhausted,
    apply_escalate,
    apply_recommend,
    apply_request_information,
    apply_tool_call,
    is_duplicate_tool_call,
    normalize_tool_arguments,
    parse_model_action,
    record_skipped_duplicate,
    seed_initial_observations,
)
from app.agent.prompt import SYSTEM_PROMPT, build_turn_prompt, cache_key_for_turn
from app.agent.registry import ToolRegistry, get_default_registry
from app.domain.enums import AgentStatus, ToolCallStatus
from app.domain.models import AgentToolCall, Patient, TriageAgentState
from app.llm import config as llm_config
from app.llm.gemini import GeminiClient
from app.llm.types import LLMResult
from app.models import LLMCall


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class AgentLLM(Protocol):
    """Minimal completion interface. Tests inject mocks; prod uses Gemini."""

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        cache_key: str,
        max_tokens: int = 1024,
    ) -> LLMResult: ...


class GeminiAgentLLM:
    """Wraps GeminiClient: API key, model, timeout, cache, failure handling."""

    def __init__(
        self,
        client: Optional[GeminiClient] = None,
        *,
        mode: Optional[str] = None,
    ) -> None:
        self.configured = (mode or llm_config.configured_mode()).strip().lower()
        self.key_present = llm_config.api_key() is not None
        self.package_available = llm_config.package_available()
        self.model = llm_config.model_name()
        want = self.configured in ("auto", "gemini")
        self.live = want and self.key_present and self.package_available

        if client is not None:
            self._client = client
            self.live = True
            self.model = client.model
        elif self.live:
            self._client = GeminiClient(
                llm_config.api_key() or "",
                self.model,
                llm_config.timeout_ms(),
            )
        else:
            self._client = None

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        cache_key: str,
        max_tokens: int = 1024,
    ) -> LLMResult:
        if self._client is None:
            return LLMResult(
                text="",
                model=self.model or "unavailable",
                provider="gemini",
                ok=False,
                error="Gemini unavailable (no API key, package, or stub mode)",
            )
        return self._client.generate(
            system=system,
            prompt=prompt,
            json_mode=True,
            max_tokens=max_tokens,
            cache_key=cache_key,
        )


def build_default_agent_llm() -> AgentLLM:
    """Build the Gemini agent LLM from SENTINEL_LLM / env."""

    return GeminiAgentLLM()


class MockAgentLLM:
    """Scripted actions for tests. Each complete() pops the next JSON string."""

    def __init__(
        self,
        responses: list[str],
        *,
        model: str = "mock-agent",
        fail_after: Optional[int] = None,
    ) -> None:
        self.responses = list(responses)
        self.model = model
        self.fail_after = fail_after
        self.calls: list[dict[str, Any]] = []
        self._n = 0

    def complete(
        self,
        *,
        system: str,
        prompt: str,
        cache_key: str,
        max_tokens: int = 1024,
    ) -> LLMResult:
        self.calls.append(
            {
                "system": system,
                "prompt": prompt,
                "cache_key": cache_key,
                "max_tokens": max_tokens,
            }
        )
        if self.fail_after is not None and self._n >= self.fail_after:
            self._n += 1
            return LLMResult(
                text="",
                model=self.model,
                provider="mock",
                ok=False,
                error="mock failure",
            )
        if not self.responses:
            return LLMResult(
                text="",
                model=self.model,
                provider="mock",
                ok=False,
                error="mock exhausted",
            )
        text = self.responses.pop(0)
        self._n += 1
        return LLMResult(
            text=text,
            model=self.model,
            provider="mock",
            input_tokens=10,
            output_tokens=20,
            ok=True,
        )


@dataclass
class AgentRunResult:
    """Outcome of one agent assessment session before or awaiting HITL."""

    state: TriageAgentState
    patient_id: str
    terminal_action: Optional[str] = None
    iterations: int = 0
    llm_calls: list[LLMCall] = field(default_factory=list)
    stopped_reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state.status in (
            AgentStatus.AWAITING_HUMAN,
            AgentStatus.GATHERING_INFORMATION,
            AgentStatus.ESCALATED,
            AgentStatus.COMPLETED,
        )


class TriageAgentOrchestrator:
    """Primary reasoning loop. Gemini (or mock) chooses tools dynamically."""

    def __init__(
        self,
        llm: AgentLLM,
        *,
        registry: Optional[ToolRegistry] = None,
        max_iterations: int = MAX_ITERATIONS,
        max_parse_retries: int = 2,
    ) -> None:
        self.llm = llm
        self.registry = registry or get_default_registry()
        self.max_iterations = max_iterations
        self.max_parse_retries = max_parse_retries
        self._telemetry: list[LLMCall] = []

    @property
    def telemetry(self) -> list[LLMCall]:
        return list(self._telemetry)

    def _record(self, res: LLMResult, *, fell_back: bool = False) -> LLMCall:
        call = LLMCall(
            task="triage_agent",
            provider=res.provider,
            model=res.model,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            latency_ms=round(res.latency_ms, 1),
            cached=res.cached,
            ok=res.ok,
            fell_back=fell_back,
            est_cost_usd=(
                llm_config.estimate_cost(res.model, res.input_tokens, res.output_tokens)
                if res.provider == "gemini"
                else 0.0
            ),
            error=res.error,
            timestamp_utc=_now_iso(),
        )
        self._telemetry.append(call)
        return call

    def run(
        self,
        *,
        patient: Patient,
        state: TriageAgentState,
        context: ToolContext,
    ) -> AgentRunResult:
        """Execute the bounded dynamic agent loop."""

        if patient.patient_id != state.patient_id:
            raise ValueError("patient_id mismatch between patient and state")

        context.patients[patient.patient_id] = patient
        context.agent_states[state.patient_id] = state

        seed_initial_observations(state)
        state.assessment_id = state.assessment_id or f"as-{patient.patient_id}"
        run_calls: list[LLMCall] = []
        last_tool_feedback: Optional[str] = None
        parse_error: Optional[str] = None
        terminal_action: Optional[str] = None
        stopped_reason = ""

        iteration = 0
        parse_retries = 0

        while iteration < self.max_iterations:
            state.iteration_count = iteration
            state.status = AgentStatus.REASONING

            prompt = build_turn_prompt(
                state=state,
                registry=self.registry,
                last_tool_feedback=last_tool_feedback,
                parse_error=parse_error,
            )
            key = cache_key_for_turn(state.patient_id, iteration, prompt)
            started = perf_counter()
            llm_res = self.llm.complete(
                system=SYSTEM_PROMPT,
                prompt=prompt,
                cache_key=key,
            )
            if llm_res.latency_ms == 0 and not llm_res.cached:
                llm_res.latency_ms = (perf_counter() - started) * 1000
            recorded = self._record(llm_res)
            run_calls.append(recorded)

            if not llm_res.ok:
                state.status = AgentStatus.FAILED
                state.last_error = llm_res.error or "LLM call failed"
                stopped_reason = "llm_failure"
                break

            parsed = parse_model_action(llm_res.text)
            if not parsed.ok:
                parse_error = parsed.error  # type: ignore[union-attr]
                parse_retries += 1
                if parse_retries > self.max_parse_retries:
                    state.status = AgentStatus.FAILED
                    state.last_error = f"invalid model actions: {parse_error}"
                    stopped_reason = "parse_failure"
                    break
                iteration += 1
                continue

            parse_error = None
            parse_retries = 0
            action = parsed.action  # type: ignore[union-attr]

            if isinstance(action, CallToolAction):
                args = normalize_tool_arguments(
                    action.tool, action.arguments, state.patient_id
                )

                if not self.registry.is_registered(action.tool):
                    last_tool_feedback = (
                        f"Rejected unknown tool '{action.tool}'. "
                        f"Use only: {', '.join(self.registry.names())}."
                    )
                    state.tool_history.append(
                        AgentToolCall(
                            tool_name=action.tool,
                            arguments=args,
                            status=ToolCallStatus.error,
                            result_summary=last_tool_feedback,
                            error=f"unknown tool: {action.tool}",
                            iteration=iteration,
                        )
                    )
                    iteration += 1
                    continue

                if is_duplicate_tool_call(state, action.tool, args):
                    last_tool_feedback = record_skipped_duplicate(
                        state, action.tool, args, iteration
                    )
                    iteration += 1
                    continue

                tool_result = apply_tool_call(
                    state=state,
                    context=context,
                    registry=self.registry,
                    action=CallToolAction(
                        action="CALL_TOOL", tool=action.tool, arguments=args
                    ),
                    iteration=iteration,
                )
                last_tool_feedback = (
                    f"ok={tool_result.ok}; {tool_result.summary}"
                    + (f" error={tool_result.error}" if tool_result.error else "")
                )
                iteration += 1
                continue

            if isinstance(action, RequestInformationAction):
                apply_request_information(state, action)
                terminal_action = "REQUEST_INFORMATION"
                stopped_reason = "request_information"
                break

            if isinstance(action, RecommendAction):
                apply_recommend(state, action)
                terminal_action = "RECOMMEND"
                stopped_reason = "recommend"
                break

            if isinstance(action, EscalateAction):
                apply_escalate(state, action)
                terminal_action = "ESCALATE"
                stopped_reason = "escalate"
                break

            state.status = AgentStatus.FAILED
            state.last_error = "unhandled action type"
            stopped_reason = "unhandled_action"
            break

        else:
            apply_budget_exhausted(state)
            terminal_action = "AWAITING_HUMAN"
            stopped_reason = "max_iterations"

        return AgentRunResult(
            state=state,
            patient_id=patient.patient_id,
            terminal_action=terminal_action,
            iterations=len(run_calls),
            llm_calls=run_calls,
            stopped_reason=stopped_reason,
        )
