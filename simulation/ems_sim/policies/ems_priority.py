"""EMS signal preemption - the counterfactual condition.

Stub.

The policy: detect the ambulance approaching a controlled junction and hold or
bring forward a green phase on its approach, clearing the queue ahead of it.

Design questions to settle when implementing, because each one materially
changes the result and each therefore has to be recorded as part of the policy
configuration rather than buried in code:

* **Detection range.** How far ahead is the ambulance detected? Real preemption
  systems use fixed detector placement; a policy that reads the ambulance's exact
  position from the simulator has information no real deployment would have, and
  will overstate the achievable benefit.
* **Clearance time.** Real signals cannot switch instantly — pedestrian clearance
  and inter-green intervals apply. Ignoring them makes preemption look better
  than any real system could be.
* **Recovery.** After the ambulance passes, the junction has to return to its
  plan. The cross-traffic delay caused by preemption is a genuine cost of the
  policy and must appear in the results, not just the ambulance's saving.
* **Scope.** Which junctions participate — only those on the route, or
  neighbours too?

The credibility of the headline number rests on these being conservative and
stated. An implementation that grants the ambulance perfect foresight and instant
switching will produce a large, impressive, and unusable figure.
"""

from __future__ import annotations


class EmsPreemptionPolicy:
    """Grants signal priority along the ambulance's route."""

    name = "ems_preemption"

    def __init__(self) -> None:
        raise NotImplementedError("Requires a built SUMO network. See docs/ROADMAP.md.")
