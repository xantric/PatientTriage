# Sentinel: a safety-first triage assistant for the emergency department

Team Sentinel. Track 2, PatientTriage.ai. Accenture Innovation Challenge 2026.

Business proposal for Round 2. This document frames the problem, lays out the solution and who it serves, makes the business case, sets a phased roadmap, and is honest about the risks and how we defuse them. The working prototype that backs every claim here lives in this repository.

## The one-line version

Emergency departments have seconds to decide who is sick, using messy and incomplete information, and the cost of getting it wrong is not symmetric. Missing a critical patient is far worse than being cautious with a minor one. Sentinel is a decision-support layer that scores every arrival with a transparent, age-aware clinical engine, says out loud how confident it is, keeps watching the people who are still waiting, and hands the final call to a clinician with a full audit trail. It is built to be safe when it is unsure, not to win on average accuracy.

## The problem, framed honestly

A nurse at the front of an emergency department is often triaging one patient while three more arrive. They have a chief complaint that may be vague, a set of vitals that may be incomplete, and sometimes no prior record at all. From that they have to assign an urgency level, decide where the patient goes, and move on, all in under a couple of minutes. This is hard for reasons that do not go away with more staff or a better paper form.

The same numbers mean different things at different ages. A heart rate of 150 is an emergency in a calm adult and roughly normal in a distressed toddler. A fever of 38.5 degrees is read very differently in a three-year-old than in a seventy-five-year-old. A single adult-calibrated rule set applied to everyone hides real danger, and that danger is silent: nothing on the screen tells you the rule was wrong for this patient.

The costs are asymmetric. Over-triage wastes a bit of capacity. Under-triage can kill someone. Sepsis mortality climbs with every hour antibiotics are delayed. Stroke and heart attack have hard treatment windows measured in minutes. A patient sent to the waiting room as "not urgent" can deteriorate while no one is looking, and crowded departments are exactly where that happens most.

Data is uneven. Roughly half of arrivals have a useful history on file and half are effectively strangers. A model that quietly assumes rich data will behave worst on the patients it knows least about, which is the wrong failure mode for a safety tool.

And trust is fragile. Staff are fatigued and time-pressured. A tool that cries wolf gets ignored inside a week. A tool that acts like an oracle and hides its reasoning gets overridden and distrusted. Either way it fails, not because the math was wrong but because no one used it.

So the real problem is not "predict acuity accurately." It is "be genuinely useful and genuinely safe under time pressure and bad data, and earn a fatigued clinician's trust." That reframing drives every design choice below.

## Who this is for

The daily users are the people at the front of the department.

The triage nurse is the primary user. Sentinel gives them a fast, explained starting point and a place to record what they saw that the engine did not. It never takes the decision away from them.

The charge nurse and flow coordinator get the board-level view: who is waiting, who is overdue for a re-check, where the pressure is building during a surge. The waiting-room watcher is really built for them.

The emergency physician gets a defensible, auditable rationale for each acuity, the running time-critical clocks, and a clear record of any override and why it was made.

The economic buyers are hospital leadership. The Chief Nursing Officer and Chief Medical Officer care about patient safety, mistriage rates, and the medico-legal exposure that comes with them. Hospital operations care about throughput, wait times, and patients who leave without being seen. The CIO and CISO care about integration, data protection, and whether this is one more thing to secure and maintain.

Patients are the indirect beneficiary and never a direct user. The whole point is that the sick ones get seen sooner and the waiting ones are not forgotten.

## The solution

Sentinel is three cooperating agents around one firm principle: deterministic clinical logic sets the score, and a language model is used only at the edges, for reading free text and writing a plain-language explanation. The model never sets the acuity. That single line is what makes the system auditable and what keeps it on the safe side of medical-device regulation.

**The Interpreter** takes messy intake, a complaint, whatever vitals exist, arrival mode, history, and turns it into structured findings. It maps every vital against age-banded danger zones, computes derived signals like the shock index, notes what is missing, and produces a confidence score that reflects how much it actually had to work with. Sparse data lowers confidence rather than being papered over.

**The Adjudicator** assigns the urgency level on a recognized five-level scale, runs the time-critical clocks for stroke, heart attack, and sepsis, sets a monitoring tier, and writes out the top drivers, a short "what happens if this is ignored" note, and a confidence band. It is pure deterministic scoring, so a clinician can audit any decision in seconds. Two rules make it safe by construction: danger-zone vitals for a patient's age can only push urgency up, and genuine uncertainty escalates rather than settling on a comfortable middle score.

**The Watcher** is the part most triage tools miss. It keeps watching everyone who is still in the waiting room. It spends a limited attention budget by risk, checking the sick often and the stable rarely, re-records their vitals, re-runs triage, and ratchets a patient up (never down) the moment they worsen or pass the maximum safe wait for their level. A timer backstop raises an alert on anyone overdue even when the department is saturated, so a busy queue can never quietly hide a deteriorating patient.

Uncertainty is a first-class output, not a footnote. Every score ships with a confidence band. Low confidence does not auto-clear a patient; it routes them to a nurse. We treat this as the product's spine, because a triage tool that is quietly wrong is more dangerous than one that admits it does not know.

The clinician always has the final say, in both directions, and nothing changes silently. An override writes an append-only audit record with the engine's original score, the new score, who changed it, why, a timestamp, and the engine version that produced the call. The record cannot be edited or deleted, and it cannot be written without a reason.

The language model earns its keep in two narrow places. It reads a nurse's free-text note into structured fields so the engine can score it, and it writes a two-sentence plain-language explanation of the decision. Both paths are guarded: the model runs at temperature zero with a cache and a hard timeout, any explanation that names a different urgency level than the engine chose is rejected, and if the model is slow, wrong, or simply not configured, a deterministic rule-based parser and a template explanation take over. The app runs completely offline with no model at all. That is not a fallback we bolted on; it is the default.

One engine flexes across very different hospitals because thresholds, staffing, and re-check intervals are configuration, not code. The same core runs a small rural department and a large urban trauma center by changing a config, not by forking the product.

## What the prototype already proves

The prototype is not a slide. It runs, and it demonstrates every item on the Round 2 checklist for this track.

It scores a cohort of simulated patients into the five levels, well past the fifteen-to-twenty minimum, each with drivers and a confidence band. The cohort deliberately includes the hard cases the brief asks for: an atypical geriatric heart attack that does not present with classic chest pain, a febrile toddler where adult thresholds would mislead, an ambiguous elderly weakness, and a walk-in with almost no data on file.

It behaves under a simulated three-times surge. Flip the load and the board fills, the Watcher reallocates its budget to the highest risk, and the longest unsafe wait grows, which is exactly the pressure a real department feels and exactly what leadership needs to see modeled.

It never returns a score without a confidence indicator, and it routes low-confidence cases to a nurse instead of clearing them.

It captures a clinician override and shows precisely what it logs, in an audit trail that callers cannot mutate.

It catches a waiting-room deterioration: one seemingly stable patient worsens while waiting, and the Watcher escalates them before a human would have looked again.

A test suite guards the safety properties directly. The tests assert that no named case is under-triaged, that the Watcher never lowers an acuity, that an override needs a reason, and that the audit trail is immutable. We test for safety, not just for correctness.

## The business case and impact

The value is a mix of hard operational savings and softer but larger safety and liability upside. We are deliberate about which is which, and every number here is illustrative and tied to a stated assumption, not a claim about a specific hospital.

The safety lever is the biggest and the hardest to put a single number on. Under-triage of genuinely high-acuity patients is a recurring finding in studies of triage reliability, and each missed critical case carries both a human cost and a serious medico-legal one. Sentinel attacks this directly by biasing toward escalation, by being age-aware where flat rules fail, and by watching the queue for deterioration. Even a small reduction in missed critical cases is worth more than the entire cost of the system, which is the core of the pitch to a Chief Medical Officer.

The throughput lever is easier to quantify. Patients who leave without being seen are a direct loss of revenue and a safety risk, and their rate rises sharply when the department is crowded and triage is slow. For an illustrative mid-size department of forty thousand visits a year, moving the leave-without-being-seen rate down by even one percentage point is on the order of four hundred patients a year who stay and get care. Faster, more consistent triage and a queue that actively re-checks waiting patients both push in that direction.

The time-critical lever compounds the safety case. Shaving minutes off recognition for stroke, heart attack, and sepsis maps onto outcomes that are well established in the literature. Sentinel's running clocks and escalate-on-uncertainty behavior are built to surface these earlier.

On cost, the system is cheap to run precisely because the core is deterministic. The expensive part, the language model, is optional, cached, and used in two narrow places, so cost per patient is a fraction of a cent even with it turned on, and zero with it off. We can show that live in the telemetry panel.

The business model is a per-department annual SaaS subscription, tiered by size measured in annual visits, plus a one-time integration engagement. An illustrative range is on the order of tens of thousands of dollars a year for a small department up to low six figures for a large trauma center, which is small next to the cost of a single missed critical case or the ongoing cost of crowding. Multi-site health systems buy at the system level. We would price the pilot low or free in exchange for the validation data and a reference site, because the first credible outcomes study is worth more than the first year of revenue.

## Market opportunity

The addressable market is large and non-discretionary. The United States alone has on the order of five thousand emergency departments handling well over a hundred million visits a year, and crowding is a named, worsening problem that hospitals are under regulatory and reputational pressure to fix. Beyond the US, any system that uses a structured triage scale is a candidate, which covers most of the developed world and a growing share of everywhere else.

We think about it in three rings. The total market is every emergency department that triages patients. The serviceable market in the near term is departments in our starting jurisdiction with a modern enough record system to integrate, which is a large minority. The obtainable market at first is a handful of pilot sites whose outcomes become the evidence base for everyone after them. This is a market where one strong published outcome moves more deals than any amount of marketing.

## Getting a fatigued staff to actually use it

Adoption is where triage tools usually die, so we treat change management as part of the product, not an afterthought.

We start in shadow mode. For the first weeks Sentinel scores every patient but changes nothing; the department keeps triaging exactly as before. This does two things: it builds the local validation record that leadership needs, and it lets nurses see the tool agree with them again and again before it is ever in the critical path. Trust is earned by being right quietly first.

We keep the clinician in charge, always, in both directions, with a one-line reason. Nobody is being graded or overruled by a machine. The override is framed as the clinician teaching the system, and it goes into the record next to the engine's original call.

We fight alert fatigue on purpose. The Watcher spends a budget by risk instead of pinging constantly, the confidence band tells a nurse when to trust a score at a glance, and escalations come with the reason attached so they are actionable rather than noise. Over-flagging is a failure mode we design against, not a side effect we tolerate.

We fit the existing workflow. The recommendation shows up where triage already happens, the reasoning is one glance, and recording an override is a few seconds. If it adds clicks, it loses.

And we make the wins visible. When the Watcher catches a deteriorating patient in the waiting room, that is a story the whole department hears about, and it is the single most persuasive thing for adoption.

## Data protection, compliance, and jurisdiction

We assume HIPAA in the United States as the governing regime, and the design follows from it. A different jurisdiction, GDPR plus national health law in the EU for example, changes the specifics of consent and retention but not the shape of the architecture.

Patient data is handled on a minimum-necessary basis, encrypted in transit and at rest, and access is role-based so a user sees only what their role needs. The audit trail is append-only and immutable, retained per the hospital's stated policy, which is exactly what an override must legally record: who, what, when, why, and against what the engine originally said. Model development uses de-identified data.

The language model is the obvious privacy question, and our answer is built in. The model never receives patient identifiers, only structured clinical fields, and it never sets a decision. In a real deployment the model call would run against a provider covered by a Business Associate Agreement or an on-premises open model, so no protected health information leaves the hospital's control without a contract that permits it. Because the model is optional and cached, a site with a strict policy can run Sentinel entirely on the deterministic core with the model switched off and lose none of the clinical logic.

On medical-device regulation, we have a deliberate strategy rather than a hope. Sentinel is designed to fit the Non-Device Clinical Decision Support category under the FDA's final guidance: it displays and analyzes clinical information, it offers a recommendation rather than a specific directive command, and, critically, it lets the clinician independently review the basis for that recommendation instead of relying on it as a black box. The transparent drivers and rationale are not just a trust feature, they are the regulatory argument. We would confirm the specific classification with regulatory counsel before any clinical deployment, and we would not overstate it before then.

## Scalability

Scaling is mostly a configuration story, which is the point. The clinical thresholds, the staffing model, the re-check intervals, and the surge behavior are all config, so the same engine serves a hundred-visit rural department and a five-hundred-visit trauma center without a code change. The deterministic core is light enough to run at the edge inside a hospital, and the optional model layer scales independently because it is cached and used sparingly. Integration is the real work, and we sequence it deliberately in the roadmap below rather than pretending every hospital's record system looks the same.

## Phased roadmap

We build outward from a safe, small, honest demo toward a validated clinical tool, and we protect the timeline by freezing scope early and treating the fancy items as the first things to cut.

**Now, the prototype.** All three agents, the live board, the surge toggle, confidence on every score, override and audit, and the optional model layer, all working on simulated data. This is complete and is what backs this proposal.

**Next, a shadow-mode pilot at one department.** Integrate read-only with the hospital's record and patient-flow systems over standard health-data interfaces, run Sentinel alongside the real triage without touching decisions, and measure agreement, mistriage, and the cases the Watcher would have caught. The deliverable is a local validation record and a first outcomes signal.

**Then, supervised live use.** Turn the recommendation on in the workflow with the clinician firmly in control, keep measuring, and tune thresholds to the site. Add the deterioration monitoring into the live queue. The deliverable is safe, measured, everyday use at one site.

**After that, multi-site and the learning loop.** Roll the same configurable engine to departments of different size and specialty, and stand up a feedback loop where overrides and outcomes improve the thresholds over time, with expert validation in the loop and without any single site's data leaking to another. Autonomy stays graduated: the system earns a higher level of independence only as its measured accuracy at a site supports it, and a human can always dial it back.

**Longer term, the network effect.** With enough validated sites, the learning loop and the outcomes evidence become the moat. Each new hospital is easier to win because the last one has data behind it.

## Key risks and how we defuse them

**Clinical safety and liability.** The core risk is that a wrong score contributes to harm. We defuse it structurally: the engine biases to escalation, uncertainty routes to a human, the clinician always has the final say, and every decision and override is auditable. We never position Sentinel as a diagnosis or a replacement for judgment, and we start in shadow mode so it proves itself before it is ever in the critical path.

**Regulatory classification.** If a regulator treats it as a medical device, the path to deployment lengthens. We design to the Non-Device Clinical Decision Support criteria from the start, keep the reasoning transparent so a clinician can independently review it, and confirm classification with counsel before clinical use rather than assuming.

**Alert fatigue and workaround.** If it nags, staff route around it and it dies. We spend the Watcher's attention by risk, attach a reason to every escalation, show confidence at a glance, and treat over-flagging as a bug. Shadow mode also tunes the noise down before anyone has to act on it.

**Data privacy.** Patient data is sensitive and the model is an external dependency. We keep identifiers away from the model entirely, run it under a Business Associate Agreement or on-premises, encrypt everything, gate access by role, and let a strict site run with the model off and lose no clinical logic.

**Integration reality.** Hospital systems vary enormously and integration is where prototypes stall. We sequence it as read-only shadow integration first over standard interfaces, prove value before asking for deeper hooks, and keep the engine useful even where data is thin.

**Bias and equity.** A safety tool that is worse for some groups is unacceptable. Age-banding is the first step away from one-size-fits-all, and part of the pilot's job is to measure performance across demographics explicitly and tune for it, not to assume fairness.

**Adoption.** Covered above and worth repeating as a risk: the tool fails if it is not trusted. Shadow mode, clinician control, transparency, and visible catches are the answer.

## What sets Sentinel apart

Most triage tools do one of two things. Either they are a static scoring calculator that treats every age the same and stops caring the moment the patient sits down in the waiting room, or they are a black-box risk model that is accurate on average and impossible to audit in the two minutes a nurse actually has. Sentinel is neither. It is age-aware where flat rules are silently unsafe, it keeps watching the queue instead of scoring once and forgetting, it says how confident it is and escalates when it is not, it keeps the clinician in charge with a real audit trail, and it uses a language model only where a language model helps, never as the source of the decision. The combination, safe by construction and transparent enough to trust, is the differentiator.

## Assumptions

The data in the prototype is simulated and de-identified. We assume HIPAA as the jurisdiction and a five-level severity scale as the triage framework. Clinical thresholds are adapted from standard references and are configurable per hospital; they are illustrative and would be set with clinical governance at each site. Every financial figure in this proposal is illustrative and tied to a stated assumption. This is a competition prototype and a business proposal, not clinical advice or a regulatory filing.

## The ask

To take this from a working prototype to a validated tool, we need one partner emergency department willing to run Sentinel in shadow mode, read-only access to its patient records and flow data over standard interfaces, and a clinical champion to help tune thresholds and interpret the results. In return the site gets an early, safe view of its own mistriage and crowding, and a say in how the tool works. The first credible outcomes study is the thing that unlocks the rest of this roadmap, and that is what we are asking for the chance to build.
