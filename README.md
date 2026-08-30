# Sentinel (PatientTriage.ai)

Team Sentinel. Accenture Innovation Challenge 2026. Track 2, Round 2.

Sentinel is an agent-first, human-in-the-loop triage helper for the emergency department. It recommends urgency and placement. It does not diagnose, treat, or replace the clinician.

## Live demo

Follow the steps below to run it on your own machine. Free Render may take 30 to 90 seconds on the first open after idle. The live path uses Gemini when configured; otherwise the deterministic engine is the fallback.

<!-- ## Demo video -->

<!-- Demo video: add the public link here before submission. -->

## Implementation approach

1. Bias toward safety when unsure. Missing a critical patient is worse than over-calling a minor one. Low confidence goes to a nurse. Thin notes can ask for more information instead of forcing a firm score.
2. Hybrid model. A Gemini agent is the main reasoner. It uses tools, proposes ESI 1 to 5, and can request info or escalate. A deterministic engine stays as baseline, fallback, and evaluation so the app still runs with no API key.
3. Clinician always decides. Accept, modify, override, request more information, or escalate. Modify and override need a reason. Both agent and clinician choices are kept for audit.
4. Keep watching the waiting room. The Watcher re-checks on unsafe waits or worsening vitals, and only escalates. Demo covers normal load and 3x surge.
5. Simulated data only. About 25 fixed-seed patients, including ambiguous, pediatric, geriatric, zero-history, and one deterioration case (P-011). Real patient data can be added via the dashboard.
6. One process for the demo. FastAPI serves the API and a simple HTML/CSS/JS board. No frontend build step.

Assumed jurisdiction: HIPAA (United States). Thresholds are illustrative, not clinical advice.

More detail: [docs/final-architecture.md](docs/final-architecture.md) and [docs/business-proposal.md](docs/business-proposal.md).

## Solution architecture

```text
Intake / board
    -> agent state
    -> Gemini agent + tools
    -> recommendation
    -> clinician HITL

Deterministic engine: baseline / fallback / evaluation
Watcher: waiting-room events -> reassessment when needed
```

| Part | What it does | Main path |
| --- | --- | --- |
| UI | Board, patient detail, intake, audit, Watcher sim | `backend/app/static/` |
| API | Board, patients, intake, HITL, audit, simulation | `backend/app/api.py` |
| Agent | Tool use, recommend, request info, escalate | `backend/app/agent/` |
| Engine | Age-banded vitals, ESI score, clocks, confidence | `backend/app/engine/` |
| Watcher | Surge, deterioration, unsafe-wait alerts | `backend/app/agent/event_watcher.py`, `backend/app/engine/watcher.py` |
| Audit | Append-only log with required reasons | `backend/app/audit.py` |
| LLM | Optional Gemini; rule-based stub if offline | `backend/app/llm/` |

### Files in this repository

```text
README.md
docs/
  business-proposal.md
  final-architecture.md
  Sentinel_Pitch.pptx
backend/
  app/                 # API, agent, engine, LLM, UI
  tests/
  requirements.txt
  .env.example
  run_server.py
  run_demo.py
  run_watch.py
```

### Track 2 checklist

| Requirement | Where to see it |
| --- | --- |
| 15 to 20+ simulated patients | Board at `/`, or `python run_demo.py` |
| Ambiguous, pediatric/geriatric, zero-history | P-005, P-003, P-002, P-004 |
| 3x surge | Surge toggle, Watcher tab, `python run_watch.py` |
| Confidence on every score | Board column and patient detail |
| Clinician override and log | HITL form and Audit trail tab |
| Waiting-room deterioration | Watcher sim, patient P-011 |
| Age-specific vitals | `backend/app/engine/thresholds.py` |
| Named jurisdiction and audit | HIPAA (US), `backend/app/audit.py` |

## Dependencies

Required:

- Python 3.11 or newer
- Packages in [backend/requirements.txt](backend/requirements.txt): FastAPI, Uvicorn, Pydantic, httpx, python-dateutil, pytest
- Optional LLM packages in the same file: `google-genai`, `python-dotenv`

Optional(Highly Recommended):

- Gemini API key from [Google AI Studio](https://aistudio.google.com)

Without a key, the prototype still runs using the rule-based parser and deterministic fallback.

Copy [backend/.env.example](backend/.env.example) to `backend/.env` for optional LLM settings. Do not commit `.env`.

## How to run

Install:

```powershell
cd backend
python -m pip install -r requirements.txt
```

On macOS or Linux, use `python3` instead of `python`.

Start the demo:

```powershell
python run_server.py
```

Open http://127.0.0.1:8000  
API docs: http://127.0.0.1:8000/docs

Optional LLM setup:

```powershell
copy .env.example .env
```

Edit `.env`, then restart `python run_server.py`.

Other commands:

```powershell
python run_demo.py      # terminal board
python run_watch.py     # normal vs 3x surge Watcher report
python -m pytest -q     # tests
```

Quick walkthrough:

1. Open the board. The most urgent patient opens on the right.
2. Check confidence and age-banded vitals. Open P-004 for zero history.
3. Switch to 3x surge. Open Watcher simulation and find P-011.
4. Record an override with a reason. Open Audit trail.
5. Optional: add a patient from a note, parse and score, then add to the board.

## Other deliverables

| Item | Location |
| --- | --- |
| Working prototype | `backend/` |
| This README | `README.md` |
| Business proposal | [docs/business-proposal.md](docs/business-proposal.md) |
| Pitch deck | [docs/Sentinel_Pitch.pptx](docs/Sentinel_Pitch.pptx) |
| Architecture notes | [docs/final-architecture.md](docs/final-architecture.md) |
| Demo video | Link in the Demo video section above |

## Disclaimer

This is a competition prototype on simulated data. Not for clinical use. Not a medical device.
