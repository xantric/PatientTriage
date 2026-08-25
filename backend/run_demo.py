"""Run the Sentinel engine over the full simulated cohort and print a summary.

Usage (from sentinel/backend):
    python run_demo.py
"""

from __future__ import annotations

from app.data.generator import build_cohort
from app.engine.pipeline import triage_cohort


def main() -> None:
    cohort = build_cohort()
    results = triage_cohort(cohort)

    # Sort by acuity (most urgent first), then by arrival for a stable board.
    results.sort(key=lambda r: (r.adjudicator.acuity, r.patient.arrival_epoch_min))

    print(f"\nSENTINEL TRIAGE BOARD  ({len(results)} patients)\n" + "=" * 92)
    header = f"{'ID':7} {'ESI':3} {'conf':6} {'exp':3} {'flags':5} {'route':6}  {'complaint':40}"
    print(header)
    print("-" * 92)
    mismatches = 0
    for r in results:
        a = r.adjudicator
        i = r.interpreter
        exp = r.patient.expected_acuity
        mark = ""
        if exp is not None and a.acuity != exp:
            # Only flag UNDER-triage (worse) loudly; over-triage is acceptable.
            mark = " UNDER" if a.acuity > exp else " over"
            mismatches += 1
        route = "NURSE" if a.routed_to_nurse else ""
        print(
            f"{r.patient.patient_id:7} {a.acuity:<3} {i.confidence:<6} "
            f"{str(exp):3} {len(i.red_flags):<5} {route:6}  {r.patient.chief_complaint[:40]:40}{mark}"
        )

    print("-" * 92)
    print(f"label mismatches: {mismatches} (over-triage is safe; watch for UNDER)\n")

    # Detail view for the safety-critical named cases.
    print("KEY CASE DETAIL\n" + "=" * 92)
    for r in results:
        if not r.patient.patient_id.startswith("P-0") or r.patient.patient_id > "P-010":
            continue
        a, i = r.adjudicator, r.interpreter
        print(f"\n{r.patient.patient_id}  {r.patient.display_name}  (age {r.patient.age_years}, {i.age_band})")
        print(f"  complaint : {r.patient.chief_complaint}")
        print(f"  ESI       : {a.acuity}  (provisional {a.provisional_acuity}"
              f"{', escalated' if a.escalated_for_uncertainty else ''})  -> {a.placement}")
        print(f"  confidence: {i.confidence} ({i.confidence_band.value})"
              f"{'  ROUTED TO NURSE' if a.routed_to_nurse else ''}")
        if i.shock_index is not None:
            print(f"  shock idx : {i.shock_index} ({i.shock_index_flag})")
        if a.time_critical_clocks:
            for c in a.time_critical_clocks:
                rem = "unknown" if c.minutes_remaining is None else f"{c.minutes_remaining} min left"
                print(f"  clock     : {c.name} (window {c.window_minutes} min, {rem})")
        print(f"  drivers   : {', '.join(a.top_drivers)}")
        print(f"  if ignored: {a.what_if_ignored}")
        if i.data_gaps:
            print(f"  data gaps : {', '.join(i.data_gaps)}")


if __name__ == "__main__":
    main()
