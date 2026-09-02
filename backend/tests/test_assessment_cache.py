"""SQLite cache for primary Gemini assessments."""

from __future__ import annotations

import json

import pytest

from app.agent.orchestrator import MockAgentLLM
from app.agent.primary import run_primary_assessment
from app.cache.assessment_cache import AssessmentCache
from app.data.generator import build_cohort
from app.domain.enums import DecisionSource
from app.domain.models import TriageAgentState
from app.models import TriageResult


def _recommend(priority: int) -> str:
    return json.dumps(
        {
            "action": "RECOMMEND",
            "recommendation": {
                "priority": priority,
                "urgency": "high",
                "care_pathway": "cached pathway",
                "monitoring_plan": "cached monitoring",
                "confidence": 0.9,
                "reason_summary": "Cached agent recommendation.",
                "key_evidence": ["cached evidence"],
            },
        }
    )


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_ASSESSMENT_CACHE", "1")
    store = AssessmentCache(path=tmp_path / "agent_assessments.db")
    return store


def test_cache_round_trip(cache):
    patient = build_cohort(surge_factor=1)[0]
    mock = MockAgentLLM([_recommend(2)])
    outcome = run_primary_assessment(patient, llm=mock)
    assert outcome.decision_source == DecisionSource.agent

    key = cache.make_key(patient.patient_id, 1)
    cache.put(key, outcome)
    restored = cache.get(key)

    assert restored is not None
    assert restored.decision_source == DecisionSource.agent
    assert restored.state.patient_id == patient.patient_id
    assert restored.state.current_recommendation.priority == 2
    assert restored.baseline_result.adjudicator.acuity == outcome.baseline_result.adjudicator.acuity


def test_cache_skips_fallback_results(cache):
    patient = build_cohort(surge_factor=1)[0]
    outcome = run_primary_assessment(patient, force_fallback=True)
    assert outcome.decision_source == DecisionSource.deterministic_fallback

    key = cache.make_key(patient.patient_id, 1)
    cache.put(key, outcome)
    assert cache.get(key) is None
    assert cache.count() == 0


def test_department_uses_cached_assessment(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_ASSESSMENT_CACHE", "1")
    db_path = tmp_path / "board_cache.db"
    monkeypatch.setenv("SENTINEL_ASSESSMENT_CACHE_DB", str(db_path))

    from app.cache.assessment_cache import AssessmentCache
    from app.state import Department

    patient = next(p for p in build_cohort(surge_factor=1) if p.patient_id == "P-009")
    store = AssessmentCache(path=db_path)
    mock = MockAgentLLM([_recommend(1)])
    outcome = run_primary_assessment(patient, llm=mock)
    store.put(store.make_key(patient.patient_id, 1), outcome)

    dept = Department(surge_factor=1, force_fallback=False)
    row = dept.row(patient.patient_id)
    assert row.decision_source == "agent"
    assert row.agent_priority == 1


def test_surge_three_inherits_surge_one_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_ASSESSMENT_CACHE", "1")
    db_path = tmp_path / "surge_cache.db"
    monkeypatch.setenv("SENTINEL_ASSESSMENT_CACHE_DB", str(db_path))

    from app.cache.assessment_cache import AssessmentCache
    from app.state import Department

    patient = next(p for p in build_cohort(surge_factor=1) if p.patient_id == "P-009")
    store = AssessmentCache(path=db_path)
    mock = MockAgentLLM([_recommend(2)])
    outcome = run_primary_assessment(patient, llm=mock)
    store.put(store.make_key(patient.patient_id, 1), outcome)

    dept = Department(surge_factor=3, force_fallback=False)
    row = dept.row(patient.patient_id)
    assert row.decision_source == "agent"
    assert row.agent_priority == 2
    assert store.get(store.make_key(patient.patient_id, 3)) is not None
