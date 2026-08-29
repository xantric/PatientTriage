from app.llm.service import LLMService
from app.agent.primary import run_primary_assessment
from app.engine.pipeline import triage

svc = LLMService(mode="ollama")
parsed, calls = svc.parse_intake("21M, no disease", "DBG-1")
print("PARSE source", parsed.source, "note", parsed.note)
print("fields", parsed.fields_found)
p = parsed.patient
print("patient age", p.age_years, "sex", p.sex, "cc", p.chief_complaint)
print("vitals", p.vitals)
print("history", p.history)

base = triage(p)
print("\nENGINE acuity", base.adjudicator.acuity, "band", base.adjudicator.confidence_band)
print("drivers", base.adjudicator.top_drivers)
print("placement", base.adjudicator.placement)
print("escalated", base.adjudicator.escalated_for_uncertainty)

# force fallback path like board off? intake uses run_primary_assessment without force
outcome = run_primary_assessment(p)  # will use ollama agent - SLOW
print("\nPRIMARY source", outcome.decision_source)
print("rec", outcome.state.current_recommendation)
print("fallback", outcome.fallback_reason)
