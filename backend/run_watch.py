"""Run the Watcher over the waiting room, normal load vs a 3x surge.

Usage (from sentinel/backend):
    python run_watch.py
"""

from __future__ import annotations

from app.engine.watcher import simulate
from app.models import SimReport


def _story_for(report: SimReport, patient_id: str) -> None:
    """Print the timeline of one patient across the run."""

    line = [e for e in report.events if e.patient_id == patient_id]
    if not line:
        return
    print(f"\n  {patient_id} timeline ({report.label}):")
    for e in line:
        stamp = f"t+{e.time_min:>3}m"
        if e.kind == "ratchet":
            print(f"    {stamp}  RATCHET  ESI {e.before_acuity} -> {e.after_acuity}  ({e.detail})")
        elif e.kind == "backstop":
            print(f"    {stamp}  ALERT    {e.detail}")
        else:
            print(f"    {stamp}  {e.kind:<14} {e.detail}")


def _summary(report: SimReport) -> None:
    print(f"\n{report.label.upper()}  (surge x{report.surge_factor})")
    print("-" * 64)
    print(f"  patients seen        : {report.total_patients}")
    print(f"  simulation ticks     : {report.ticks} (x5 min)")
    print(f"  peak waiting-room load: {report.peak_waiting}")
    print(f"  longest unsafe wait  : {report.peak_unsafe_wait_min} min")
    print(f"  deterioration ratchets: {report.total_ratchets}")
    print(f"  timer-backstop alerts : {report.total_backstops}")
    print(f"  acuity down-grades    : {report.down_ratchets}  (must be 0)")


def main() -> None:
    normal = simulate(surge_factor=1)
    surge = simulate(surge_factor=3)

    print("=" * 64)
    print("SENTINEL WATCHER: WAITING-ROOM SIMULATION")
    print("=" * 64)

    _summary(normal)
    _summary(surge)

    print("\n" + "=" * 64)
    print("DETERIORATION CAUGHT IN THE WAITING ROOM")
    print("=" * 64)
    print("P-011 arrives looking like a stable ESI-3 urinary infection.")
    _story_for(normal, "P-011")

    print("\n" + "=" * 64)
    print("WHAT THE SURGE DOES TO SAFETY")
    print("=" * 64)
    print("Same engine, 3x the arrivals. The Watcher's budget saturates, so")
    print("patients start breaching their safe wait and the backstop fires.")
    surge_backstops = [e for e in surge.events if e.kind == "backstop"][:6]
    for e in surge_backstops:
        print(f"    t+{e.time_min:>3}m  {e.patient_id}  {e.detail}")


if __name__ == "__main__":
    main()
