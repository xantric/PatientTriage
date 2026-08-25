"""Append-only audit trail for clinician overrides.

Two rules make this an audit trail rather than a log file: records can only be
appended, never edited or deleted, and an override cannot be written without a
reason. Readers get copies, so a caller cannot reach in and rewrite history.

This prototype holds records in memory. A deployment would append to
write-once storage (an append-only table or object store with a retention
lock) to satisfy the HIPAA-style trail we assume in the plan.
"""

from __future__ import annotations

import itertools
from datetime import datetime, timezone
from typing import Optional

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
        )
        self._records.append(record)
        return record

    def all(self) -> list[AuditRecord]:
        """Newest last. Returns copies so callers cannot mutate the trail."""

        return [r.model_copy(deep=True) for r in self._records]

    def for_patient(self, patient_id: str) -> list[AuditRecord]:
        return [r.model_copy(deep=True) for r in self._records if r.patient_id == patient_id]

    def __len__(self) -> int:
        return len(self._records)
