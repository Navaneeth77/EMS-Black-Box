"""Did the queue actually discharge?

The story a priority policy tells is: a queue is standing at the signal, priority
is granted, the queue discharges, the ambulance goes through. Three of those four
are recorded elsewhere — the request, the signal state, the ambulance's position.
The queue was not, and "the ambulance moved, so the queue must have cleared" is
an inference, not a measurement: an ambulance can also get through because it
arrived on a green with nothing in front of it, which would look identical in
every other record.

So the queue is counted, from SUMO's own halting count on the approach lanes of
each signal, and reported at the four moments that make the claim checkable:
when priority was asked for, when the signal answered, when the ambulance got
there, and after it had gone. If the queue at the third moment is not smaller
than at the second, the corridor did not clear ahead of the ambulance whatever
else happened.

Nothing here estimates a queue. A sample is ``getLastStepHaltingNumber`` summed
over the approach's lanes at a recorded time; where there is no sample near a
moment, the answer is ``None``.
"""

from __future__ import annotations

from typing import Any

CLEARED_FRACTION = 0.25
"""How much of the queue must be gone for it to count as discharged.

A queue rarely reaches zero on a busy approach — vehicles keep arriving behind
the ones leaving — so "cleared" cannot mean "empty". Three quarters gone is a
threshold, and an arbitrary one; it is reported alongside the raw counts so a
reader can apply their own.
"""

SAMPLE_TOLERANCE_S = 6.0
"""How far from a moment a sample may be and still be called that moment's.

The queue is sampled every few seconds, so an event almost never lands on a
sample. Beyond this, the answer is ``None`` rather than the nearest number.
"""

AFTER_PASSAGE_S = 30.0
"""How long after the ambulance passes the "after" sample is taken."""


def _at(series: list[list[float]], when: float | None) -> dict[str, Any] | None:
    """The sample nearest ``when``, or None if there is none close enough."""
    if when is None or not series:
        return None
    nearest = min(series, key=lambda row: abs(row[0] - when))
    if abs(nearest[0] - when) > SAMPLE_TOLERANCE_S:
        return None
    return {
        "sim_time_s": nearest[0],
        "halting": int(nearest[1]),
        "vehicles_on_approach": int(nearest[2]) if len(nearest) > 2 else None,
    }


def _peak(series: list[list[float]], start: float | None, end: float | None) -> int | None:
    if start is None or end is None:
        return None
    window = [row for row in series if start <= row[0] <= end]
    return int(max(row[1] for row in window)) if window else None


def clearance_for_encounter(
    timeline: dict[str, Any],
    series: list[list[float]],
    *,
    cleared_fraction: float = CLEARED_FRACTION,
) -> dict[str, Any]:
    """Queue metrics for one encounter between the ambulance and a signal."""
    requested = timeline.get("requested_at_s")
    active = timeline.get("priority_active_at_s")
    cleared = timeline.get("ambulance_cleared_at_s")

    at_request = _at(series, requested)
    at_active = _at(series, active)
    at_arrival = _at(series, cleared)
    after = _at(series, cleared + AFTER_PASSAGE_S if cleared is not None else None)

    clearance_time = None
    if active is not None and at_active is not None and at_active["halting"] > 0:
        target = at_active["halting"] * cleared_fraction
        for row in series:
            if row[0] >= active and row[1] <= target:
                clearance_time = round(row[0] - active, 1)
                break

    discharged = None
    if at_active is not None and at_arrival is not None:
        discharged = at_active["halting"] - at_arrival["halting"]

    return {
        "tls_id": timeline.get("tls_id"),
        "encounter": timeline.get("key"),
        "approach_edge": timeline.get("approach_edge"),
        "queue_at_request": at_request,
        "queue_at_priority_active": at_active,
        "queue_when_ambulance_passed": at_arrival,
        "queue_after_passage": after,
        "peak_queue_before_request": _peak(
            series, (requested - 120.0) if requested is not None else None, requested
        ),
        "vehicles_discharged_between_green_and_arrival": discharged,
        "clearance_time_s": clearance_time,
        "cleared_fraction": cleared_fraction,
        "after_passage_offset_s": AFTER_PASSAGE_S,
        "samples": len(series),
        "source": "SUMO lane.getLastStepHaltingNumber on the signal's approach lanes",
    }


def queue_clearance_report(
    policy_report: dict[str, Any],
    queue_by_tls: dict[str, list[list[float]]],
    *,
    cleared_fraction: float = CLEARED_FRACTION,
) -> dict[str, Any]:
    """Per-encounter queue metrics for a whole run.

    For a control arm — which never requests anything — the per-moment figures
    are ``None`` and only the series length is reported. That is the honest
    answer: there was no priority, so there is no "queue when priority was
    granted", and a zero there would read as "no queue".
    """
    encounters = policy_report.get("encounter_timelines", [])
    rows = [
        clearance_for_encounter(
            timeline,
            queue_by_tls.get(timeline.get("tls_id", ""), []),
            cleared_fraction=cleared_fraction,
        )
        for timeline in encounters
    ]
    measured = [r for r in rows if r["queue_at_priority_active"] is not None]
    return {
        "encounters": rows,
        "encounters_with_priority": len(measured),
        "total_discharged_between_green_and_arrival": sum(
            r["vehicles_discharged_between_green_and_arrival"] or 0 for r in measured
        )
        or None,
        "method": (
            "Halting vehicles on each signal's approach lanes, sampled through the "
            "run and read at the moments the policy's own timeline names. Not "
            "inferred from the ambulance's movement."
        ),
        "cleared_fraction": cleared_fraction,
        "data_class": "SIMULATED_DATA",
    }
