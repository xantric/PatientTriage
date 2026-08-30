# Sentinel: Business Proposal

**Team Sentinel · Track 2: PatientTriage.ai · Accenture Innovation Challenge 2026, Round 2**

This proposal covers what Round 2 asks for: problem framing, solution design, target users, business case and impact, a phased roadmap, and key risks with mitigations. It also answers the Track 2 solutioning areas (data strategy, decision model, workflow, safety-first design, adoption, data protection, scalability) against a working prototype in this repository. Every clinical claim below is decision support under clinician authority, not autonomous medical care.

---

## 1. Problem framing

Emergency triage is a two-minute decision made with bad information while more patients keep arriving. A nurse must assign urgency, place the patient, and move on. The Round 2 brief is right that this is not a clean ranking problem.

**Age changes the meaning of every number.** A heart rate of 150 is an emergency in a calm adult and roughly normal in a distressed toddler. A fever of 38.5°C is read differently in a three-year-old than in a seventy-five-year-old. A single adult-calibrated scorer applied to everyone creates silent safety risk: nothing on the screen says the rule was wrong for this body.

**Symptoms overlap and under-report.** Ambiguous weakness in an older adult can be sepsis, a silent heart attack, or something benign. Patients minimize pain. Presentation does not map cleanly onto a five-level scale, yet a number still has to be chosen.

**Data at intake is uneven.** About half of arrivals have useful history on file and half are effectively strangers. A system that assumes rich records fails worst on the people it knows least, which is the wrong failure mode for a safety tool.

**Under-triage and over-triage are not equal.** Over-triage wastes some capacity. Under-triage can kill. Stroke, STEMI, and sepsis have hard treatment windows measured in minutes. Any solution that optimizes for average accuracy will, under uncertainty, quietly settle in the middle. The brief requires the opposite: **bias toward escalation when unsure, and show that choice in the prototype.**

**Triage does not end at the desk.** People sit in the waiting room and deteriorate while the department is busiest. A score-once tool forgets them. The brief explicitly requires ongoing monitoring of the queue, with re-assessment when safe wait is exceeded or vitals worsen.

**Trust and liability are binding constraints.** Staff are fatigued. A tool that cries wolf gets ignored. A tool that acts like an oracle gets overridden and distrusted. Clinical accountability means every recommendation must stay reviewable and overridable, with an audit trail that meets health-data regulation.

So the real problem is not "predict ESI accurately." It is: **be useful and safe under time pressure and sparse data, keep watching the people who are waiting, leave the final call with a clinician, and earn a fatigued nurse's trust.**

---

## 2. Solution design

### 2.1 What Sentinel is

Sentinel is an **agent-first, human-in-the-loop emergency triage decision-support system**. It recommends priority and placement; it does not diagnose, treat, discharge, or replace clinical judgment.

Primary path:

> Intake → agent state → Gemini triage agent (tools) → recommendation → clinician HITL

A deterministic clinical engine (Interpreter + Adjudicator with age-banded thresholds) stays in the loop as **baseline, fallback, and evaluation**, not as a silent override of a successful agent recommendation. A **Watcher** observes the waiting room, emits events, and wakes reassessment when something meaningful changes. It never owns the final clinical call.

### 2.2 Data strategy

We design for incomplete intake, not for the average chart.

**Inputs we use when present:** age, sex, arrival mode, chief complaint (free text or structured), pain score, responsiveness (AVPU), vitals (HR, RR, SpO2, BP, temperature), onset time, history, medications, allergies, and whether a prior record exists.

**How we weigh them:** life-threat and age-banded danger-zone vitals dominate. Time-critical syndromes (stroke, STEMI, sepsis) open running clocks. Resource need and complaint cues refine mid/low acuity. Missing fields are first-class: completeness feeds confidence, and sparse notes (for example mild skin complaints without vitals) trigger an explicit **needs more information** path instead of a firm high-acuity number.

**Mixed data availability:** the synthetic cohort is built so roughly half of patients have prior history and half do not, matching the brief. Zero-history walk-ins drop confidence and route to nurse review rather than inventing a story.

**Free text:** an optional LLM parses nurse notes into structured fields. Identifiers are kept out of model prompts where possible; if the model is off, slow, or wrong, a rule-based parser still runs so the desk never blocks on AI availability.

### 2.3 Decision model (hybrid, with uncertainty as a product feature)

We use a **hybrid** on purpose, because the brief asks teams to show when deterministic logic vs. an LLM is used and why.

| Layer | Role | Why |
| --- | --- | --- |
| **Gemini triage agent** | Primary reasoner. Chooses tools dynamically (vitals, history, shock index, trends, completeness, baseline peek, Watcher state). Forms priority 1–5, urgency, pathway, monitoring, confidence, evidence. May `REQUEST_INFORMATION` or `ESCALATE`. | Handles ambiguity and overlapping presentations better than a fixed rule tree alone, while staying constrained by tools and a hard iteration budget. |
| **Deterministic engine** | Age-banded vital interpretation, ESI-style baseline, time-critical clocks, safety-net escalation under low confidence. Exposed as a tool and used automatically if the agent fails, times out, or cannot complete. | Auditable reference, offline continuity, and evaluation partner. Never silently overwrites a valid agent recommendation. |
| **Uncertainty handling** | Confidence on every recommendation. Low confidence and material gaps → nurse route or "needs more information," not a quiet middle score. Budget exhaustion → await human, do not invent a priority. | Matches the brief's escalate-under-uncertainty requirement and makes the choice visible in the UI and audit trail. |
| **Evaluation store** | Agent vs baseline vs clinician agreement fields | Observes disagreement for governance. We do not train the agent to mimic the baseline as the objective. |

Safety invariants enforced in code and tests: danger-zone vitals for age can only push urgency up; the Watcher's monitoring floor only ratchets upward; clinician override and modify require a reason; the original agent recommendation is never overwritten by HITL.

### 2.4 Workflow design

**At the desk.** The live board lists waiting patients most urgent first, each with priority, confidence, flags, and any time-critical clock. Opening a patient shows evidence, gaps, baseline comparison, and clinician actions: Accept, Modify, Override, Request more information, Escalate. Modify and Override require a reason and a new priority.

**Intake.** A nurse can paste or type a short note (name, age, vitals, description), run parse-and-score, review the recommendation (or the insufficient-information gate), then optionally add the patient to the live board.

**Quiet shift vs surge.** A load toggle simulates normal volume vs **3× surge**. Under surge, arrivals multiply, the Watcher reallocates a fixed attention budget toward highest risk and most overdue patients, and the board shows rising unsafe-wait pressure. Same engine, different load: that is the rural-vs-trauma flex story in miniature.

**After the desk.** The Watcher keeps observing everyone still waiting. Meaningful deterioration or unsafe-wait events wake agent reassessment; stable ticks do not burn model calls. That is how we keep cost and alert noise down during a quiet night and still catch decline during a crunch.

### 2.5 Safety-first design (including waiting-room monitoring)

This is non-negotiable in the brief, so it is non-negotiable in the product.

1. **Escalate under uncertainty.** Low confidence with red flags or mid acuity escalates one level in the deterministic safety net; the agent is instructed to request information or escalate rather than guess. Sparse mild complaints without vitals refuse a trusted score.
2. **Waiting-room Watcher.** Monitors the queue with acuity-aware safe-wait limits, a limited re-check budget, simulated vitals drift for designated cases, and a timer backstop so saturation cannot hide a patient. Re-assessment triggers when wait exceeds the safe threshold for severity **or** when re-recorded vitals worsen.
3. **Upward-only ratchet.** Monitoring floor and Watcher logic never softens priority because the department got busy.
4. **Clinician always final.** Every completed recommendation enters awaiting-clinician. Agent and clinician decisions are stored separately for audit.
5. **Immutable trail.** Append-only audit records: who, what, when, why, before/after priority, model/engine version. No edit, no delete, no reasonless override.

### 2.6 What the prototype already demonstrates

Mapped to Round 2 minimum prototype expectations:

| Expectation | In Sentinel |
| --- | --- |
| Score 15–20+ simulated patients | Deterministic cohort of 25 (named edge cases + fillers); live board at `/` |
| Ambiguous, pediatric/geriatric, zero-history | Ambiguous elderly weakness; febrile toddler + atypical geriatric MI; Emma Jones walk-in with sparse vitals |
| Behaviour under 3× surge | Surge toggle + Watcher simulation report |
| No score without confidence | Confidence on every row and drill-down; low confidence routes to nurse; intake can abstain |
| Clinician override + what is logged | HITL form + Audit trail tab; reason required |
| Waiting-room deterioration monitoring | Watcher catches designated decline (e.g. UTI → sepsis drift) before a human would have looked again |
| Age-specific thresholds | `engine/thresholds.py`; UI shows vitals read against age band |
| Named jurisdiction + audit | HIPAA (US) assumed; append-only trail in `app/audit.py` |

---

## 3. Target users

**Primary user: triage nurse.** Needs a fast, explained starting point, a clear confidence signal, and a one-glance place to accept, adjust, or demand more information without fighting the UI.

**Charge nurse / flow coordinator.** Needs the board-level view under load: who is waiting, who is overdue, where surge pressure is building. The Watcher and surge mode are built for them.

**Emergency physician.** Needs a defensible rationale, running time-critical clocks, and a clean record of any change and why.

**Economic buyers:** Chief Nursing Officer and Chief Medical Officer (safety, mistriage, liability), hospital operations (throughput, leave-without-being-seen), CIO/CISO (integration, security, maintainability).

**Patients** are the indirect beneficiary, never a direct user. The point is that the sick are seen sooner and the waiting are not forgotten.

---

## 4. Business case and impact

Assumed setting: US emergency departments, **about 100 to 500+ visits per day**, five-level severity (ESI-style), mixed prior-record availability (~50/50), **HIPAA** as the regulatory frame. Figures below are illustrative and tied to those assumptions.

**Safety impact (primary).** Under-triage of high-acuity patients is a recurring failure in triage reliability studies. Sentinel attacks it with age-aware vitals, escalate-on-uncertainty, insufficient-information gates, and continuous waiting-room monitoring. Even a small reduction in missed critical cases dominates the cost of the system for a CMO.

**Throughput impact.** Leave-without-being-seen rises when the department is crowded and triage is slow. For an illustrative mid-size site (~40,000 visits/year), cutting LWBS by one percentage point is on the order of hundreds of patients a year who stay and receive care. Faster, more consistent first pass plus active re-check of the queue both push that way.

**Time-critical impact.** Running clocks for stroke, STEMI, and sepsis, plus escalate-when-unsure behavior, are meant to surface treatable windows earlier. Outcome literature on door-to-needle / door-to-balloon / sepsis bundles makes minutes matter; we do not invent site-specific minute savings here.

**Cost to run.** The expensive piece (Gemini) is optional, cached, iteration-capped, and skipped on quiet Watcher ticks. Deterministic fallback keeps the desk alive offline. Cost per patient with the model on is a fraction of a cent at flash-lite rates; with the model off it is zero AI spend.

**Commercial model.** Per-department annual SaaS, tiered by annual visit volume (illustrative: tens of thousands USD/year for a small ED up to low six figures for a large trauma center), plus a one-time integration engagement. Pilots priced low or free in exchange for validation data and a reference site: the first credible outcomes study is worth more than year-one revenue.

**Market shape.** Thousands of EDs in the US alone, 100M+ visits/year, crowding as a standing operational and reputational pressure. Near-term serviceable market: departments with enough digital maturity for read-only integration. Obtainable market at first: a handful of shadow pilots whose measured outcomes become the evidence base.

---

## 5. Adoption and change management

Triage tools usually die in the break room, not in the architecture review. We treat adoption as product work.

**Shadow mode first.** Weeks of scoring beside real triage without changing decisions. Leadership gets a local validation record; nurses see agreement (and honest disagreement) before the tool is in the critical path.

**Clinician in charge, always.** Accept / modify / override with a required reason. Framed as the clinician teaching the system, not being graded by it.

**Fight alert fatigue.** Risk-weighted Watcher budget, reasons on every escalation, confidence at a glance, and abstain-when-sparse instead of inventing drama on thin notes.

**Fit the existing two minutes.** Recommendation where triage already happens; override in a few seconds; if it adds clicks, it loses.

**Make catches visible.** When the Watcher escalates a deteriorating waiting patient, that story is the strongest adoption asset in the building.

---

## 6. Patient data protection and compliance

**Jurisdiction assumed: HIPAA (United States).** That choice drives audit design, retention, consent posture, and what a clinician override must record (who, what, when, why, against what was recommended). An EU deployment would remap the same architecture onto GDPR plus national health law; the control pattern (minimum necessary, role-based access, immutable trail, BAA or on-prem model) stays.

**Controls in the design:** encrypt in transit and at rest in a real deployment; role-based access; minimum-necessary fields to the model; no inventing of patient data by the agent; append-only audit with mandatory reasons for override/modify; de-identified or synthetic data in this competition prototype.

**Model posture:** preferred path is a BAA-covered Gemini (or equivalent) provider, or a private deployment of the same agent pattern. Sites that refuse external LLM calls run deterministic fallback and lose none of the age-banded clinical reference logic.

**Regulatory stance:** positioned as Non-Device Clinical Decision Support under FDA final guidance intent: displays and analyzes information, recommends rather than commands, and lets the clinician independently review the basis (evidence, gaps, baseline comparison). We would confirm classification with counsel before any clinical deployment and do not claim clearance today.

---

## 7. Scalability

Reference range from the brief: departments from ~100 to 500+ visits per day, different specialty mix and technical maturity.

**Same core, different config.** Age bands, safe-wait tables, staffing/slots, and surge multipliers are configuration, not forks. A rural ED and an urban trauma center share the engine and differ in thresholds and capacity.

**Compute shape.** Deterministic path is light enough for edge or hospital VM. Agent calls scale independently, are cached, and are gated (board live-agent load is opt-in so a demo or a frugal site does not fire N model calls on every refresh).

**Integration sequence.** Read-only shadow on standard health-data interfaces first (ADT / vitals / census), prove value, then deeper write-back. The prototype stays useful on thin data so immature sites are not blocked on perfect EHR wiring.

**Learning later, carefully.** Multi-site feedback on overrides and outcomes can tune thresholds with expert validation. Autonomy only rises as measured site accuracy earns it. No silent cross-site PHI leakage. Full federated learning is roadmap, not a claim of the current prototype.

---

## 8. Phased roadmap

**Phase 0 (now): Working prototype.** Agent-first assessment, deterministic baseline/fallback/evaluation, HITL, immutable audit, age-banded engine, synthetic cohort with required edge cases, Watcher + 3× surge, optional Gemini. Simulated data only. **Done in this repo.**

**Phase 1: Shadow pilot at one ED.** Read-only integration, no decision change, measure agreement, mistriage proxies, and Watcher catches that would have fired. Deliverable: local validation pack for clinical governance.

**Phase 2: Supervised live.** Recommendation in the live workflow with clinician firmly in control; tune thresholds to the site; waiting-room monitoring on real re-checks. Deliverable: safe everyday use at one site with outcome logging.

**Phase 3: Multi-site + governed learning.** Roll configurable engine across size/specialty mix; feedback loop on overrides and outcomes with human validation; graduated autonomy (hospital dial) that can always be turned down.

**Phase 4: Evidence moat.** Published or shared outcomes become the sales engine. Each new hospital is easier because the last one has data behind it.

---

## 9. Key risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Wrong recommendation contributes to harm | Escalate under uncertainty; abstain on sparse mild cases; clinician always final; shadow mode before live; agent and clinician records kept separate |
| Agent "sounds sure" on thin data | Needs-more gate; confidence bands; tool-grounded evidence; iteration budget → await human; deterministic fallback on failure |
| Treated as a medical device / blocked path | Design for transparent CDS; counsel before clinical use; never market as diagnosis or autonomous care |
| Alert fatigue → workaround | Risk-weighted Watcher budget; reason on every alert; surge vs quiet behavior differs by design |
| Privacy / unauthorized use | HIPAA controls; minimize PHI to models; BAA or on-prem; model-off mode; immutable access-relevant audit |
| Integration stalls the pilot | Read-only shadow first; standard interfaces; engine useful on incomplete intake |
| Bias / inequity across age or demographics | Age-banding as baseline fairness move; pilot measurement across groups before wider roll-out |
| Adoption failure | Shadow mode, clinician control, visible Watcher catches, workflow fit |

---

## 10. Assumptions (stated clearly)

- Competition prototype on **simulated, de-identified** data; not for clinical use without validation and governance.
- Triage framework: **five-level ESI-style** severity.
- Department size reference: **~100–500+ visits/day**.
- Prior records available for **~half** of arrivals.
- Jurisdiction: **HIPAA (US)**.
- Thresholds adapted from standard references (ESI danger-zone vitals, age-banded HR/RR/BP, SIPA-style shock index); **configurable per hospital**, illustrative in this build.
- Financial figures are directional illustrations, not quotes for a named hospital.
- Gemini is optional; the desk must function with deterministic fallback alone.

---

## 11. The ask

To move from a working prototype to a validated tool we need **one partner emergency department** willing to run Sentinel in shadow mode, **read-only access** to records and flow data over standard interfaces, and a **clinical champion** to help tune thresholds and interpret disagreement. In return the site gets an early, safe view of its own mistriage and crowding patterns, and a real say in how the assistant behaves.

We built a triage assistant that is safe when it is unsure, keeps watching the people who are waiting, uses an agent where language and ambiguity need judgment and a deterministic engine where auditability and fallback matter, and always leaves the decision with the clinician. That is the product Round 2 asked us to prove.
