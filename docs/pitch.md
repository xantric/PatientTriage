# Sentinel: pitch

Team Sentinel. Track 2, PatientTriage.ai.

This is the script for the Round 2 pitch. It is written to be delivered in about eight to ten minutes with the prototype running live, and it doubles as the content spec for the slide deck. Each slide has a headline, the few words that go on screen, what to say, and a demo cue where the story moves to the running app. Keep the slides sparse and let the live product do the talking.

Running order at a glance: hook, why it is hard, our stance, the system, then four short live moments (the board, the hard cases, the surge and the catch, the override), then the model, safety and compliance, the business case, the roadmap, and the ask. Four slides carry the whole demo, so rehearse the transitions to the app until they are boring.

---

## Slide 1: Title

On screen: Sentinel. A safety-first triage assistant for the emergency department. Team Sentinel, Track 2.

Say: "We built Sentinel for the two minutes at the front of an emergency department where a nurse has to decide who is sick, with bad information, while more patients keep arriving. Everything you are about to see is running live."

---

## Slide 2: The hook

On screen: one line. "Missing a critical patient is far worse than being cautious with a minor one." Under it: the department has seconds, not minutes.

Say: "A triage nurse is often assessing one patient while three more walk in. Vague complaint, maybe incomplete vitals, sometimes no history at all, and a decision in under two minutes. And the cost of that decision is lopsided. Over-triage wastes a little capacity. Under-triage can kill someone. So the goal cannot be to be accurate on average. It has to be safe when it is unsure. That one idea is the whole product."

---

## Slide 3: Why it is genuinely hard

On screen: three forces. Age changes the meaning of a number. Data is half-missing. The waiting room is where people deteriorate.

Say: "Three things make this hard and none of them go away with more staff. First, the same number means different things at different ages. A heart rate of 150 is an emergency in a calm adult and normal in an upset toddler. A flat adult rule set hides that, and the danger is silent. Second, about half of arrivals have a real history on file and half are strangers, so any tool that assumes rich data fails worst on the patients it knows least. Third, triage is not a one-time score. People sit in the waiting room and get worse while no one is looking, and that happens most when the department is busiest."

---

## Slide 4: Our stance

On screen: The engine decides. The model explains. Uncertainty is a feature.

Say: "So we made three deliberate choices. Deterministic clinical logic sets the score, so a clinician can audit any decision in seconds. A language model is used only at the edges, to read free text and to write a plain explanation, and it never sets the number. And uncertainty is a first-class output: every score comes with a confidence band, and low confidence goes to a nurse instead of clearing the patient. A triage tool that is quietly wrong is more dangerous than one that admits it does not know."

---

## Slide 5: The system, three agents

On screen: a simple diagram. Interpreter, then Adjudicator, then Watcher. One line each.

Say: "Three agents. The Interpreter structures the messy intake and reads every vital against the patient's own age band. The Adjudicator assigns the five-level acuity, runs the time-critical clocks for stroke, heart attack, and sepsis, and writes out why. Danger-zone vitals can only push urgency up, and uncertainty escalates. The Watcher is the part most tools skip: it keeps watching everyone still waiting, re-checks the sick often and the stable rarely, and ratchets a patient up the moment they worsen or wait too long. Let me show you."

Demo cue: switch to the running board. Leave this slide and do not come back to slides until slide 10.

---

## Slide 6: Live, the board and one patient

Demo, not a slide. On the board:

- "This is a full department, most urgent at the top. Every row has an acuity, a confidence bar, flags, and any running clock."
- Click a clear high-acuity patient. "Here is why. The drivers, the vitals read against this patient's age band, the time-critical clock counting down, and a plain-language read written by the model but checked against the engine's level. Nothing here is a black box."
- Point at the confidence band. "And it tells you how sure it is."

---

## Slide 7: Live, the cases that break flat rules

Demo. Open, in turn:

- The geriatric heart attack that does not present with classic chest pain. "An adult-calibrated tool can miss this. We catch it and start the clock."
- The febrile toddler. "Adult thresholds would either panic or under-call this. Age-banding lands it correctly."
- The near-zero-data walk-in. "Almost nothing on file, so confidence drops and it routes to a nurse. It does not guess and move on."

Say while clicking: "These are the exact cases the brief asks for, and they are the ones where being age-aware and honest about uncertainty actually saves someone."

---

## Slide 8: Live, the surge and the catch

Demo. Flip to three-times surge, then open the Watcher simulation:

- "Same engine, three times the arrivals. The board fills and the Watcher reallocates its attention to the highest risk. Watch the longest unsafe wait climb, which is the pressure a real department feels."
- Point to the deterioration story. "This patient arrived looking like a stable infection. While waiting, their vitals drift, the Watcher re-checks, and it escalates them before a human would have looked again. That catch is the single most persuasive thing about this product."
- Point to the invariant. "And the Watcher never lowers an acuity. That is enforced and tested."

---

## Slide 9: Live, the override and the audit trail

Demo. On a patient, record an override:

- "The clinician always has the final say, in both directions. I change the level and I have to give a reason."
- Show the audit tab. "It is written to an append-only trail: the engine's original score, the new one, who, why, when, and the engine version. It cannot be edited or deleted, and it cannot be saved without a reason. That is what a clinician override has to record under HIPAA, and it is what makes the whole thing defensible."

---

## Slide 10: The model, used on purpose

On screen: free text in, structured fields and a plain explanation out. Telemetry: calls, latency, tokens, cost. "Never sets the acuity."

Say: "Back to the model for a second, because how you use it matters. It does two narrow jobs: it reads a free-text note into fields the engine can score, and it writes the two-sentence explanation. It runs at temperature zero, it is cached, it has a hard timeout, and any explanation that names the wrong level is thrown out. If it is slow, wrong, or simply switched off, a deterministic parser and a template take over and the app loses nothing. And every call is timed and costed on this telemetry panel: fractions of a cent per patient, or zero with the model off."

Demo cue, optional: paste a free-text note, show it parse and score and explain, then show the telemetry tick up.

---

## Slide 11: Safe by construction, and compliant by design

On screen: Escalate on uncertainty. Clinician in control. Immutable audit. HIPAA. Non-device CDS.

Say: "Safety is not a setting here, it is the architecture. It escalates when unsure, the clinician is always in charge, and every decision is auditable. On regulation, we designed to fit the FDA's Non-Device Clinical Decision Support category: it recommends rather than commands, and it lets a clinician independently review the basis, which is exactly why the reasoning is transparent. On privacy, the model never sees identifiers, it runs under a business agreement or on-premises, and a strict site can turn it off entirely and keep every bit of the clinical logic."

---

## Slide 12: The business case

On screen: three levers. Fewer missed critical cases. Fewer patients who leave without being seen. Faster time-critical recognition. Pricing: per-department SaaS, tiered by size.

Say: "The value is safety first and throughput second. The safety lever is the biggest: under-triage is a known, expensive failure, and even a small reduction in missed critical cases is worth more than the whole system. The throughput lever is easier to count: for a mid-size department, cutting the leave-without-being-seen rate by a single point is hundreds of patients a year who stay and get care. It is cheap to run because the core is deterministic and the model is optional. We sell it as a per-department subscription tiered by size, and we would price a pilot low in exchange for the first outcomes study, because that study is worth more than the first year of revenue."

---

## Slide 13: Roadmap

On screen: Prototype (done). Shadow pilot. Supervised live. Multi-site and learning loop.

Say: "We go outward from safe and small. The prototype is done, on simulated data. Next is a shadow-mode pilot at one department: read-only, changes nothing, measures agreement and the catches it would have made, and gives leadership a local validation record. Then supervised live use with the clinician in control. Then multi-site, with a feedback loop that improves the thresholds over time and autonomy that only rises as measured accuracy earns it. One strong outcomes study unlocks the rest."

---

## Slide 14: The ask and the close

On screen: One partner department. Read-only data access. A clinical champion.

Say: "To turn this into a validated tool we need one emergency department willing to run Sentinel in shadow mode, read-only access to its data over standard interfaces, and a clinical champion. In return they get an early, safe look at their own mistriage and crowding, and a say in how it works. We built a triage assistant that is safe when it is unsure, keeps watching the people who are waiting, and always leaves the decision with the clinician. Thank you."

---

## Delivery notes

Keep it to eight to ten minutes and spend more than half of it in the live app, not on slides. Rehearse the four demo transitions until they are smooth, because a fumbled surge toggle costs more than a weak sentence. Have the recorded backup video ready in case the room's network or the machine misbehaves; the data is deterministic, so the recording and the live run tell the identical story. If you are tight on time, the two moments you never cut are the waiting-room catch on slide 8 and the override and audit on slide 9. Those two are what separate this from a scoring script.
