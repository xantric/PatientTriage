"""Read-only context shared by agent tools.

Tools may read patients and TriageAgentState from here. They must not mutate
agent state as a side effect of execution; the orchestrator owns writes later.
"""

from __future__ import annotations

from typing import Optional

from app.domain.models import Patient, TriageAgentState


class ToolContext:
    """In-memory lookup surface for Phase 3 tools."""

    def __init__(
        self,
        *,
        patients: Optional[dict[str, Patient]] = None,
        agent_states: Optional[dict[str, TriageAgentState]] = None,
        assessments_by_patient: Optional[dict[str, list[TriageAgentState]]] = None,
    ) -> None:
        self.patients: dict[str, Patient] = dict(patients or {})
        self.agent_states: dict[str, TriageAgentState] = dict(agent_states or {})
        # Prior completed/in-flight states newest-last, for get_previous_agent_assessment.
        self.assessments_by_patient: dict[str, list[TriageAgentState]] = {
            k: list(v) for k, v in (assessments_by_patient or {}).items()
        }

    def get_patient(self, patient_id: str) -> Patient:
        if patient_id not in self.patients:
            raise KeyError(f"unknown patient {patient_id}")
        return self.patients[patient_id]

    def get_agent_state(self, patient_id: str) -> TriageAgentState:
        if patient_id not in self.agent_states:
            raise KeyError(f"no agent state for patient {patient_id}")
        return self.agent_states[patient_id]

    def prior_assessments(self, patient_id: str) -> list[TriageAgentState]:
        return list(self.assessments_by_patient.get(patient_id, []))
