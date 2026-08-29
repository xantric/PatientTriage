"""Append-only audit trail for clinician and agent decisions.

Two rules make this an audit trail rather than a log file: records can only be
appended, never edited or deleted, and an override/HITL write cannot omit a
reason when the action requires one. Readers get copies.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone
from typing import Any, Optional

from app import __version__ as ENGINE_VERSION
from app.models import AuditRecord


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def direction_for(before: Optional[int], after: Optional[int]) -> Optional[str]:
    """Name the direction of a change. Lower acuity number = more urgent."""

    if before is None or after is None:
        return None
    if after < before:
        return "escalate"
    if after > before:
        return "de-escalate"
    return "unchanged"


class AuditLog:
    def __init__(self) -> None:
        self._records: list[AuditRecord] = []
        self._seq = itertools.count(1)

    def append(
        self,
        *,
        patient_id: str,
        action: str,
        actor: str,
        actor_role: str,
        reason: str,
        before_acuity: Optional[int] = None,
        after_acuity: Optional[int] = None,
        engine_confidence: Optional[float] = None,
        engine_drivers: Optional[list[str]] = None,
        assessment_id: Optional[str] = None,
        event_id: Optional[str] = None,
        agent_model: Optional[str] = None,
        agent_version: Optional[str] = None,
        agent_action: Optional[str] = None,
        tools_used: Optional[list[str]] = None,
        iterations: Optional[int] = None,
        agent_priority: Optional[int] = None,
        agent_confidence: Optional[float] = None,
        baseline_priority: Optional[int] = None,
        agreement: Optional[bool] = None,
        clinician_action: Optional[str] = None,
        clinician_reason: Optional[str] = None,
        final_priority: Optional[int] = None,
    ) -> AuditRecord:
        if not reason or not reason.strip():
            raise ValueError("an audit record cannot be written without a reason")
        record = AuditRecord(
            record_id=next(self._seq),
            timestamp_utc=_now_iso(),
            patient_id=patient_id,
            action=action,
            actor=actor,
            actor_role=actor_role,
            reason=reason.strip(),
            model_version=ENGINE_VERSION,
            before_acuity=before_acuity,
            after_acuity=after_acuity,
            direction=direction_for(before_acuity, after_acuity),
            engine_confidence=engine_confidence,
            engine_drivers=list(engine_drivers or []),
            assessment_id=assessment_id,
            event_id=event_id,
            agent_model=agent_model,
            agent_version=agent_version or ENGINE_VERSION,
            agent_action=agent_action,
            tools_used=list(tools_used or []),
            iterations=iterations,
            agent_priority=agent_priority,
            agent_confidence=agent_confidence,
            baseline_priority=baseline_priority,
            agreement=agreement,
            clinician_action=clinician_action,
            clinician_reason=clinician_reason,
            final_priority=final_priority,
        )
        self._records.append(record)
        return record

    def all(self) -> list[AuditRecord]:
        return [r.model_copy(deep=True) for r in self._records]

    def for_patient(self, patient_id: str) -> list[AuditRecord]:
        return [
            r.model_copy(deep=True)
            for r in self._records
            if r.patient_id == patient_id
        ]

    def __len__(self) -> int:
        return len(self._records)
