"""Event-driven agent reassessment after meaningful Watcher events.

Does not run an LLM call on every simulation tick. Only meaningful TriageEvents
wake the Gemini agent. Agent recommendations never erase Watcher history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol

from app.agent.context import ToolContext
from app.agent.events import apply_triage_event, raise_monitoring_floor, should_trigger_reassessment
from app.agent.orchestrator import AgentLLM, AgentRunResult, TriageAgentOrchestrator
from app.domain.enums import AgentStatus
from app.domain.models import Patient, TriageAgentState, TriageEvent


class ReassessmentCallback(Protocol):
    def __call__(
        self,
        *,
        patient: Patient,
        state: TriageAgentState,
        context: ToolContext,
        trigger: TriageEvent,
    ) -> AgentRunResult: ...


@dataclass
class ReassessmentRecord:
    trigger_event_id: str
    event_type: str
    patient_id: str
    time_min: int
    llm_invoked: bool
    result: Optional[AgentRunResult] = None


@dataclass
class ReassessmentBus:
    """Applies events, optionally invokes the agent, preserves Watcher history."""

    llm: Optional[AgentLLM] = None
    orchestrator: Optional[TriageAgentOrchestrator] = None
    invoke_on_arrival: bool = False
    records: list[ReassessmentRecord] = field(default_factory=list)
    llm_invocations: int = 0

    def _orch(self) -> TriageAgentOrchestrator:
        if self.orchestrator is not None:
            return self.orchestrator
        if self.llm is None:
            raise RuntimeError("ReassessmentBus needs llm or orchestrator")
        self.orchestrator = TriageAgentOrchestrator(self.llm)
        return self.orchestrator

    def handle(
        self,
        *,
        event: TriageEvent,
        patient: Patient,
        state: TriageAgentState,
        context: ToolContext,
    ) -> Optional[AgentRunResult]:
        # Snapshot deterioration history before any agent work.
        prior_deterioration = [
            e.model_copy(deep=True) for e in state.deterioration_events()
        ]
        prior_ids = {e.event_id for e in prior_deterioration}

        apply_triage_event(state, event)

        wake = should_trigger_reassessment(event)
        if event.event_type.value == "PATIENT_ARRIVAL" and not self.invoke_on_arrival:
            wake = False
        # Stable NEW_VITALS without trigger flag: observe only.
        if event.event_type.value == "NEW_VITALS" and not event.triggers_reassessment:
            wake = False

        if not wake or self.llm is None and self.orchestrator is None:
            self.records.append(
                ReassessmentRecord(
                    trigger_event_id=event.event_id,
                    event_type=event.event_type.value,
                    patient_id=event.patient_id,
                    time_min=event.time_min,
                    llm_invoked=False,
                )
            )
            return None

        # Agent may revise recommendation; Watcher floor only rises.
        self.llm_invocations += 1
        result = self._orch().run(patient=patient, state=state, context=context)
        self.records.append(
            ReassessmentRecord(
                trigger_event_id=event.event_id,
                event_type=event.event_type.value,
                patient_id=event.patient_id,
                time_min=event.time_min,
                llm_invoked=True,
                result=result,
            )
        )

        if result.state.current_recommendation is not None:
            # Soft agent priority must not lower the monitoring floor.
            raise_monitoring_floor(
                state, result.state.current_recommendation.priority
            )
            if result.state.status == AgentStatus.AWAITING_HUMAN:
                state.human_review_required = True

        # Hard invariant: deterioration events still present and unchanged.
        after = state.deterioration_events()
        after_ids = {e.event_id for e in after}
        if not prior_ids.issubset(after_ids):
            raise RuntimeError(
                "agent reassessment erased Watcher deterioration history"
            )
        for old in prior_deterioration:
            match = next(e for e in after if e.event_id == old.event_id)
            if match.model_dump() != old.model_dump():
                raise RuntimeError(
                    "agent reassessment mutated a DETERIORATION_DETECTED event"
                )

        return result
