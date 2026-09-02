"""SQLite cache for completed Gemini primary assessments.

Stores serialized agent outcomes so the waiting-room board can reload without
re-running the full agent loop for every seeded patient. Turn-level LLM
responses remain in seed_cache.json; this layer caches the assembled result.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app import __version__ as ENGINE_VERSION
from app.agent.primary import PrimaryAssessmentResult
from app.data.generator import COHORT_SEED
from app.domain.enums import DecisionSource
from app.domain.models import TriageAgentState
from app.llm.config import BACKEND_DIR, model_name
from app.models import TriageResult

_DEFAULT_DB = BACKEND_DIR / "app" / "data" / "agent_assessments.db"


def _enabled() -> bool:
    return (os.getenv("SENTINEL_ASSESSMENT_CACHE") or "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _db_path() -> Path:
    raw = (os.getenv("SENTINEL_ASSESSMENT_CACHE_DB") or "").strip()
    return Path(raw) if raw else _DEFAULT_DB


class AssessmentCache:
    """Read/write cache for PrimaryAssessmentResult rows."""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._override_path = path
        self._lock = threading.Lock()
        self._schema_paths: set[str] = set()

    def enabled(self) -> bool:
        return _enabled()

    def _path(self) -> Path:
        return self._override_path or _db_path()

    def make_key(self, patient_id: str, surge_factor: int) -> str:
        return (
            f"{patient_id}|{surge_factor}|{ENGINE_VERSION}|{COHORT_SEED}|{model_name()}"
        )

    def get(self, cache_key: str) -> Optional[PrimaryAssessmentResult]:
        if not self.enabled():
            return None
        self._ensure_schema()
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT payload_json FROM agent_assessment_cache WHERE cache_key = ?",
                (cache_key,),
            ).fetchone()
            conn.close()
        if row is None:
            return None
        try:
            return _deserialize(json.loads(row[0]))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            self.delete(cache_key)
            return None

    def put(self, cache_key: str, outcome: PrimaryAssessmentResult) -> None:
        if not self.enabled():
            return
        if outcome.decision_source != DecisionSource.agent:
            return
        payload = json.dumps(_serialize(outcome))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._ensure_schema()
        with self._lock:
            conn = self._connect()
            conn.execute(
                """
                INSERT INTO agent_assessment_cache (
                    cache_key, patient_id, surge_factor, engine_version, model,
                    decision_source, created_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    decision_source = excluded.decision_source,
                    created_at = excluded.created_at,
                    payload_json = excluded.payload_json
                """,
                (
                    cache_key,
                    outcome.state.patient_id,
                    _surge_from_key(cache_key),
                    ENGINE_VERSION,
                    model_name(),
                    outcome.decision_source.value,
                    now,
                    payload,
                ),
            )
            conn.commit()
            conn.close()

    def delete(self, cache_key: str) -> None:
        self._ensure_schema()
        with self._lock:
            conn = self._connect()
            conn.execute(
                "DELETE FROM agent_assessment_cache WHERE cache_key = ?",
                (cache_key,),
            )
            conn.commit()
            conn.close()

    def count(self) -> int:
        self._ensure_schema()
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT COUNT(*) FROM agent_assessment_cache"
            ).fetchone()
            conn.close()
        return int(row[0]) if row else 0

    def count_by_surge(self) -> dict[int, int]:
        self._ensure_schema()
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT surge_factor, COUNT(*) FROM agent_assessment_cache "
                "GROUP BY surge_factor ORDER BY surge_factor"
            ).fetchall()
            conn.close()
        return {int(sf): int(n) for sf, n in rows}

    def _connect(self) -> sqlite3.Connection:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _ensure_schema(self) -> None:
        path = str(self._path())
        if path in self._schema_paths:
            return
        with self._lock:
            if path in self._schema_paths:
                return
            conn = self._connect()
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_assessment_cache (
                    cache_key       TEXT PRIMARY KEY,
                    patient_id      TEXT NOT NULL,
                    surge_factor    INTEGER NOT NULL,
                    engine_version  TEXT NOT NULL,
                    model           TEXT NOT NULL,
                    decision_source TEXT NOT NULL,
                    created_at      TEXT NOT NULL,
                    payload_json    TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_cache_patient "
                "ON agent_assessment_cache (patient_id, surge_factor)"
            )
            conn.commit()
            conn.close()
            self._schema_paths.add(path)


def _surge_from_key(cache_key: str) -> int:
    parts = cache_key.split("|")
    if len(parts) >= 2 and parts[1].isdigit():
        return int(parts[1])
    return 1


def _serialize(outcome: PrimaryAssessmentResult) -> dict:
    return {
        "state": outcome.state.model_dump(mode="json"),
        "baseline_result": outcome.baseline_result.model_dump(mode="json"),
        "decision_source": outcome.decision_source.value,
        "fallback_reason": outcome.fallback_reason,
    }


def _deserialize(data: dict) -> PrimaryAssessmentResult:
    return PrimaryAssessmentResult(
        state=TriageAgentState.model_validate(data["state"]),
        baseline_result=TriageResult.model_validate(data["baseline_result"]),
        decision_source=DecisionSource(data["decision_source"]),
        fallback_reason=data.get("fallback_reason"),
    )


assessment_cache = AssessmentCache()
