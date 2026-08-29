# Sentinel final architecture (Phase 8)

An **agent-first, human-in-the-loop emergency triage decision-support system.**

It is **not** autonomous medical decision-making. The clinician remains final authority.

---

## 1. Final architecture diagram

```text
                    +------------------+
                    |  Intake / Board  |
                    +--------+---------+
                             |
                             v
                    +------------------+
                    |  Agent State     |
                    +--------+---------+
                             |
                             v
              +--------------+--------------+
              |     Gemini triage agent     |
              |  (primary recommendation)   |
              +------+--------------+-------+
                     |              |
                     v              v
              +------------+  +-------------+
              | Tool layer |  |  HITL queue  |
              +-----+------+  +------+------+
                    |                |
                    |                v
                    |         +-------------+
                    |         |  Clinician  |
                    |         +------+------+
                    |
                    v
         +----------------------+
         | Deterministic engine |
         | baseline / fallback  |
         | / evaluation only    |
         +----------------------+

Watcher (waiting room)
  OBSERVE -> DETECT -> TriageEvent
       |
       v
  Agent reassessment (meaningful events only) -> HITL
```

---

## 2. Final agent loop

```text
OBSERVE
  -> REASON
  -> SELECT ACTION
      CALL_TOOL | REQUEST_INFORMATION | RECOMMEND | ESCALATE
  -> OBSERVE RESULT
  -> UPDATE STATE
  -> REASON AGAIN

Bound: MAX_ITERATIONS = 5
Duplicate identical tool calls are skipped.
Budget exhausted -> AWAITING_HUMAN (not invent a priority).
```

---

## 3. Tool list

| Tool | Role |
|------|------|
| `get_patient` | Demographics / intake context |
| `get_latest_vitals` | Latest vitals snapshot |
| `get_vital_history` | Chronologic vitals |
| `get_patient_history` | PMH / meds / allergies |
| `calculate_shock_index` | HR/SBP numeric evidence |
| `calculate_vital_trends` | Series text (e.g. HR 96 → 110 → 122) |
| `calculate_time_since_onset` | Minutes since onset |
| `assess_data_completeness` | Missing fields (e.g. missing SBP) |
| `inspect_watcher_state` | Queue / unsafe wait state |
| `get_previous_agent_assessment` | Prior agent recommendation |
| `get_baseline_engine_assessment` | Deterministic baseline (reference) |

There is **no** `get_final_triage` / decide-ESI tool.

---

## 4. Gemini role

- Primary triage reasoner
- Chooses tools dynamically
- Forms `priority`, urgency, pathway, monitoring, confidence, evidence
- May request information or escalate
- May inspect baseline and disagree
- Never invents patient data, diagnoses, treats, or discharges
- Recommendation always subject to clinician review

---

## 5. Deterministic engine role

Kept (`interpreter`, `adjudicator`, `thresholds`, `pipeline`) as:

- **BASELINE** via `get_baseline_engine_assessment`
- **FALLBACK** when Gemini is unavailable, times out, returns invalid output, or the loop cannot complete (`decision_source = deterministic_fallback`)
- **EVALUATION** for agreement analytics

It must **not** silently overwrite a successful agent recommendation.

---

## 6. Watcher role

- Waiting-room monitoring, attention budget, deterioration simulation, unsafe waits, 3× surge
- Upward-only monitoring floor
- Emits `TriageEvent`s (observe / detect / emit)
- Does **not** own the final clinical recommendation
- Meaningful events wake agent reassessment (not every tick)
- Deterioration history is append-only and cannot be erased by a softer agent recommendation

---

## 7. HITL workflow

Every completed recommendation enters **Awaiting clinician**.

Clinician actions:

- ACCEPT
- MODIFY (reason + priority required)
- OVERRIDE (reason + priority required)
- REQUEST MORE INFORMATION (reason required)
- ESCALATE (reason required)

Agent recommendation and clinician decision are stored separately. The original agent recommendation is never overwritten.

---

## 8. Fallback architecture

```text
try Gemini agent loop
  success -> decision_source = agent
             baseline_used = true only if baseline tool called
failure / unavailable / invalid / incomplete
  -> deterministic pipeline recommendation
  -> decision_source = deterministic_fallback
  -> human_review_required = true
```

Board cohort load uses fallback unless `SENTINEL_LIVE_AGENT_BOARD=1` or an `AgentLLM` is injected (avoids N live Gemini calls on every refresh).

---

## 9. Baseline comparison

Stored on each assessment:

- `agent_priority`
- `baseline_priority`
- `clinician_priority`
- `agent_baseline_agreement`
- `agent_clinician_agreement`
- `baseline_clinician_agreement`
- optional `disagreement_reason`

Purpose: observe disagreement. Do **not** train the agent to mimic baseline as the objective.

---

## 10. Test results

Run from `sentinel/backend`:

```bash
python -m pytest -q
```

Expect all tests green (Phase 8 target: full suite including `test_phase8_integration.py`).

---

## 11. Demo instructions

```bash
cd sentinel/backend
python run_demo.py      # demos 1-4 (info gap, agent vs baseline, deterioration, surge)
python run_watch.py     # normal vs 3x surge Watcher report
python run_server.py    # open http://127.0.0.1:8000
```

In the UI:

1. Open a patient → **Agent assessment** panel (status, recommendation, evidence, gaps, baseline comparison, timeline)
2. Use HITL buttons (Modify/Override require a reason)
3. Audit trail tab for agent + clinician records
4. Watcher simulation tab for normal vs 3× surge

Optional live agent board load:

```bash
set SENTINEL_LIVE_AGENT_BOARD=1
set GEMINI_API_KEY=...
python run_server.py
```

---

## 12. Remaining limitations

- In-memory state (not multi-hospital durable storage)
- Synthetic cohort / deterioration profiles (simulation, not live EHR)
- Gemini board load is opt-in to control cost/latency
- HITL “request more information” does not yet reopen a full live Gemini loop automatically in the UI (API/state support re-entry; demo script shows the pattern)
- Not a medical device; not for clinical use without validation, governance, and jurisdiction-specific clearance
- Thresholds are configurable but not yet a full multi-site config service
- Learning / federated update loops described in Round 2 docs are not implemented in this prototype

---

**Team Sentinel · PatientTriage.ai · Accenture Innovation Challenge 2026**
