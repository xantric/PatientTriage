# Sentinel: Round 2 prototype plan

Team Sentinel. Track 2, PatientTriage.ai. Accenture Innovation Challenge 2026.

## Where we start

Round 1 pitched three agents (Interpreter, Adjudicator, Watcher), a priority ratchet that only escalates, a learning loop, and hospital-controlled autonomy levels. Round 2 asks us to turn that into a working prototype on simulated data, plus a business proposal and a pitch. The good news is that Track 2 gives an explicit minimum-expectations checklist, so we can treat it as the scoring rubric and make sure nothing is missed.

## What actually wins this track

The brief puts one thing in bold: bias toward escalation under uncertainty, and demonstrate that choice explicitly. So the demo should prove Sentinel is safe when it is unsure, not that it hits the best average accuracy. Every score ships with a confidence band, low confidence routes to a nurse instead of auto-clearing, the Watcher keeps re-checking the waiting room, and overrides are logged. That safety story is what separates a top team from a clever scoring script.

One design choice runs through everything: deterministic clinical logic sets the acuity so a clinician can audit it in seconds, and the LLM is used only for reading free-text symptoms and writing the plain-language explanation. The LLM never sets the number.

## Graded checklist, mapped to features

| Round 2 requirement | How Sentinel demonstrates it |
| --- | --- |
| Score 15 to 20 simulated patients | Deterministic generator makes 24 records; Interpreter plus Adjudicator score each into ESI 1 to 5 with drivers |
| Ambiguous, pediatric or geriatric, zero-history cases | Seed set includes an atypical geriatric MI, a febrile toddler, an ambiguous elderly weakness, and a walk-in with almost no data |
| Behaviour under 3x surge | Surge toggle triples arrivals; the Watcher reallocates its re-check budget and the board shows load and the longest unsafe wait |
| No score without a confidence indicator | Every agent output carries a confidence band; low confidence routes to the nurse |
| Capture a clinician override and log it | Override control on each patient; the audit log stores before, after, reason, user, timestamp, and model version |
| Waiting-room deterioration monitoring | Re-check intervals by acuity (15 min to 2 h), a timer backstop, and re-recorded vitals that can ratchet a patient up |
| Age-specific vital thresholds | Danger zones banded by infant, child, adolescent, adult, geriatric |
| Named jurisdiction and audit trail | Assume HIPAA (US): de-identified demo data, an immutable audit log, stated retention and consent |

## Architecture

Three agents, now concrete.

**Interpreter (Agent 1).** Takes messy intake (complaint, vitals, arrival mode, history), structures it, computes shock index and time since onset, maps vitals to age-banded danger zones, and scores what is missing. The LLM parses free text; rules compute the indices.

**Adjudicator (Agent 2).** Assigns ESI 1 to 5, runs the time-critical clocks (stroke 4.5 h, STEMI 90 min, sepsis 1 h), and sets a monitoring tier. It outputs placement, the top drivers, a what-if-ignored note, and a confidence band. Pure deterministic scoring, fully auditable. Danger-zone vitals for age can only push acuity up, and genuine uncertainty escalates rather than settling on a middle score.

**Watcher (Agent 3).** Watches everyone waiting, spends a limited attention budget by risk (checks the sick often, the stable rarely), and ratchets a patient up (never down) the moment vitals worsen or a safe wait is exceeded, alerting the nurse with the reason. This is the surge and deterioration story.

## What we add beyond the Round 1 deck

Safety and trust: a confidence band on every output, abstain to the nurse when low, override capture with a full audit record, and escalate-on-uncertainty made visible (we show the cases we push up precisely because we are not sure).

Realism and scale: age-banded thresholds, mixed data quality (about half of patients have prior history, half do not), config-driven staffing and thresholds so one engine flexes from a small rural ED to an urban trauma center, and the autonomy dial (L1 to L4) tied to measured accuracy in the learning loop.

## Scope, built in tiers so the demo is protected

**Must (the MVP demo).** Synthetic data, all three agents, a live queue board, the surge toggle, confidence on every score, and override plus audit log. Every graded line item lives here, so this alone is a complete and honest demo.

**Should.** LLM free-text parsing, what-if-ignored notes, age-band explanations, and a telemetry panel (latency, model calls, token usage, estimated cost). This is depth and polish, and it shows the LLM used on purpose rather than everywhere.

**Could (stretch).** Federated learning simulation, autonomy auto-unlock, a multi-hospital config switch, and a deterioration replay. These are differentiators if time allows, and they are the first things we cut if we fall behind.

## Build sequence

Phases are numbered from 0 so the numbers match how we talk about them.

- **Phase 0, Foundations.** Synthetic patient generator with named edge cases, and the age-band threshold tables. Done.
- **Phase 1, Scoring engine.** Deterministic Interpreter and Adjudicator, confidence, drivers, unit-tested against the seed cases. Done.
- **Phase 2, Watcher and surge.** The re-check scheduler, ratchet logic, timer backstop, and 3x surge mode. Done.
- **Phase 3, Interface.** The live triage board, patient drill-down, override flow, and audit-log view. Done.
- **Phase 4, LLM and telemetry.** Free-text parsing, plain-language explanations, and the runtime telemetry panel. Done. Gemini is optional and off by default; with no key the app runs a rule-based parser and a template explainer, so nothing breaks offline. The model never sets an acuity, calls run at temperature 0 with a cache and a timeout, and a generated explanation that names the wrong ESI level is rejected in favour of the template.
- **Phase 5, Proposal and pitch.** The business proposal (users, impact, roadmap, risks) and a tight pitch tied to the live demo. Done. The proposal is in `docs/business-proposal.md` and the pitch script is in `docs/pitch.md`. The slide deck is generated from the pitch script.

## Calibration notes worth defending out loud

Two thresholds were wrong in the first cut, and both were caught by looking at
the board rather than the tests. They are worth mentioning in the pitch because
they show the age-banding is real rather than decorative.

A flat critical shock-index cutoff of 1.3 is an adult number. Children run fast
heart rates against lower pressures, so a healthy toddler can sit near 1.7 and a
flat cutoff called our febrile three-year-old an ESI 1. The critical cutoff is
now age-banded on the same SIPA logic as the elevated one, and that patient
lands at ESI 2, which is both safe and defensible.

A low respiratory rate on its own was triggering an ESI-1 resuscitation call. A
six-year-old, alert, saturating at 92%, with a rate of 15 is abnormal but is not
an airway emergency. Bradypnea now only counts as an immediate life threat when
the patient is also near-apnoeic, not fully alert, or severely hypoxic;
otherwise it is a danger-zone vital that pushes to ESI 2.

Both fixes cut the ESI-1 rate on the 25-patient board from 4 to 2 without
introducing a single under-triage. That matters: over-triage is the safe
direction, but a system that calls everything critical has not prioritised
anything, and a judge will say so.

Background patients are also now generated against their own age band rather
than adult defaults, so the cohort no longer contains six-year-olds with adult
blood pressures.

Bringing the live Gemini path up surfaced two more fixes. Gemini rejects any
call deadline under ten seconds, so the timeout now clamps up to that floor
whatever the environment asks for. And the parser was throwing away a good
extraction whenever the model wrote one field loosely, like "about 40" for an
onset, so the JSON reader now coerces field by field and drops only the bad
value. With those in, parse and explain both run live against
gemini-2.5/3.5-flash-lite with zero fallbacks on the demo notes, each call
around one to two seconds and a fraction of a cent.

## Risks and how we defuse them

Looking like a black box: keep scoring deterministic and show the drivers and the what-if note on every card. The LLM only explains and parses.

Fake medical authority: base thresholds on recognised triage frameworks (ESI 5-level), cite them, and state assumptions clearly. Position the tool as decision support, never diagnosis.

Over-scoping the build: freeze the Must tier early. Stretch items are additive and get cut first. A complete small demo beats a broken big one.

Demo fragility on stage: seeded, deterministic data and a scripted surge so the same story plays every run, plus a recorded backup video.

## Assumptions

Illustrative data, assumed jurisdiction HIPAA (US). Thresholds are adapted from standard references and are configurable. This is a prototype for a competition, not clinical advice.
