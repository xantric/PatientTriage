"""Phase 7: agent owns primary recommendation; engine is baseline/fallback/eval."""

from __future__ import annotations

import json

import pytest

from app.agent.context import ToolContext
from app.agent.orchestrator import MockAgentLLM
from app.agent.primary import run_primary_assessment
from app.agent.registry import reset_default_registry
from app.data.generator import build_cohort
from app.domain.enums import DecisionSource
from app.engine.pipeline import triage as baseline_triage
from app.models import OverrideRequest, Patient, Vitals
from app.state import DEPARTMENT, Department


@pytest.fixture(autouse=True)
def _reset():
    reset_default_registry()
    yield
    reset_default_registry()


def _action(obj: dict) -> str:
    return json.dumps(obj)


def _recommend(priority: int, evidence: list[str] | None = None) -> str:
    return _action(
        {
            "action": "RECOMMEND",
            "recommendation": {
                "priority": priority,
                "urgency": "high",
                "care_pathway": "agent pathway",
                "monitoring_plan": "agent monitoring",
                "confidence": 0.85,
                "reason_summary": "Independent agent recommendation.",
                "key_evidence": evidence or ["agent evidence"],
            },
        }
    )


def test_pipeline_module_documents_baseline_role():
    import app.engine.pipeline as pipe

    assert "BASELINE" in pipe.__doc__
    assert "FALLBACK" in pipe.__doc__
    assert "EVALUATION" in pipe.__doc__


def test_forced_fallback_uses_deterministic_source():
    patient = build_cohort(surge_factor=1)[0]
    outcome = run_primary_assessment(patient, force_fallback=True)
    assert outcome.decision_source == DecisionSource.deterministic_fallback
    assert outcome.state.decision_source == DecisionSource.deterministic_fallback
    assert outcome.state.current_recommendation is not None
    assert outcome.state.baseline_assessment is not None
    assert (
        outcome.state.current_recommendation.priority
        == outcome.baseline_result.adjudicator.acuity
    )
    assert outcome.state.baseline_used is False
    ev = outcome.state.evaluation
    assert ev.agent_priority == ev.baseline_priority
    assert ev.agent_baseline_agreement is True


def test_llm_failure_falls_back_to_deterministic():
    patient = build_cohort(surge_factor=1)[0]
    mock = MockAgentLLM(["unused"], fail_after=0)
    outcome = run_primary_assessment(patient, llm=mock)
    assert outcome.decision_source == DecisionSource.deterministic_fallback
    assert outcome.fallback_reason
    assert outcome.state.current_recommendation.priority == (
        outcome.baseline_result.adjudicator.acuity
    )


def test_successful_agent_is_primary_not_forced_to_baseline():
    patient = build_cohort(surge_factor=1)[0]
    baseline = baseline_triage(patient)
    # Pick a priority different from baseline when possible.
    agent_priority = 1 if baseline.adjudicator.acuity != 1 else 2
    mock = MockAgentLLM([_recommend(agent_priority)])
    outcome = run_primary_assessment(patient, llm=mock)
    assert outcome.decision_source == DecisionSource.agent
    assert outcome.state.decision_source == DecisionSource.agent
    assert outcome.state.current_recommendation.priority == agent_priority
    assert outcome.state.baseline_assessment.priority == baseline.adjudicator.acuity
    # Must not silently overwrite agent rec with adjudicator.
    assert outcome.state.current_recommendation.priority != baseline.adjudicator.acuity or (
        agent_priority == baseline.adjudicator.acuity
    )
    assert outcome.state.evaluation.agent_priority == agent_priority
    assert outcome.state.evaluation.baseline_priority == baseline.adjudicator.acuity
    if agent_priority != baseline.adjudicator.acuity:
        assert outcome.state.evaluation.agent_baseline_agreement is False
        assert outcome.state.baseline_comparison is not None
        assert outcome.state.baseline_comparison.agrees is False


def test_agent_baseline_tool_sets_baseline_used():
    patient = build_cohort(surge_factor=1)[0]
    mock = MockAgentLLM(
        [
            _action(
                {
                    "action": "CALL_TOOL",
                    "tool": "get_baseline_engine_assessment",
                    "arguments": {"patient_id": patient.patient_id},
                }
            ),
            _recommend(2),
        ]
    )
    outcome = run_primary_assessment(patient, llm=mock)
    assert outcome.decision_source == DecisionSource.agent
    assert outcome.state.baseline_used is True
    assert outcome.state.baseline_assessment is not None
    # Agent still owns recommendation (may equal baseline by chance).
    assert outcome.state.current_recommendation.priority == 2


def test_invalid_agent_output_falls_back():
    patient = build_cohort(surge_factor=1)[0]
    mock = MockAgentLLM(["not-json", "still-bad", '{"action":"NOPE"}'])
    outcome = run_primary_assessment(patient, llm=mock)
    assert outcome.decision_source == DecisionSource.deterministic_fallback
    assert outcome.fallback_reason
    assert outcome.state.current_recommendation.priority == (
        outcome.baseline_result.adjudicator.acuity
    )


def test_department_board_exposes_decision_source_and_evaluation():
    DEPARTMENT.agent_llm = None
    DEPARTMENT.force_fallback = False
    DEPARTMENT.load(surge_factor=1)
    board = DEPARTMENT.board()
    assert board.summary.fallback_decisions == board.summary.total
    row = board.rows[0]
    assert row.decision_source == "deterministic_fallback"
    assert row.baseline_priority == row.engine_acuity
    assert row.agent_priority is not None
    pid = row.patient_id
    DEPARTMENT.apply_override(
        OverrideRequest(
            patient_id=pid,
            new_acuity=2,
            reason="clinical reassessment after exam",
            actor="tester",
        )
    )
    detail = DEPARTMENT.detail(pid)
    assert detail.row.engine_acuity == row.engine_acuity
    assert detail.row.acuity == 2
    assert detail.row.clinician_priority == 2
    assert detail.evaluation is not None
    assert detail.evaluation["baseline_clinician_agreement"] is not None
    DEPARTMENT.load(surge_factor=1)


def test_department_with_mock_agent_marks_agent_source():
    patient = next(p for p in build_cohort(surge_factor=1) if p.patient_id == "P-009")
    baseline_p = baseline_triage(patient).adjudicator.acuity
    agent_p = 1 if baseline_p != 1 else 5
    mock = MockAgentLLM([_recommend(agent_p)])

    dept = Department(surge_factor=1, force_fallback=True)
    dept.patients = {}
    dept.results = {}
    dept.agent_states = {}
    dept.decision_sources = {}
    dept.overrides = {}
    dept.agent_llm = mock
    dept.force_fallback = False
    dept._register_assessed(patient)

    row = dept.row(patient.patient_id)
    assert row.decision_source == "agent"
    assert row.agent_priority == agent_p
    assert row.baseline_priority == baseline_p
    assert row.acuity == agent_p
    assert row.engine_acuity == baseline_p
    if agent_p != baseline_p:
        assert row.agent_baseline_agreement is False
